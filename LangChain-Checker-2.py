import os
import sys
import time
import logging
import re
import requests
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import threading
from typing import List, Dict, Any, Optional, Tuple

# LangChain imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

# --- CONFIGURATION & INITIALIZATION ---

def load_configuration():
    """Loads configuration from the .env file."""
    load_dotenv()
    config = {
        "gemini_api_key": os.getenv("GEMINI_API_KEY"),
        "figma_api_token": os.getenv("FIGMA_API_TOKEN"),
        "figma_file_url": os.getenv("FIGMA_FILE_URL"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
        "processing_delay": int(os.getenv("PROCESSING_DELAY", 3)),
        "iteration_count": int(os.getenv("ITERATION_COUNT", 3)),
        "project_path": os.getenv("PROJECT_PATH"),
        "is_common_header_footer": os.getenv("IS_COMMAN_HEADER_FOOTER", "false").lower() == "true",
        "max_threads": int(os.getenv("MAX_THREADS", 4)),
    }
    # Validate essential configuration
    for key, value in config.items():
        if value is None and key in ["gemini_api_key", "figma_api_token", "figma_file_url"]:
            logging.error(f"Error: Missing required configuration in .env file: {key.upper()}")
            sys.exit(1)
    return config

def setup_logging(project_name):
    """Sets up a dedicated logger for the script's execution."""
    log_filename = f"generation_log_{project_name}.txt"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info(f"Logging initialized. Log file: {log_filename}")

def get_figma_file_key(figma_url):
    """Extracts the file key from a Figma URL."""
    try:
        path_segments = urlparse(figma_url).path.split('/')
        if 'file' in path_segments:
            file_segment_index = path_segments.index('file')
            return path_segments[file_segment_index + 1]
        elif 'design' in path_segments:
            design_segment_index = path_segments.index('design')
            return path_segments[design_segment_index + 1]
        else:
            raise ValueError("Neither 'file' nor 'design' segment found in URL path.")
    except (ValueError, IndexError) as e:
        logging.error(f"Could not parse Figma file key from URL: {figma_url}. Error: {e}")
        sys.exit(1)

# --- FIGMA API HELPERS ---

class FigmaAPI:
    """A wrapper for Figma API interactions."""
    BASE_URL = "https://api.figma.com/v1"

    def __init__(self, api_token):
        self.headers = {"X-Figma-Token": api_token}
        self._lock = Lock()

    def get_file_data(self, file_key):
        """Fetches the entire structure of a Figma file."""
        logging.info(f"Fetching Figma file structure for key: {file_key}")
        try:
            response = requests.get(f"{self.BASE_URL}/files/{file_key}", headers=self.headers)
            response.raise_for_status()
            logging.info("Successfully fetched Figma file data.")
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to fetch Figma file data: {e}")
            sys.exit(1)

    def get_node_image_url(self, file_key, node_id):
        """Gets a temporary download URL for an image of a specific node."""
        with self._lock:  # Thread-safe API calls
            logging.info(f"Requesting image URL for node: {node_id}")
            params = {'ids': node_id, 'format': 'png', 'scale': '2'}
            try:
                response = requests.get(f"{self.BASE_URL}/images/{file_key}", headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                if data.get('err') or not data.get('images') or not data['images'].get(node_id):
                    logging.error(f"Figma API returned an error for node image URL: {data.get('err')}")
                    return None
                return data['images'][node_id]
            except requests.exceptions.RequestException as e:
                logging.error(f"Failed to get image URL for node {node_id}: {e}")
                return None

    @staticmethod
    def download_image(url, save_path):
        """Downloads an image from a URL to a specified path."""
        logging.info(f"Downloading image from URL to {save_path}")
        try:
            # Ensure the directory for the save_path exists
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logging.info("Image downloaded successfully.")
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to download image: {e}")
            return False

# --- LANGCHAIN AI HELPER ---

class LangChainGeminiProcessor:
    """LangChain-based processor for Gemini AI interactions."""
    
    def __init__(self, api_key: str, model_name: str, processing_delay: int = 3):
        self.processing_delay = processing_delay
        self._lock = Lock()
        
        # Initialize LangChain Gemini model
        self.llm = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=0.1,
            max_tokens=None,
            timeout=None,
            max_retries=2,
        )
        
        # Create prompt template
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("human", "{prompt}")
        ])
        
        # Create chain
        self.chain = (
            self.prompt_template 
            | self.llm 
            | StrOutputParser()
        )
    
    def process_with_image(self, prompt: str, image_path: Path) -> str:
        """Process prompt with image using LangChain."""
        with self._lock:  # Thread-safe AI calls
            try:
                logging.info(f"Processing with LangChain Gemini - Thread: {threading.current_thread().name}")
                
                # Read image data
                image_data = image_path.read_bytes()
                
                # Create message with image
                message = HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{self._encode_image(image_data)}"
                            }
                        }
                    ]
                )
                
                # Process with chain
                response = self.llm.invoke([message])
                
                # Apply processing delay
                time.sleep(self.processing_delay)
                
                return response.content
                
            except Exception as e:
                logging.error(f"Error in LangChain processing: {e}")
                raise
    
    def _encode_image(self, image_data: bytes) -> str:
        """Encode image data to base64."""
        import base64
        return base64.b64encode(image_data).decode('utf-8')

