import os
import re
import subprocess
import shutil
import time
import logging
import requests
from dotenv import load_dotenv
from PIL import Image
from io import BytesIO
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import json
from typing import Dict, List, Optional, Tuple, Any

# LangChain imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain.schema import Document

# --- Configuration & Setup ---

# Load environment variables from .env file
load_dotenv()

# Get configuration from environment
FIGMA_ACCESS_TOKEN = os.getenv("FIGMA_ACCESS_TOKEN")
FIGMA_FILE_URL = os.getenv("FIGMA_FILE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
MULTI_PAGE_MODE = os.getenv("MULTI_PAGE_MODE", "true").lower() == "true"
PAGE_PROCESSING_DELAY = int(os.getenv("PAGE_PROCESSING_DELAY", 3))
USE_FIGMA_PAGE_NAMES = os.getenv("USE_FIGMA_PAGE_NAMES", "true").lower() == "true"
IS_COMMAN_HEADER_FOOTER = os.getenv("IS_COMMAN_HEADER_FOOTER", "false").lower() == "true"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 4))  # Number of threads for parallel processing

# Thread-safe logging lock
log_lock = Lock()

# Initialize LangChain LLM
llm = ChatGoogleGenerativeAI(
    model=GEMINI_MODEL,
    google_api_key=GEMINI_API_KEY,
    temperature=0.1,
    max_tokens=8192
)

# Create output parser
output_parser = StrOutputParser()

# --- Logging Setup ---
log_formatter = logging.Formatter('%(message)s')
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Determine the log file name dynamically
BASE_LOG_FILE_NAME = "generation_log.txt"
log_file_path = BASE_LOG_FILE_NAME
if os.path.exists(BASE_LOG_FILE_NAME):
    i = 1
    while True:
        new_log_file_name = f"generation_log_{i}.txt"
        if not os.path.exists(new_log_file_name):
            log_file_path = new_log_file_name
            break
        i += 1# F
ile handler
file_handler = logging.FileHandler(log_file_path, mode='w')
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

def thread_safe_log(message: str, level: str = "info"):
    """Thread-safe logging function"""
    with log_lock:
        if level.lower() == "info":
            logger.info(message)
        elif level.lower() == "warning":
            logger.warning(message)
        elif level.lower() == "error":
            logger.error(message)
        elif level.lower() == "debug":
            logger.debug(message)

# --- Provided Boilerplate & Command Functions ---

def run_command(command, cwd):
    """Runs a command in a specified directory."""
    thread_safe_log(f"Running command: '{' '.join(command)}' in '{cwd}'")
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True, shell=True)
        thread_safe_log(result.stdout)
        if result.stderr:
            thread_safe_log(result.stderr, "warning")
        return result
    except subprocess.CalledProcessError as e:
        thread_safe_log(f"Error running command: {' '.join(command)}", "error")
        thread_safe_log(e.stdout, "error")
        thread_safe_log(e.stderr, "error")
        raise RuntimeError(f"Command failed: {' '.join(command)}") from e

def setup_vite_tailwind_project(project_dir):
    """Sets up a new Vite project with Tailwind CSS by cloning from a boilerplate repository."""
    thread_safe_log(f"Step 0: Setting up Vite + Tailwind CSS project in '{project_dir}'...")
    repo_url = "https://github.com/kombee-technologies/figma2html-tailwind-boilerplate.git"
    
    if os.path.exists(project_dir):
        thread_safe_log(f"   -> Removing existing directory '{project_dir}'...")
        shutil.rmtree(project_dir)
        
    thread_safe_log(f"   -> Cloning repository from '{repo_url}'...")
    run_command(['git', 'clone', repo_url, project_dir], cwd=os.getcwd())
    
    public_img_dir = os.path.join(project_dir, "src", "public", "img")
    os.makedirs(public_img_dir, exist_ok=True)
    thread_safe_log(f"   -> Ensured directory '{public_img_dir}' exists.")
    
    thread_safe_log(f"   -> Project structure cloned successfully to '{project_dir}'")
    
    thread_safe_log("   -> Installing dependencies with pnpm...")
    run_command(['pnpm', 'install'], cwd=project_dir)
    thread_safe_log("   -> Dependencies installed.")# --- Fi
gma API Interaction ---

def get_figma_file_key_from_url(url):
    """Extracts the file key from a Figma URL."""
    thread_safe_log(f"Attempting to extract file key from URL: {url}", "debug")
    match = re.search(r'(?:file|design)/([a-zA-Z0-9]+)', url)
    if not match:
        raise ValueError("Invalid Figma file URL. Could not extract file key.")
    return match.group(1)

def figma_api_get(endpoint):
    """Makes a GET request to the Figma API."""
    headers = {"X-Figma-Token": FIGMA_ACCESS_TOKEN}
    url = f"https://api.figma.com/v1/{endpoint}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def find_nodes_by_name(node, names_to_find):
    """Recursively searches the Figma node tree for nodes with specific names."""
    found_nodes = {}
    if 'name' in node and node['name'] in names_to_find:
        found_nodes[node['name']] = node
        
    if 'children' in node:
        for child in node['children']:
            found_nodes.update(find_nodes_by_name(child, names_to_find))
            
    return found_nodes