# --- FILE & CODE HELPERS ---

def find_html_file_for_page(page_name, project_src_path):
    """Finds the corresponding HTML file in the src directory for a Figma page name."""
    page_name_lower = page_name.lower().replace(" ", "")

    # First, try to find a direct match based on the comment
    for file in project_src_path.glob("*.html"):
        try:
            content = file.read_text(encoding='utf-8')
            match = re.search(r"<!-- Figma Frame Name:\s*(.*?)\s*-->", content)
            if match:
                figma_frame_name_from_comment = match.group(1).strip().lower().replace(" ", "")
                if figma_frame_name_from_comment == page_name_lower:
                    logging.info(f"Matched HTML file '{file.name}' with Figma page '{page_name}' using comment.")
                    return file
        except Exception as e:
            logging.warning(f"Could not read HTML file '{file.name}' or parse comment: {e}")

    logging.warning(f"No corresponding HTML file found for Figma page: '{page_name}'")
    return None

def extract_section_html(html_content, section_name):
    """Extracts HTML content for a section defined by start/end comments."""
    start_comment = f"<!-- START: {section_name} -->"
    end_comment = f"<!-- END: {section_name} -->"
    
    pattern = re.compile(f"{re.escape(start_comment)}(.*?){re.escape(end_comment)}", re.DOTALL)
    match = pattern.search(html_content)
    
    if match:
        return match.group(1).strip()
    else:
        logging.warning(f"Could not find section markers for '{section_name}'. The section will be skipped.")
        return None

def replace_section_html(full_html, section_name, new_section_code):
    """Replaces the HTML content of a section with new code."""
    start_comment = f"<!-- START: {section_name} -->"
    end_comment = f"<!-- END: {section_name} -->"

    # Add newlines for proper formatting
    replacement_code = f"{start_comment}\n{new_section_code}\n{end_comment}"
    
    pattern = re.compile(f"{re.escape(start_comment)}.*?{re.escape(end_comment)}", re.DOTALL)
    
    if pattern.search(full_html):
        return pattern.sub(replacement_code, full_html)
    else:
        logging.error(f"Could not find section markers for '{section_name}' to replace content. This should not happen if extraction succeeded.")
        return full_html