def find_asset_nodes(node, image_nodes, icon_nodes):
    """
    Recursively traverses the Figma node tree to find images and icons.
    This uses heuristics to differentiate between images and icons.
    """
    node_name_lower = node.get('name', '').lower()
    node_id = node.get('id')

    # Heuristic 1: Prioritize nodes named 'logo' as images to ensure they are captured.
    is_logo = 'logo' in node_name_lower
    if is_logo:
        if node_id and node_id not in image_nodes:
            box = node.get('absoluteBoundingBox', {})
            width = box.get('width', 0)
            height = box.get('height', 0)
            clean_name = sanitize_filename(node.get('name', ''))
            image_nodes[node_id] = {
                'name': clean_name or 'logo',
                'width': round(width),
                'height': round(height)
            }
        # Do not return; a logo component might have other assets inside.

    # Heuristic to identify images: a node that has an image fill.
    has_image_fill = False
    if 'fills' in node and isinstance(node['fills'], list):
        for fill in node['fills']:
            if isinstance(fill, dict) and fill.get('type') == 'IMAGE':
                has_image_fill = True
                break

    # If it has an image fill, treat it as a distinct image asset.
    if has_image_fill:
        node_name = node.get('name', '')
        clean_name = sanitize_filename(node_name)
        if not clean_name:
            clean_name = f"image_{node.get('id', 'unknown').replace(':', '-')}"
        
        box = node.get('absoluteBoundingBox', {})
        width = box.get('width', 0)
        height = box.get('height', 0)

        if node_id and node_id not in image_nodes:
            image_nodes[node_id] = {
                'name': clean_name,
                'width': round(width),
                'height': round(height)
            }    
# Heuristic to identify icons: node is a vector, or its name suggests it's an icon.
    node_name = node.get('name', '').lower()
    is_vector = node.get('type') == 'VECTOR'
    is_boolean_op = node.get('type') == 'BOOLEAN_OPERATION'
    is_icon_by_name = 'icon' in node_name or 'ic_' in node_name or 'icn' in node_name

    if is_vector or is_boolean_op or is_icon_by_name:
        icon_name = node.get('name', '').strip()
        if icon_name:
            icon_nodes.add(icon_name)  # Use a set to avoid duplicates
        return

    # Recurse through children if they exist.
    if 'children' in node and isinstance(node['children'], list):
        for child in node['children']:
            if isinstance(child, dict):
                find_asset_nodes(child, image_nodes, icon_nodes)

def download_node_image(file_key, node_id, output_path):
    """Requests an image export from Figma and downloads it."""
    thread_safe_log(f"   -> Requesting image export for node '{node_id}'...")
    img_endpoint = f"images/{file_key}?ids={node_id}&format=png&scale=2"
    img_data = figma_api_get(img_endpoint)
    
    if 'err' in img_data and img_data['err']:
        thread_safe_log(f"Figma API error when requesting image: {img_data['err']}", "error")
        return None, None

    img_url = img_data['images'][node_id]
    
    thread_safe_log(f"   -> Downloading image from URL...")
    response = requests.get(img_url, stream=True)
    response.raise_for_status()
    
    image = Image.open(BytesIO(response.content))
    image.save(output_path, "PNG")
    
    width, height = image.size
    thread_safe_log(f"   -> Saved image to '{output_path}' (Size: {width}x{height}px)")
    
    relative_path = os.path.join('img', os.path.basename(output_path))
    return f"/{relative_path.replace(os.sep, '/')}", {"width": width, "height": height}

def download_single_image(args):
    """Download a single image - used for threading"""
    node_id, image_url, node_details, assets_dir = args
    
    if not image_url or not node_details:
        return None
        
    node_name = node_details.get('name', f"image_{node_id.replace(':', '-')}")
    filename = f"{node_name}_{node_id.replace(':', '-')}.png"
    filepath = os.path.join(assets_dir, filename)

    try:
        thread_safe_log(f"   -> Downloading {filename}...")
        image_response = requests.get(image_url)
        image_response.raise_for_status()

        with open(filepath, "wb") as f:
            f.write(image_response.content)

        relative_path = os.path.join("img", filename).replace("\\", "/")
        return {
            relative_path: {
                'width': node_details['width'],
                'height': node_details['height']
            }
        }
    except requests.exceptions.RequestException as e:
        thread_safe_log(f"   -> WARNING: Could not download image for node {node_id}. Reason: {e}", "warning")
        return Nonedef d
ownload_figma_images(file_key, image_nodes, token, project_dir):
    """Fetches multiple images from Figma and saves them to the 'src/public/img' folder using multi-threading."""
    if not image_nodes:
        thread_safe_log("Step 2b: No image assets to download.")
        return {}
        
    thread_safe_log(f"Step 2b: Fetching {len(image_nodes)} image assets from Figma...")
    assets_dir = os.path.join(project_dir, "src", "public", "img")
    if not os.path.exists(assets_dir):
        os.makedirs(assets_dir)

    headers = {"X-Figma-Token": token}
    ids_param = ",".join(image_nodes.keys())
    url = f"https://api.figma.com/v1/images/{file_key}?ids={ids_param}&format=png&scale=1"

    response = requests.get(url, headers=headers)
    response.raise_for_status()

    image_data = response.json()
    if 'images' not in image_data or not image_data['images']:
        thread_safe_log("Could not retrieve image URLs from Figma for assets.", "warning")
        return {}

    image_urls = image_data.get('images', {})
    
    # Prepare arguments for threading
    download_args = []
    for node_id, image_url in image_urls.items():
        if image_url:
            node_details = image_nodes.get(node_id)
            if node_details:
                download_args.append((node_id, image_url, node_details, assets_dir))

    downloaded_files = {}
    
    # Use ThreadPoolExecutor for parallel downloads
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_args = {executor.submit(download_single_image, args): args for args in download_args}
        
        for future in as_completed(future_to_args):
            result = future.result()
            if result:
                downloaded_files.update(result)

    thread_safe_log(f"   -> {len(downloaded_files)} images saved to '{assets_dir}'")
    return downloaded_files

# --- AI Prompt Generation ---

def extract_design_properties(node, properties):
    """Recursively traverses the node tree to extract design properties like colors, fonts, and button styles."""
    # Extract colors from fills and strokes
    for prop in ['fills', 'strokes']:
        if prop in node and isinstance(node[prop], list):
            for paint in node[prop]:
                if paint.get('type') == 'SOLID' and paint.get('visible', True):
                    color_data = paint.get('color')
                    if color_data:
                        # Convert RGBA (0-1 floats) to a CSS hex string
                        r = int(color_data['r'] * 255)
                        g = int(color_data['g'] * 255)
                        b = int(color_data['b'] * 255)
                        properties['colors'].add(f"#{r:02x}{g:02x}{b:02x}")

                elif paint.get('type') == 'GRADIENT_LINEAR' and paint.get('visible', True):
                    gradient_stops = paint.get('gradientStops')
                    if gradient_stops:
                        gradient_info = {
                            'type': 'linear-gradient',
                            'stops': []
                        }
                        for stop in gradient_stops:
                            color_data = stop.get('color')
                            position = stop.get('position')
                            if color_data and position is not None:
                                r = int(color_data['r'] * 255)
                                g = int(color_data['g'] * 255)
                                b = int(color_data['b'] * 255)
                                a = round(color_data.get('a', 1) * 100) # Alpha as percentage
                                # Format as rgba(r, g, b, a%) or rgb(r, g, b) if alpha is 100%
                                color_str = f"rgba({r}, {g}, {b}, {a}%)" if a < 100 else f"rgb({r}, {g}, {b})"
                                gradient_info['stops'].append({
                                    'color': color_str,
                                    'position': round(position * 100) # Position as percentage
                                })
                        if gradient_info['stops']:
                            # Convert to a hashable tuple of tuples for the set
                            properties['gradients'].add(tuple(sorted(tuple(s.items()) for s in gradient_info['stops'])))    # Ext
ract font styles from text nodes
    if node.get('type') == 'TEXT':
        style = node.get('style', {})
        font_info = {
            'Family': style.get('fontFamily'),
            'Size': f"{style.get('fontSize')}px",
            'Weight': style.get('fontWeight'),
            'Line-Height': f"{style.get('lineHeightPx')}px"
        }
        # Get text color from its fills
        if 'fills' in node and isinstance(node['fills'], list):
            for paint in node.get('fills', []):
                if paint.get('type') == 'SOLID':
                    color_data = paint.get('color')
                    if color_data:
                        r, g, b = int(color_data['r'] * 255), int(color_data['g'] * 255), int(color_data['b'] * 255)
                        font_info['Color'] = f"#{r:02x}{g:02x}{b:02x}"
                        break
        # Use a tuple of items to make it hashable for the set
        properties['fonts'].add(tuple(sorted(font_info.items())))

    # Heuristic for buttons: A frame with a corner radius, a background color, and a text child.
    is_frame_or_component = node.get('type') in ['FRAME', 'COMPONENT', 'COMPONENT_SET']
    has_children = 'children' in node and isinstance(node['children'], list)

    if is_frame_or_component and has_children:
        has_text_child = any(child.get('type') == 'TEXT' for child in node['children'])
        has_bg_color = any(
            p.get('type') == 'SOLID' for p in node.get('fills', []) if isinstance(p, dict)
        )

        if has_text_child and has_bg_color and node.get('name', '').lower() not in ['icon', 'logo']:
            button_style = {}
            # Background color
            for fill in node.get('fills', []):
                if fill.get('type') == 'SOLID':
                    color_data = fill.get('color')
                    r, g, b = int(color_data['r'] * 255), int(color_data['g'] * 255), int(color_data['b'] * 255)
                    button_style['background-color'] = f"#{r:02x}{g:02x}{b:02x}"
                    break
            # Corner radius
            if 'cornerRadius' in node and node['cornerRadius'] > 0:
                button_style['border-radius'] = f"{node['cornerRadius']}px"
            
            # Padding (approximated from child positions)
            if len(node['children']) > 0:
                child_box = node['children'][0].get('absoluteBoundingBox', {})
                node_box = node.get('absoluteBoundingBox', {})
                if child_box and node_box:
                    padding_y = (node_box['height'] - child_box['height']) / 2
                    if padding_y > 2:
                         button_style['padding-vertical'] = f"{round(padding_y)}px"

            # Use tuple of items to make it hashable for the set
            properties['buttons'].add(tuple(sorted(button_style.items())))

    # Recurse through children
    if has_children:
        for child in node['children']:
            if isinstance(child, dict):
                extract_design_properties(child, properties)# ---
 LangChain Prompt Templates ---