def clean_gemini_response(response_text):
    """Extracts the pure HTML code from Gemini's markdown-formatted response."""
    match = re.search(r"```html\s*\n(.*?)\n\s*```", response_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback for responses without explicit markdown
    logging.warning("Gemini response was not in a standard markdown code block. Using the full response.")
    return response_text.strip()

# --- MULTI-THREADING TASK CLASSES ---

class ComponentTask:
    """Task for processing common components (Header/Footer)."""
    
    def __init__(self, comp_name: str, comp_node: Dict, comp_file_path: Path, 
                 figma_api: FigmaAPI, langchain_processor: LangChainGeminiProcessor,
                 figma_file_key: str, temp_img_dir: Path, config: Dict):
        self.comp_name = comp_name
        self.comp_node = comp_node
        self.comp_file_path = comp_file_path
        self.figma_api = figma_api
        self.langchain_processor = langchain_processor
        self.figma_file_key = figma_file_key
        self.temp_img_dir = temp_img_dir
        self.config = config

class SectionTask:
    """Task for processing page sections."""
    
    def __init__(self, section: Dict, page_name: str, full_html_content: str,
                 figma_api: FigmaAPI, langchain_processor: LangChainGeminiProcessor,
                 figma_file_key: str, temp_img_dir: Path, config: Dict):
        self.section = section
        self.page_name = page_name
        self.full_html_content = full_html_content
        self.figma_api = figma_api
        self.langchain_processor = langchain_processor
        self.figma_file_key = figma_file_key
        self.temp_img_dir = temp_img_dir
        self.config = config

# --- MULTI-THREADING PROCESSORS ---

def process_component_task(task: ComponentTask) -> Tuple[str, str]:
    """Process a single component task."""
    try:
        logging.info(f"Processing component: '{task.comp_name}' in thread: {threading.current_thread().name}")
        
        comp_html_content = task.comp_file_path.read_text(encoding='utf-8')
        
        # Download image of the component from Figma
        image_url = task.figma_api.get_node_image_url(task.figma_file_key, task.comp_node['id'])
        if not image_url:
            logging.warning(f"Could not get image URL for common component '{task.comp_name}'. Skipping refinement.")
            return task.comp_name, comp_html_content
        
        sanitized_comp_name = re.sub(r'\W+', '_', task.comp_name)
        image_path = task.temp_img_dir / f"{task.comp_name}_{sanitized_comp_name}.png"
        if not task.figma_api.download_image(image_url, image_path):
            logging.warning(f"Could not download image for common component '{task.comp_name}'. Skipping refinement.")
            return task.comp_name, comp_html_content

        # Iteratively improve the component
        current_comp_html = comp_html_content
        for i in range(task.config["iteration_count"]):
            logging.info(f"-> Iteration {i+1}/{task.config['iteration_count']} for common component '{task.comp_name}'")

            prompt = f"""
            You are an expert front-end developer specializing in creating pixel-perfect, responsive websites using HTML5 and Tailwind CSS.
            Your task is to analyze an image of a website component from a Figma design and its corresponding HTML code.
            Compare the provided image (the source of truth) with the HTML code meticulously. Identify ALL discrepancies, no matter how small, including:
            - **Layout and Positioning**: Incorrect flex/grid usage, misalignment, unexpected wrapping, incorrect stacking order.
            - **Spacing**: Inaccurate padding, margin, gap values (both internal and external spacing).
            - **Sizing**: Incorrect width, height, or aspect ratios for elements, especially images and containers.
            - **Typography**: Discrepancies in font-family, font-size, font-weight, line-height, letter-spacing, and text alignment.
            - **Colors**: Incorrect background colors, text colors, border colors, and gradients.
            - **Borders & Shadows**: Missing or incorrect border-radius, border-width, border-style, box-shadow, and text-shadow.
            - **Responsiveness**: Ensure the layout adapts correctly to different screen sizes, even if the image only shows one viewport (make reasonable assumptions for common responsive patterns).

            Your goal is to regenerate ONLY the HTML for this specific component to make it an EXACT, pixel-perfect, and responsive match to the Figma design in the image.

            **Strict Guidelines for Regeneration:**
            - **Exclusively Tailwind CSS**: Use Tailwind CSS utility classes for styling. Do NOT add custom CSS or inline styles unless a very specific style cannot be achieved with Tailwind (explain why in an internal comment if you must).
            - **Semantic HTML5**: Use appropriate HTML5 semantic elements (e.g., <header>, <nav>, <main>, <section>, <article>, <footer>, <button>, <a>, <img>) for accessibility and structure.
            - **Maintain Existing Assets**:
                - **DO NOT** replace `<img>` tags with `<svg>` or vice-versa.
                - **DO NOT** replace existing Font Awesome icons (e.g., `<i class="fa-solid fa-star"></i>`) with SVG icons.
                - **DO NOT** modify image `src` paths unless explicitly instructed.
            - **Code Structure**: Ensure the code is clean, well-indented, and easy to read.
            - **No Explanations/Comments**: Your entire response MUST be only the raw HTML code for the component. Do NOT include any explanatory text, comments (unless absolutely necessary for a non-Tailwind style), or markdown formatting around the code.

            Here is the current HTML code for the component:
            ```html
            {current_comp_html}
            ```
            """
            
            response = task.langchain_processor.process_with_image(prompt, image_path)
            new_comp_code = clean_gemini_response(response)

            if new_comp_code == current_comp_html:
                logging.info(f"LangChain returned identical code for '{task.comp_name}'. No changes needed in this iteration.")
                break
            else:
                logging.info(f"LangChain generated updated code for '{task.comp_name}'. Applying changes.")
                current_comp_html = new_comp_code
        
        return task.comp_name, current_comp_html
        
    except Exception as e:
        logging.error(f"An error occurred during component processing for '{task.comp_name}': {e}", exc_info=True)
        return task.comp_name, task.comp_file_path.read_text(encoding='utf-8')

def process_section_task(task: SectionTask) -> Tuple[str, str, str]:
    """Process a single section task."""
    try:
        section = task.section
        section_name = section['name']
        # Sanitize section_name to match HTML comment format
        section_name_sanitized = re.sub(r'^\d+\s*', '', section_name).strip()
        
        logging.info(f"Processing section: '{section_name}' in thread: {threading.current_thread().name}")
        
        # Skip processing Header/Footer sections if common components are enabled
        if task.config.get("is_common_header_footer", False) and (section_name_sanitized.lower() == "header" or section_name_sanitized.lower() == "footer"):
            logging.info(f"Skipping refinement of common component section: '{section_name_sanitized}'")
            return section_name_sanitized, "", "skipped"
        
        # 1. Download image of the section from Figma
        image_url = task.figma_api.get_node_image_url(task.figma_file_key, section['id'])
        if not image_url:
            return section_name_sanitized, "", "no_image"
        
        sanitized_section_name = re.sub(r'\W+', '_', section_name)
        image_path = task.temp_img_dir / f"{task.page_name}_{sanitized_section_name}.png"
        if not task.figma_api.download_image(image_url, image_path):
            return section_name_sanitized, "", "download_failed"

        # 2. Extract current HTML for the section
        current_section_html = extract_section_html(task.full_html_content, section_name_sanitized)
        if not current_section_html:
            return section_name_sanitized, "", "no_html"

        # 3. Iteratively improve the section
        for i in range(task.config["iteration_count"]):
            logging.info(f"-> Iteration {i+1}/{task.config['iteration_count']} for section '{section_name}'")

            # Conditional instruction for common components
            common_component_instruction = ""
            if task.config.get("is_common_header_footer", False) and (section_name_sanitized.lower() == "header" or section_name_sanitized.lower() == "footer"):
                common_component_instruction = "- **CRITICAL**: This is a common component (Header/Footer). You MUST NOT generate any `<header>` or `<footer>` elements directly within this section, as they are handled as separate, shared components. Focus ONLY on the content specific to this section, avoiding any full document structure or shared component elements."
            
            # Prepare prompt for LangChain
            prompt = f"""
            You are an expert front-end developer specializing in creating pixel-perfect, responsive websites using HTML5 and Tailwind CSS.
            Your task is to analyze an image of a website section from a Figma design and its corresponding HTML code.
            Compare the provided image (the source of truth) with the HTML code meticulously. Identify ALL discrepancies, no matter how small, including:
            - **Layout and Positioning**: Incorrect flex/grid usage, misalignment, unexpected wrapping, incorrect stacking order.
            - **Spacing**: Inaccurate padding, margin, gap values (both internal and external spacing).
            - **Sizing**: Incorrect width, height, or aspect ratios for elements, especially images and containers.
            - **Typography**: Discrepancies in font-family, font-size, font-weight, line-height, letter-spacing, and text alignment.
            - **Colors**: Incorrect background colors, text colors, border colors, and gradients.
            - **Borders & Shadows**: Missing or incorrect border-radius, border-width, border-style, box-shadow, and text-shadow.
            - **Responsiveness**: Ensure the layout adapts correctly to different screen sizes, even if the image only shows one viewport (make reasonable assumptions for common responsive patterns).

            Your goal is to regenerate ONLY the HTML for this specific section to make it an EXACT, pixel-perfect, and responsive match to the Figma design in the image.

            **Strict Guidelines for Regeneration:**
            - **Exclusively Tailwind CSS**: Use Tailwind CSS utility classes for styling. Do NOT add custom CSS or inline styles unless a very specific style cannot be achieved with Tailwind (explain why in an internal comment if you must).
            - **Semantic HTML5**: Use appropriate HTML5 semantic elements (e.g., <header>, <nav>, <main>, <section>, <article>, <footer>, <button>, <a>, <img>) for accessibility and structure.
            {common_component_instruction}
            - **Maintain Existing Assets**:
                - **DO NOT** replace `<img>` tags with `<svg>` or vice-versa.
                - **DO NOT** replace existing Font Awesome icons (e.g., `<i class="fa-solid fa-star"></i>`) with SVG icons.
                - **DO NOT** modify image `src` paths unless explicitly instructed.
            - **Code Structure**: Ensure the code is clean, well-indented, and easy to read.
            - **No Explanations/Comments**: Your entire response MUST be only the raw HTML code for the section. Do NOT include any explanatory text, comments (unless absolutely necessary for a non-Tailwind style), or markdown formatting around the code.

            Here is the current HTML code for the section:
            '''html
            {current_section_html}
            '''
            """

            # Call LangChain processor
            response = task.langchain_processor.process_with_image(prompt, image_path)
            new_section_code = clean_gemini_response(response)
            
            if new_section_code == current_section_html:
                logging.info(f"LangChain returned identical code for section '{section_name}'. No changes needed in this iteration.")
                break
            else:
                logging.info(f"LangChain generated updated code for section '{section_name}'. Applying changes.")
                current_section_html = new_section_code
        
        return section_name_sanitized, current_section_html, "success"
        
    except Exception as e:
        logging.error(f"An error occurred during section processing for '{section_name}': {e}", exc_info=True)
        return section_name_sanitized, "", "error"

# --- MAIN LOGIC ---

def process_project(project_path, config):
    """Main function to process the entire project with multi-threading."""
    project_path = Path(project_path)
    project_src_path = project_path / "src"
    project_name = project_path.name

    setup_logging(project_name)
    logging.info(f"--- Starting LangChain Figma Code Healer for project: {project_name} ---")
    logging.info(f"Configuration: Model={config['gemini_model']}, Iterations={config['iteration_count']}, Delay={config['processing_delay']}s, Threads={config['max_threads']}")
    
    if not project_src_path.is_dir():
        logging.error(f"'src' directory not found in project path: {project_path}")
        sys.exit(1)

    # Setup LangChain processor and Figma API
    langchain_processor = LangChainGeminiProcessor(
        api_key=config["gemini_api_key"],
        model_name=config["gemini_model"],
        processing_delay=config["processing_delay"]
    )
    figma_api = FigmaAPI(config["figma_api_token"])

    # Create a temporary directory for images
    temp_img_dir = project_path / ".temp_figma_images"
    temp_img_dir.mkdir(exist_ok=True)
    logging.info(f"Temporary image directory created at: {temp_img_dir}")

    # Get Figma data
    figma_file_key = get_figma_file_key(config["figma_file_url"])
    figma_data = figma_api.get_file_data(figma_file_key)
    document = figma_data.get('document')
    
    if not document:
        logging.error("Could not find 'document' in Figma API response.")
        return

    # Find the main canvas
    main_canvas = None
    for top_level_child in document.get('children', []):
        if top_level_child['type'] == 'CANVAS' and top_level_child['name'] == 'Page 1':
            main_canvas = top_level_child
            break

    if not main_canvas:
        logging.error("Could not find the main 'CANVAS' (e.g., 'Page 1') in Figma file. Check Figma structure.")
        return

    is_common_header_footer = config.get("is_common_header_footer", False)

    # Process common Header and Footer components if enabled (with threading)
    if is_common_header_footer:
        logging.info("\n--- Processing Common Header and Footer Components with Multi-threading ---")
        
        # Find Header and Footer nodes in Figma data
        header_node = None
        footer_node = None
        for frame_node in main_canvas.get('children', []):
            if frame_node['type'] == 'FRAME':
                if frame_node['name'].lower() == 'header':
                    header_node = frame_node
                elif frame_node['name'].lower() == 'footer':
                    footer_node = frame_node
        
        component_tasks = []
        if header_node:
            comp_file_path = project_path / "src" / "components" / "header.html"
            if comp_file_path.is_file():
                task = ComponentTask("Header", header_node, comp_file_path, figma_api, 
                                   langchain_processor, figma_file_key, temp_img_dir, config)
                component_tasks.append(task)
        
        if footer_node:
            comp_file_path = project_path / "src" / "components" / "footer.html"
            if comp_file_path.is_file():
                task = ComponentTask("Footer", footer_node, comp_file_path, figma_api, 
                                   langchain_processor, figma_file_key, temp_img_dir, config)
                component_tasks.append(task)

        # Process components with threading
        if component_tasks:
            with ThreadPoolExecutor(max_workers=min(config["max_threads"], len(component_tasks))) as executor:
                future_to_task = {executor.submit(process_component_task, task): task for task in component_tasks}
                
                for future in as_completed(future_to_task):
                    task = future_to_task[future]
                    try:
                        comp_name, refined_html = future.result()
                        # Save the refined component
                        task.comp_file_path.write_text(refined_html, encoding='utf-8')
                        logging.info(f"Successfully refined and saved common component: '{comp_name}'.")
                    except Exception as e:
                        logging.error(f"Component task failed for '{task.comp_name}': {e}")

    # Process each page (which are frames within the main canvas)
    pages_to_process = [
        p for p in main_canvas.get('children', []) 
        if p['type'] == 'FRAME' and not (is_common_header_footer and p['name'].lower() in ['header', 'footer'])
    ]

    for page in pages_to_process:
        if page['type'] != 'FRAME':
            logging.warning(f"Skipping non-frame element within main canvas: {page.get('name', 'Unnamed')} (Type: {page['type']})")
            continue

        page_name = page['name']
        logging.info(f"\n{'='*60}\nProcessing Figma Page: '{page_name}' with Multi-threading\n{'='*60}")

        html_file_path = find_html_file_for_page(page_name, project_src_path)
        if not html_file_path:
            continue

        logging.info(f"Found matching HTML file: {html_file_path}")
        
        try:
            full_html_content = html_file_path.read_text(encoding='utf-8')
        except IOError as e:
            logging.error(f"Could not read HTML file {html_file_path}: {e}")
            continue

        # If common header/footer is enabled, replace with placeholders
        if is_common_header_footer:
            logging.info(f"Replacing actual Header and Footer content with placeholders in {html_file_path.name}")
            
            # Replace Header section with placeholder
            header_start_comment = "<!-- START: Header -->"
            header_end_comment = "<!-- END: Header -->"
            header_placeholder_html = '<header id="header-placeholder" class="sticky top-0 z-50 shadow-sm bg-white w-full"></header>'
            header_replacement_code = f"{header_start_comment}\n{header_placeholder_html}\n{header_end_comment}"
            full_html_content = re.sub(
                f"{re.escape(header_start_comment)}.*?{re.escape(header_end_comment)}",
                header_replacement_code,
                full_html_content, flags=re.DOTALL
            )

            # Replace Footer section with placeholder
            footer_start_comment = "<!-- START: Footer -->"
            footer_end_comment = "<!-- END: Footer -->"
            footer_placeholder_html = '<footer id="footer-placeholder"></footer>'
            footer_replacement_code = f"{footer_start_comment}\n{footer_placeholder_html}\n{footer_end_comment}"
            full_html_content = re.sub(
                f"{re.escape(footer_start_comment)}.*?{re.escape(footer_end_comment)}",
                footer_replacement_code,
                full_html_content, flags=re.DOTALL
            )

        # Prepare section tasks for multi-threading
        section_tasks = []
        for section in page.get('children', []):
            if section['type'] != 'FRAME':
                continue
            
            task = SectionTask(section, page_name, full_html_content, figma_api, 
                             langchain_processor, figma_file_key, temp_img_dir, config)
            section_tasks.append(task)

        # Process sections with threading
        if section_tasks:
            section_results = {}
            with ThreadPoolExecutor(max_workers=config["max_threads"]) as executor:
                future_to_task = {executor.submit(process_section_task, task): task for task in section_tasks}
                
                for future in as_completed(future_to_task):
                    task = future_to_task[future]
                    try:
                        section_name_sanitized, refined_html, status = future.result()
                        if status == "success":
                            section_results[section_name_sanitized] = refined_html
                            logging.info(f"Successfully processed section: '{section_name_sanitized}'")
                        else:
                            logging.warning(f"Section '{section_name_sanitized}' processing status: {status}")
                    except Exception as e:
                        logging.error(f"Section task failed for '{task.section['name']}': {e}")

            # Apply all section results to the HTML content
            for section_name_sanitized, refined_html in section_results.items():
                full_html_content = replace_section_html(full_html_content, section_name_sanitized, refined_html)

        # Write the final, modified content back to the HTML file
        try:
            logging.info(f"Saving all accumulated changes to {html_file_path}")
            html_file_path.write_text(full_html_content, encoding='utf-8')
            logging.info(f"Successfully saved modifications for page '{page_name}'.")
        except IOError as e:
            logging.error(f"Could not write to HTML file {html_file_path}: {e}")

    logging.info(f"\n--- LangChain Figma Code Healer has completed for project: {project_name} ---")

if __name__ == "__main__":
    # Determine project_name early for logging setup
    initial_project_path = None
    if os.getenv("PROJECT_PATH"):
        initial_project_path = os.getenv("PROJECT_PATH")
    elif len(sys.argv) > 1:
        initial_project_path = sys.argv[1]
    
    project_name_for_logging = Path(initial_project_path).name if initial_project_path else "default_project"
    setup_logging(project_name_for_logging)

    app_config = load_configuration()

    project_folder_path = None

    # Prioritize PROJECT_PATH from environment variables
    if app_config["project_path"]:
        project_folder_path = app_config["project_path"]
        logging.info(f"Using project path from environment variable: {project_folder_path}")
    elif len(sys.argv) < 2:
        print("Usage: Set PROJECT_PATH environment variable OR run: python LangChain-Checker-2.py <path_to_your_project_folder>")
        sys.exit(1)
    else:
        project_folder_path = sys.argv[1]
        logging.info(f"Using project path from command-line argument: {project_folder_path}")
        
    if not Path(project_folder_path).is_dir():
        print(f"Error: The provided path '{project_folder_path}' is not a valid directory.")
        sys.exit(1)

    # Start processing
    process_project(project_folder_path, app_config)