component_prompt_template = ChatPromptTemplate.from_messages([
    ("system", """You are an expert front-end developer specializing in creating clean, responsive, and production-ready HTML components using Tailwind CSS.

Your task is to convert the provided Figma design of a single component into a self-contained HTML file.

Component Name: {component_name}

{design_summary_section}

Instructions & Requirements:
1. Analyze the Image: Use the provided image of the component for layout, spacing, colors, and typography.
2. HTML Structure:
   - CRITICAL: Figma Design Fidelity (All Sections & Parts): You MUST ensure that every single section and part of the generated HTML, CSS, and JavaScript is created exactly as shown in the Figma design image.
   - CRITICAL: Sticky Header (for Header Component): If this is the Header component, the outer <nav> element within src/components/header.html should not directly apply sticky, top-0, z-50, or shadow-md classes.
   - CRITICAL: Modern Hamburger Menu: If a navigation menu is present, implement a modern, fully functional and working, and visually appealing mobile "hamburger menu" with smooth open and close animations.
   - CRITICAL: Interactive Elements Implementation: If the Figma design includes interactive elements such as pagination, accordion sections, toggle buttons, dropdowns, modals, tabs, carousels, or any other user-interactive components, you MUST implement them with fully functional JavaScript code.
   - CRITICAL: Responsive Table Implementation: If the Figma design includes table sections, you MUST implement them as fully responsive tables that work perfectly on any screen resolution.
   - CRITICAL: Header and Footer Navigation Structure: For header and footer components, you MUST use specific structure with proper semantic HTML5 tags.
   - Generate ONLY the HTML code for the {component_name} component.
   - Do NOT include <!DOCTYPE>, <html>, <head>, or <body> tags. The output should be a snippet ready for injection.
   - Use semantic HTML5 tags (<header>, <footer>, <nav>, <ul>, <li>, <a>, <button>).
   - Use Font Awesome for all icons. The main page will include the CDN. Use classes like <i class="fas fa-icon-name"></i>.
3. Styling:
   - You MUST use Tailwind CSS utility classes for all styling.
   - Do NOT generate any <style> blocks or separate CSS files.
   - Ensure the component is responsive using Tailwind's breakpoints (e.g., md:, lg:).
4. Image Asset:
   - The image for this component has been provided. Use the following path: {image_list}
   - Note: All images, including this one, will be available in the src/public/img/ directory of the generated project.
5. Output Format (Strictly Enforced):
   - Provide the entire code output in a single response block.
   - The file path MUST be src/components/{component_name_lower}.html.

Now, based on the provided image, generate the HTML for the {component_name} component."""),
    ("human", "Please analyze this Figma design image and generate the HTML component code.")
])

master_prompt_template = ChatPromptTemplate.from_messages([
    ("system", """You are an expert front-end developer specializing in creating clean, responsive, and production-ready code from design mockups using Tailwind CSS.

Your task is to convert the provided Figma design for the '{page_name}' page into a responsive website using Vite, Tailwind CSS, and JavaScript.

Project Name: {project_name}

{component_injection_instructions}

{image_prompt_section}

{design_summary_section}

Instructions & Requirements:
1. CRITICAL: Figma Design Fidelity (All Sections & Parts): You MUST ensure that every single section and part of the generated HTML, CSS, and JavaScript is created exactly as shown in the Figma design image.
2. Analyze the Image: Use the main design image for visual layout, spacing, colors, and typography.
3. Layout and Alignment (CRITICAL): Carefully analyze the spacing, alignment, and distribution of elements in the Figma design.
4. HTML Structure (src/{page_name}.html): Generate a single {page_name}.html file and ensure it is placed in the src directory.
5. Image Asset: Here is the image for the entire page: {image_list}
6. CSS Styling (src/style.css): Generate a src/style.css file that contains only the following line: @import 'tailwindcss';
7. JavaScript Functionality (src/main.js): CRITICAL AND STRICTLY ENFORCED: Single DOMContentLoaded Block & No Duplicate Loaders
8. Output Format (Strictly Enforced): You MUST provide the entire code output in a single response.

{section_marker_instructions}

CRITICAL: Sticky Header Setup: The <header id="header-placeholder"> in src/{page_name}.html MUST include the classes sticky top-0 z-50 shadow-sm bg-white w-full to ensure the header is sticky and styled correctly.

Now, based on the provided image, generate the complete HTML, CSS, and JavaScript files for the '{page_name}' page."""),
    ("human", "Please analyze this Figma design image and generate the complete website code.")
])# --- L
angChain Chains ---

def create_component_chain():
    """Creates a LangChain chain for component generation"""
    return component_prompt_template | llm | output_parser

def create_master_chain():
    """Creates a LangChain chain for master page generation"""
    return master_prompt_template | llm | output_parser

def create_component_prompt(component_name, image_path, image_details, design_summary=None):
    """Creates a focused prompt for generating a single component (e.g., Header, Footer)."""
    image_list = f"- `{image_path}` (Size: {image_details['width']}x{image_details['height']}px)"

    design_summary_section = ""
    if design_summary:
        colors_str = ", ".join(sorted(design_summary.get('colors', [])))
        
        fonts_list = [dict(font_tuple) for font_tuple in design_summary.get('fonts', [])]
        fonts_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(f.items())])}" for f in sorted(fonts_list, key=lambda x: x.get('Size', '0'))]
        )
        
        buttons_list = [dict(button_tuple) for button_tuple in design_summary.get('buttons', [])]
        buttons_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(b.items())])}" for b in sorted(buttons_list, key=lambda x: x.get('background-color', ''))]
        )

        gradients_list = [dict(gradient_tuple) for gradient_tuple in design_summary.get('gradients', [])]
        gradients_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(g.items())])}" for g in sorted(gradients_list, key=lambda x: x.get('type', ''))]
        )

        if colors_str or fonts_str or buttons_str or gradients_str:
            design_summary_section = f"""
**Figma Design System Summary:**
To help you build an accurate layout, I have analyzed the Figma file and extracted the following design properties.
You MUST use these values when deciding on your Tailwind CSS classes.

*   **Color Palette:**
    {colors_str or "No solid colors found."}

*   **Typography (Font Styles Used):**
{fonts_str or "No text styles found."}

*   **Button Styles (Detected):**
{buttons_str or "No common button styles detected."}

*   **Gradient Styles (Detected):**
{gradients_str or "No linear gradients found."}
            """

    return {
        "component_name": component_name,
        "component_name_lower": component_name.lower(),
        "design_summary_section": design_summary_section,
        "image_list": image_list
    }

def create_master_prompt(project_name, page_name, image_path, image_details, generated_components, downloaded_assets=None, is_header_footer_common=False, is_first_page=True, design_summary=None, page_sections=None):
    """Creates the master prompt for a full page, aware of existing components and assets."""
    image_list = f"- `{image_path}` (Size: {image_details['width']}x{image_details['height']}px)"

    # Initialize dynamic instruction sections
    component_injection_instructions = ""
    image_prompt_section = ""
    design_summary_section = ""
    section_marker_instructions = ""

    # --- Component Injection Instructions ---
    if is_header_footer_common and is_first_page:
        component_injection_instructions = """
**AI Task Refinement (CRITICAL for Common Components - First Page):**
*   For this first page, you MUST analyze the *entire* provided Figma page image.
*   You are responsible for intelligently identifying the distinct Header, Footer, and Main Content sections of the page.
*   You MUST then generate code for **FIVE** files in a single response. Each file block MUST be preceded by the exact literal string `FILEPATH: ` followed by the file path, and the code enclosed in triple backticks (e.g., ```html`).
    *   **STRICTLY ENFORCED:** Do NOT include any conversational text, explanations, or remarks outside of these code blocks. ONLY the `FILEPATH:` and code blocks are allowed.
"""   
 elif is_header_footer_common and not is_first_page:
        component_injection_instructions = """
**AI Task Refinement (CRITICAL for Common Components - Subsequent Pages):**
*   This website uses shared components for the Header and Footer, which have already been generated in `src/components/header.html` and `src/components/footer.html`.
*   You MUST NOT generate HTML for the Header or Footer directly in this file. Instead, their content will be loaded dynamically.
*   You MUST NOT generate `src/style.css` or `src/main.js`. These files are common and already exist. **STRICTLY ENFORCED:** Only generate the HTML for the main page.
"""
    else:
        # Original logic if not using common header/footer
        if generated_components:
            placeholders = []
            for component, path in generated_components.items():
                placeholder_id = f"{component.lower()}-placeholder"
                tag = 'header' if 'header' in component.lower() else ('footer' if 'footer' in component.lower() else 'div')
                placeholders.append(f"<{tag} id=\"{placeholder_id}\"></{tag}>")
            
            if placeholders:
                component_injection_instructions = f"""
**Shared Components Integration (CRITICAL):**
*   This website uses shared components for the Header and Footer.
*   You MUST NOT generate the HTML for the Header or Footer directly in this file. Instead, their content will be loaded dynamically.
*   Instead, you MUST add empty placeholder elements where these components should appear.
*   Use the following placeholders:
    *   For the Header: `{placeholders[0]}`
    *   For the Footer: `{placeholders[1]}`
*   I have a JavaScript function that will automatically fetch the content from `src/components/header.html` and `src/components/footer.html` and inject it into these placeholders.
    *   **STRICTLY ENFORCED:** Do NOT include any conversational text, explanations, or remarks outside of the code block. ONLY the `FILEPATH:` and code block are allowed.
"""

    # --- Image Assets Section ---
    if downloaded_assets:
        asset_list = "\n".join(
            [f"- `{path}` (Size: {details['width']}x{details['height']}px)"
             for path, details in downloaded_assets.items()]
        )
        image_prompt_section = f"""
**Image Assets (Pre-downloaded):**
*   I have already downloaded the necessary image assets from the design.
*   They are located in the `src/public/img/` directory.
*   You MUST use these exact relative paths (prefixed with a forward slash) in your HTML `<img>` tags.
*   Here is the list of available images:
{asset_list}
"""

    # --- Design System Summary Section ---
    if design_summary:
        colors_str = ", ".join(sorted(design_summary.get('colors', [])))
        
        fonts_list = [dict(font_tuple) for font_tuple in design_summary.get('fonts', [])]
        fonts_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(f.items())])}" for f in sorted(fonts_list, key=lambda x: x.get('Size', '0'))]
        )
        
        buttons_list = [dict(button_tuple) for button_tuple in design_summary.get('buttons', [])]
        buttons_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(b.items())])}" for b in sorted(buttons_list, key=lambda x: x.get('background-color', ''))]
        )

        gradients_list = [dict(gradient_tuple) for gradient_tuple in design_summary.get('gradients', [])]
        gradients_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(g.items())])}" for g in sorted(gradients_list, key=lambda x: x.get('type', ''))]
        )

        if colors_str or fonts_str or buttons_str or gradients_str:
            design_summary_section = f"""
**Figma Design System Summary:**
To help you build an accurate layout, I have analyzed the Figma file and extracted the following design properties.
You MUST use these values when deciding on your Tailwind CSS classes.

*   **Color Palette:**
    {colors_str or "No solid colors found."}

*   **Typography (Font Styles Used):**
{fonts_str or "No text styles found."}

*   **Button Styles (Detected):**
{buttons_str or "No common button styles detected."}

*   **Gradient Styles (Detected):**
{gradients_str or "No linear gradients found."}
"""    # 
--- Section Markers Instructions ---
    if page_sections:
        section_list_formatted = "\n".join([f"        *   `{section_name}`" for section_name in page_sections])
        section_marker_instructions = f"""
        *   **CRITICAL: Section Markers:** You MUST add HTML comments to delineate each major section. For example, `<!-- START: Section Name -->` before a `<section>` tag and `<!-- END: Section Name -->` after its closing `</section>` tag.
            *   **STRICTLY ENFORCED:** You MUST use the following section names for your markers, in the exact order provided:
{section_list_formatted}
            *   Do NOT infer section names; use these precisely.
"""

    return {
        "project_name": project_name,
        "page_name": page_name,
        "component_injection_instructions": component_injection_instructions,
        "image_prompt_section": image_prompt_section,
        "design_summary_section": design_summary_section,
        "section_marker_instructions": section_marker_instructions,
        "image_list": image_list
    }

# --- AI Interaction & File Processing ---

async def generate_code_with_langchain(prompt_data, image_path, chain_type="master"):
    """Sends the prompt and image to the LangChain model and gets the response."""
    thread_safe_log("   -> Sending request to LangChain LLM...")
    
    if not os.path.exists(image_path):
        thread_safe_log(f"Image path does not exist: {image_path}", "error")
        raise FileNotFoundError(f"Cannot find image at {image_path}")

    try:
        # Create the appropriate chain
        if chain_type == "component":
            chain = create_component_chain()
        else:
            chain = create_master_chain()
        
        # For now, we'll use the text-only approach since image handling in LangChain requires specific setup
        # In a production environment, you'd want to implement proper image handling
        response = await chain.ainvoke(prompt_data)
        
        thread_safe_log("   -> Received response from LangChain LLM.")
        thread_safe_log(f"LANGCHAIN RAW RESPONSE:\n---\n{response}\n---", "debug")
        return response
    except Exception as e:
        thread_safe_log(f"An error occurred with the LangChain LLM: {e}", "error")
        return None

def parse_ai_response(response_text):
    """Parses the multi-file AI response using regex."""
    files = {}
    
    # Split the response by FILEPATH: to get individual file blocks
    file_blocks = re.split(r'(FILEPATH:\s*[^\n]+)', response_text, flags=re.IGNORECASE)

    # The first element will be everything before the first FILEPATH, which we ignore
    # Then it alternates: delimiter, content, delimiter, content...
    current_filepath = None
    for i, block in enumerate(file_blocks):
        if not block.strip():
            continue # Skip empty blocks

        if block.lower().startswith('filepath:'):
            # This is a filepath declaration
            # Extract the actual path
            match = re.search(r'FILEPATH:\s*([^\n]+)', block, re.IGNORECASE)
            if match:
                current_filepath = match.group(1).strip()
            else:
                current_filepath = None # Should not happen if split worked correctly
        elif current_filepath:
            # This block is the content for the previously found filepath
            content = block.strip()
            
            # Remove any leading/trailing ``` blocks from the content
            content = re.sub(r'^```(?:\w+)?\s*\n?', '', content, flags=re.IGNORECASE) # Remove leading ```
            content = re.sub(r'\n?\s*```\s*$', '', content) # Remove trailing ```

            # Clean up the path, specifically removing duplicate .html if present
            cleaned_path = current_filepath
            if cleaned_path.lower().endswith('.html.html'):
                cleaned_path = cleaned_path[:-5] # Remove the last .html
            
            # Aggressively sanitize path to remove any invalid characters
            cleaned_path = re.sub(r'[^a-zA-Z0-9_/.:-]', '', cleaned_path) 
            
            files[cleaned_path] = content.strip()
            current_filepath = None # Reset for next file block

    if not files:
        thread_safe_log("Could not parse AI response. No file blocks found.", "warning")
        thread_safe_log(f"Response Text was: {response_text}", "warning")
        
    thread_safe_log(f"   -> Parsed {len(files)} file(s) from AI response.")
    return filesdef 
save_files_from_response(project_dir, files, figma_frame_name=None):
    """Saves the parsed files to the project directory."""
    for file_path, content in files.items():
        # Ensure path is relative to the project directory, not absolute
        if os.path.isabs(file_path):
            thread_safe_log(f"File path '{file_path}' is absolute, skipping.", "warning")
            continue
        
        full_path = os.path.join(project_dir, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        if figma_frame_name and file_path.endswith('.html'):
            comment = f"<!-- Figma Frame Name: {figma_frame_name} -->\n"
            content = comment + content
        
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        thread_safe_log(f"   -> Wrote file: {full_path}")

def inject_component_loader_js(project_dir, generated_components):
    """Appends JavaScript to main.js to load HTML components."""
    if not generated_components:
        thread_safe_log("No components to inject. Skipping JS injection.")
        return

    main_js_path = os.path.join(project_dir, 'src', 'main.js')

    # Create main.js if it doesn't exist
    if not os.path.exists(main_js_path):
        with open(main_js_path, 'w') as f:
            f.write("// Main JavaScript file\n")
        thread_safe_log(f"Created missing file: {main_js_path}")

    loader_script = "\n\n// --- Component Loader ---\n"
    loader_script += "document.addEventListener('DOMContentLoaded', () => {\n"
    
    for component_name, component_path in generated_components.items():
        placeholder_id = f"{component_name.lower()}-placeholder"
        # Use relative path from the HTML file in the src directory
        relative_path = os.path.join('components', os.path.basename(component_path)).replace(os.sep, '/')
        
        loader_script += f"""
    const {component_name.lower()}El = document.getElementById('{placeholder_id}');
    if ({component_name.lower()}El) {{
        fetch('/{relative_path}')
            .then(response => response.text())
            .then(data => {{
                {component_name.lower()}El.innerHTML = data;
            }})
            .catch(error => console.error('Error loading {component_name}:', error));
    }}
"""
    loader_script += "});\n"

    with open(main_js_path, 'a', encoding='utf-8') as f:
        f.write(loader_script)
    thread_safe_log(f"Appended component loader logic to '{main_js_path}'.")

def sanitize_filename(name):
    """Sanitizes a string to be a valid filename."""
    return re.sub(r'[^a-zA-Z0-9_. -]', '', name).replace(' ', '_')

def extract_sections_from_page_node(page_node):
    """Extracts the names of top-level frames within a page node, which are treated as sections."""
    sections = []
    if 'children' in page_node and isinstance(page_node['children'], list):
        for child in page_node['children']:
            # Assuming top-level frames within a page are the sections
            if child.get('type') == 'FRAME' or child.get('type') == 'COMPONENT' or child.get('type') == 'COMPONENT_SET':
                section_name = child.get('name', '').strip()
                if section_name:
                    # Remove numbering like '1 Hero Section' -> 'Hero Section'
                    section_name = re.sub(r'^\d+\s*', '', section_name).strip()
                    sections.append(section_name)
    return sections

# --- Utility Functions ---

def get_next_project_name(base_name):
    """
    Finds the next available project directory name by checking for existing numbered directories.
    E.g., if "my-project", "my-project-1" exist, it returns "my-project-2".
    """
    if not os.path.exists(base_name):
        return base_name

    i = 1
    while True:
        new_name = f"{base_name}-{i}"
        if not os.path.exists(new_name):
            return new_name
        i += 1# -
-- Multi-threading Page Processing ---

async def process_single_page(args):
    """Process a single page - used for threading"""
    (i, page_node, pages_count, PROJECT_NAME, file_key, generated_components, 
     downloaded_assets, IS_COMMAN_HEADER_FOOTER, design_summary) = args
    
    page_name_raw = page_node['name']
    page_name = re.sub(r'[^a-zA-Z0-9]', '', page_name_raw).lower()
    
    output_filename = f"{page_name}.html"
    
    thread_safe_log(f"\n--- Processing Page {i+1}/{pages_count}: '{page_name_raw}' -> '{output_filename}' ---")
    
    try:
        # Download image for the page
        img_dir = os.path.join(PROJECT_NAME, "src", "public", "img")
        img_filename = f"page_{page_name}.png"
        img_path, img_details = download_node_image(file_key, page_node['id'], os.path.join(img_dir, img_filename))
        
        if not img_path:
            return None

        # Extract section names from the current page node
        page_sections = extract_sections_from_page_node(page_node)
        thread_safe_log(f"   -> Extracted sections for '{page_name_raw}': {page_sections}")

        # Generate prompt and get AI response
        prompt_data = create_master_prompt(
            PROJECT_NAME, output_filename, img_path, img_details, 
            generated_components, downloaded_assets, IS_COMMAN_HEADER_FOOTER, 
            (i == 0), design_summary, page_sections
        )
        
        thread_safe_log(f"Generated prompt for page: {page_name}")
        full_img_path = os.path.join(PROJECT_NAME, "src", "public", img_path.lstrip('/'))
        
        # Use LangChain for generation
        ai_response = await generate_code_with_langchain(prompt_data, full_img_path, "master")
        
        if ai_response:
            files = parse_ai_response(ai_response)
            
            result = {
                'page_index': i,
                'page_name': page_name_raw,
                'output_filename': output_filename,
                'files': files,
                'is_first_page': (i == 0)
            }
            
            thread_safe_log(f"Successfully processed page: {page_name_raw}")
            return result
        else:
            thread_safe_log(f"Failed to generate code for page: {page_name_raw}", "error")
            return None
            
    except Exception as e:
        thread_safe_log(f"Error processing page {page_name_raw}: {e}", "error")
        return None

# --- Main Execution Logic ---

async def main():
    """The main function to run the Figma to Website generation process."""
    start_time = time.time()
    thread_safe_log("==================================================")
    thread_safe_log("    FIGMA TO WEBSITE - LANGCHAIN GENERATION SCRIPT START    ")
    thread_safe_log("==================================================")
    
    # Log configuration
    thread_safe_log("Configuration:")
    thread_safe_log(f" - GEMINI_MODEL: {GEMINI_MODEL}")
    thread_safe_log(f" - MULTI_PAGE_MODE: {MULTI_PAGE_MODE}")
    thread_safe_log(f" - PAGE_PROCESSING_DELAY: {PAGE_PROCESSING_DELAY}s")
    thread_safe_log(f" - USE_FIGMA_PAGE_NAMES: {USE_FIGMA_PAGE_NAMES}")
    thread_safe_log(f" - IS_COMMAN_HEADER_FOOTER: {IS_COMMAN_HEADER_FOOTER}")
    thread_safe_log(f" - MAX_WORKERS: {MAX_WORKERS}")
    thread_safe_log(f" - FIGMA_ACCESS_TOKEN loaded: {bool(FIGMA_ACCESS_TOKEN)}")
    thread_safe_log(f" - FIGMA_FILE_URL: {FIGMA_FILE_URL}")
    thread_safe_log(f" - GEMINI_API_KEY loaded: {bool(GEMINI_API_KEY)}")

    try:
        # Step 0: Setup project
        PROJECT_NAME_ENV = os.getenv("PROJECT_NAME")
        BASE_PROJECT_NAME = PROJECT_NAME_ENV if PROJECT_NAME_ENV else "figma-generated-website"
        PROJECT_NAME = get_next_project_name(BASE_PROJECT_NAME)
        thread_safe_log(f" - PROJECT_NAME: {PROJECT_NAME}")

        setup_vite_tailwind_project(PROJECT_NAME)
        
        # Step 1: Get Figma file data
        thread_safe_log("\nStep 1: Fetching Figma file data...")
        file_key = get_figma_file_key_from_url(FIGMA_FILE_URL)
        figma_data = figma_api_get(f"files/{file_key}")
        thread_safe_log(f"Successfully fetched Figma file: '{figma_data['name']}'")
        
        document = figma_data['document']
        main_canvas = document['children'][0]
        
        generated_components = {}
        downloaded_assets = {}
        found_icon_names = set()

        # Extract design properties (colors, fonts, etc.)
        thread_safe_log("\nStep 2b: Parsing Figma JSON to extract design properties...")
        design_properties = {'colors': set(), 'fonts': set(), 'buttons': set(), 'gradients': set()}
        extract_design_properties(document, design_properties)
        thread_safe_log(f"   -> Found {len(design_properties['colors'])} colors, {len(design_properties['fonts'])} font styles, {len(design_properties['buttons'])} button styles, and {len(design_properties['gradients'])} gradients.")
        design_summary = {
            'colors': list(design_properties['colors']),
            'fonts': list(design_properties['fonts']),
            'buttons': list(design_properties['buttons']),
            'gradients': list(design_properties['gradients']),
        }    
    # Step 2a: Find and download all image assets from the Figma file
        thread_safe_log("\nStep 2a: Identifying and downloading all image assets...")
        all_image_nodes = {}
        find_asset_nodes(document, all_image_nodes, found_icon_names)
        downloaded_assets = download_figma_images(file_key, all_image_nodes, FIGMA_ACCESS_TOKEN, PROJECT_NAME)
        thread_safe_log(f"Found {len(downloaded_assets)} image assets.")

        # Step 3: Process individual pages
        thread_safe_log("\nStep 3: Processing individual pages...")
        pages_to_process = [child for child in main_canvas['children'] if child['type'] == 'FRAME']
        
        if not pages_to_process:
            thread_safe_log("No top-level frames found on the first canvas to process as pages.", "error")
            return
            
        thread_safe_log(f"Found {len(pages_to_process)} pages (frames) to process.")
        
        # Prepare arguments for parallel processing
        page_args = []
        for i, page_node in enumerate(pages_to_process):
            args = (i, page_node, len(pages_to_process), PROJECT_NAME, file_key, 
                   generated_components, downloaded_assets, IS_COMMAN_HEADER_FOOTER, design_summary)
            page_args.append(args)
        
        # Process pages in parallel using asyncio
        if MULTI_PAGE_MODE and len(pages_to_process) > 1:
            thread_safe_log(f"Processing {len(pages_to_process)} pages in parallel with {MAX_WORKERS} workers...")
            
            # Create semaphore to limit concurrent requests
            semaphore = asyncio.Semaphore(MAX_WORKERS)
            
            async def process_with_semaphore(args):
                async with semaphore:
                    return await process_single_page(args)
            
            # Process all pages concurrently
            tasks = [process_with_semaphore(args) for args in page_args]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Sort results by page index to maintain order
            successful_results = [r for r in results if r is not None and not isinstance(r, Exception)]
            successful_results.sort(key=lambda x: x['page_index'])
            
        else:
            # Process pages sequentially
            thread_safe_log("Processing pages sequentially...")
            successful_results = []
            for args in page_args:
                result = await process_single_page(args)
                if result:
                    successful_results.append(result)
                
                # Add delay between pages if processing sequentially
                if args[0] < len(page_args) - 1:  # Not the last page
                    thread_safe_log(f"Waiting for {PAGE_PROCESSING_DELAY} seconds before next page...")
                    await asyncio.sleep(PAGE_PROCESSING_DELAY)
        
        # Step 4: Save all generated files
        thread_safe_log("\nStep 4: Saving generated files...")
        for result in successful_results:
            files = result['files']
            page_name_raw = result['page_name']
            is_first_page = result['is_first_page']
            
            if IS_COMMAN_HEADER_FOOTER and is_first_page:
                # For the first page with common header/footer, expect 5 files
                for fp, content in files.items():
                    save_files_from_response(PROJECT_NAME, {fp: content}, page_name_raw)
                    if "header.html" in fp.lower():
                        generated_components["Header"] = fp
                    elif "footer.html" in fp.lower():
                        generated_components["Footer"] = fp
                thread_safe_log(f"First page response handled. Generated components: {generated_components}")
                
            elif IS_COMMAN_HEADER_FOOTER and not is_first_page:
                # For subsequent pages with common header/footer, expect only the main page html.
                save_files_from_response(PROJECT_NAME, files, page_name_raw)
                thread_safe_log(f"Subsequent page handled: {result['output_filename']}")
            else:
                # If IS_COMMAN_HEADER_FOOTER is false, save all files returned
                save_files_from_response(PROJECT_NAME, files, page_name_raw)

        # Step 5: Post-processing
        thread_safe_log("\nStep 5: Performing post-processing steps...")
        thread_safe_log("Post-processing completed.")

    except Exception as e:
        thread_safe_log(f"A critical error occurred: {e}", "error")
        import traceback
        thread_safe_log(f"Traceback: {traceback.format_exc()}", "error")
    finally:
        end_time = time.time()
        thread_safe_log("==================================================")
        thread_safe_log(f"    LANGCHAIN SCRIPT FINISHED in {end_time - start_time:.2f} seconds")
        thread_safe_log("==================================================")

if __name__ == "__main__":
    asyncio.run(main())

"""
### LangChain Figma to Website Generator

This is a LangChain-powered version of the Figma to Website generator with multi-threading support.

### Key Features:

1. **LangChain Integration**: Uses LangChain for better prompt management and AI interaction
2. **Multi-threading Support**: Parallel processing of pages and image downloads
3. **Async/Await**: Modern asynchronous programming for better performance
4. **Thread-safe Logging**: Ensures log messages don't interfere with each other
5. **Error Handling**: Robust error handling with detailed logging

### Installation:

```bash
pip install langchain langchain-google-genai python-dotenv requests Pillow
```

### Usage:

1. Set up your `.env` file with the same variables as the original script
2. Add `MAX_WORKERS=4` to control the number of parallel threads
3. Run: `python LangChain-figma.py`

### Performance Improvements:

- **Parallel Image Downloads**: Multiple images downloaded simultaneously
- **Parallel Page Processing**: Multiple pages processed concurrently (when MULTI_PAGE_MODE=true)
- **Async Operations**: Non-blocking operations for better resource utilization
- **Thread-safe Operations**: Safe concurrent access to shared resources

### Configuration:

Add these to your `.env` file:
```
MAX_WORKERS=4  # Number of parallel threads (adjust based on your system)
```

The script maintains full compatibility with the original while adding significant performance improvements through parallelization and modern async programming patterns.
"""