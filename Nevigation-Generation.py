import os
import re
import time
import logging
import requests
import sys
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
import google.generativeai as genai
from PIL import Image

# --- Configuration ---
# Load environment variables from a .env file
load_dotenv()

# --- Project Path Configuration ---
PROJECT_FOLDER = os.getenv("PROJECT_THEME_PATH")
if not PROJECT_FOLDER:
    print("❌ Error: 'PROJECT_THEME_PATH' not found in .env file. Please define the project path.")
    exit()
if not os.path.isdir(PROJECT_FOLDER):
    print(f"❌ Error: The specified project path does not exist or is not a directory: {PROJECT_FOLDER}")
    exit()

# --- Gemini AI Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-latest")  # Default fallback
if not GEMINI_API_KEY:
    print("❌ Error: GEMINI_API_KEY not found in .env file.")
    exit()
genai.configure(api_key=GEMINI_API_KEY)

# --- Figma API Configuration ---
FIGMA_API_TOKEN = os.getenv("FIGMA_API_TOKEN")
FIGMA_FILE_URL = os.getenv("FIGMA_FILE_URL")
if not FIGMA_API_TOKEN or not FIGMA_FILE_URL:
    print("❌ Error: Figma API credentials not found in .env file or script.")
    exit()

# --- Dynamic Project Structure & Constants ---
LOG_FILE = os.path.join(PROJECT_FOLDER, "Log-for-Header&footer.txt")
HEADER_FOOTER_CONTENT_DIR = os.path.join(PROJECT_FOLDER, "Header&Footer-Content")
HEADER_FOOTER_CONTENT_FILE = os.path.join(HEADER_FOOTER_CONTENT_DIR, "Header&Footer-Content.txt")
ACF_FIELDS_DIR = os.path.join(PROJECT_FOLDER, "ACF Fields")
FUNCTIONS_FILE = os.path.join(PROJECT_FOLDER, "functions.php")

# --- Logger Setup ---
def setup_logger():
    """Sets up a logger that outputs to both console and a file."""
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
        
    logger = logging.getLogger("ACF_Generator")
    logger.setLevel(logging.INFO)
    
    # File Handler with UTF-8 encoding
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_formatter = logging.Formatter('%(message)s')
    file_handler.setFormatter(file_formatter)
    
    # Console Handler with UTF-8 encoding
    console_formatter = logging.Formatter('%(message)s')
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    
    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    
    return logger

logger = setup_logger()

# --- Helper Functions ---
def log_section_header(title):
    """Logs a formatted section header."""
    border = "═" * 80
    logger.info(f"\n{border}")
    logger.info(f"🚀 {title.upper()} 🚀")
    logger.info(f"{border}")

def log_success(message):
    logger.info(f"✅ {message}")

def log_error(message):
    logger.error(f"❌ {message}")

def log_info(message):
    logger.info(f"🔹 {message}")
    
def get_figma_file_key_from_url(url):
    """Extracts the file key from a Figma URL."""
    match = re.search(r'figma\.com/(?:file|design)/([a-zA-Z0-9]+)/', url)
    if match:
        return match.group(1)
    return None

def find_header_footer_in_first_level(node):
    """
    Searches only the first level children of a node for 'Header' and 'Footer' frames.
    Does NOT search nested children.
    """
    header_id = None
    footer_id = None
    
    if 'children' in node:
        for child in node['children']:
            child_name = child.get('name', '').lower()
            if child_name == 'header' and not header_id:
                header_id = child['id']
            elif child_name == 'footer' and not footer_id:
                footer_id = child['id']
            
            # Stop searching if both found
            if header_id and footer_id:
                break
    
    return header_id, footer_id
    
# --- Core Logic ---

def fetch_figma_frames():
    """
    Fetches Header and Footer frames from Figma by searching only first-level children
    of each page, exports them, and saves them.
    """
    log_section_header("Step 1: Fetching Header & Footer Frames from Figma")
    os.makedirs(HEADER_FOOTER_CONTENT_DIR, exist_ok=True)
    
    file_key = get_figma_file_key_from_url(FIGMA_FILE_URL)
    if not file_key:
        log_error("Could not extract Figma file key from URL.")
        return None, None

    headers = {"X-Figma-Token": FIGMA_API_TOKEN}
    
    try:
        url = f"https://api.figma.com/v1/files/{file_key}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        file_data = response.json()

        header_node_id, footer_node_id = None, None
        
        log_info("Searching for 'Header' and 'Footer' frames in first-level children only...")
        
        # Search through pages
        for page in file_data['document']['children']:
            page_name = page.get('name', 'Unnamed Page')
            log_info(f"Checking page: '{page_name}'")
            
            # Search only first-level children of the page
            h_id, f_id = find_header_footer_in_first_level(page)
            
            if h_id and not header_node_id:
                header_node_id = h_id
                log_info(f"Found Header in page '{page_name}'")
            if f_id and not footer_node_id:
                footer_node_id = f_id
                log_info(f"Found Footer in page '{page_name}'")
            
            if header_node_id and footer_node_id:
                break
        
        if not header_node_id or not footer_node_id:
            log_error("Could not find 'Header' or 'Footer' frames in the first-level children of any page.")
            log_info("Please ensure your Header and Footer frames are named exactly 'Header' and 'Footer' (case-insensitive).")
            log_info("They should be direct children of a page, not nested inside other frames.")
            
            # List all first-level frame names for debugging
            for page in file_data['document']['children']:
                if 'children' in page:
                    frame_names = [f"'{child.get('name', 'Untitled')}'" for child in page['children']]
                    if frame_names:
                        log_info(f"First-level frames in page '{page.get('name', 'Unnamed')}': {', '.join(frame_names)}")
            return None, None
            
        log_success(f"Found Header (Node ID: {header_node_id}) and Footer (Node ID: {footer_node_id}).")

        export_url = f"https://api.figma.com/v1/images/{file_key}?ids={header_node_id},{footer_node_id}&format=png&scale=2"
        export_response = requests.get(export_url, headers=headers)
        export_response.raise_for_status()
        image_urls = export_response.json()['images']
        
        header_path = os.path.join(HEADER_FOOTER_CONTENT_DIR, "header.png")
        footer_path = os.path.join(HEADER_FOOTER_CONTENT_DIR, "footer.png")
        
        with requests.get(image_urls.get(header_node_id), stream=True) as r:
            with open(header_path, 'wb') as f: f.write(r.content)
        log_success(f"Header image saved to: {header_path}")
        
        with requests.get(image_urls.get(footer_node_id), stream=True) as r:
            with open(footer_path, 'wb') as f: f.write(r.content)
        log_success(f"Footer image saved to: {footer_path}")
        
        return header_path, footer_path

    except requests.exceptions.RequestException as e:
        log_error(f"Figma API request failed: {e}")
        return None, None
    except Exception as e:
        log_error(f"An unexpected error occurred during Figma processing: {e}")
        return None, None

def generate_markdown_from_images(image_paths):
    """
    Generates a Markdown document from a list of image paths using Gemini AI.
    """
    log_section_header("Step 2: Generating Markdown from Images via Gemini AI")
    if not all(image_paths):
        log_error("One or more image paths are missing. Skipping Markdown generation.")
        return

    full_markdown_content = ""
    try:
        log_info(f"Using Gemini model: {GEMINI_MODEL}")
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        for img_path in image_paths:
            # Load image using PIL
            image = Image.open(img_path)
            part_name = "Header" if "header" in img_path else "Footer"
            
            prompt = f"""Analyze the provided image of a website {part_name}. Extract all navigation menu links and their structure precisely. Format the output strictly as a Markdown document with these rules:
1. For the Header, use the title: 'Header Navigation (Primary Navigation)'
2. For the Footer, identify distinct sections (e.g., 'Quick links'). For each section, use the title format: 'Section Name (Footer-{{section-name}}-Navigation)'.
3. List all menu items under their titles using numbered lists.
Do not include any extra text or explanations."""
            
            log_info(f"Sending {part_name} image to Gemini for analysis...")
            response = model.generate_content([prompt, image])
            
            full_markdown_content += response.text.strip() + "\n\n"
            log_success(f"Successfully generated Markdown for {part_name}.")

        with open(HEADER_FOOTER_CONTENT_FILE, 'w', encoding='utf-8') as f:
            f.write(full_markdown_content.strip())
        log_success(f"Markdown content saved to: {HEADER_FOOTER_CONTENT_FILE}")
        
    except Exception as e:
        log_error(f"An error occurred with the Gemini AI request: {e}")
        
def generate_menu_creation_code():
    """
    Generates PHP code for menu registration and creation based on the markdown file.
    """
    log_section_header("Step 3: Generating WordPress Menu PHP Code")
    if not os.path.exists(HEADER_FOOTER_CONTENT_FILE):
        log_error(f"'{os.path.basename(HEADER_FOOTER_CONTENT_FILE)}' not found. Cannot generate menu code.")
        return
        
    with open(HEADER_FOOTER_CONTENT_FILE, 'r', encoding='utf-8') as f:
        markdown_content = f.read()

    log_info("Sending Markdown to Gemini to generate PHP menu functions...")
    
    prompt = f"""Based on the following Markdown, generate a complete PHP code block for a WordPress 'functions.php' file.
**Markdown:**
---
{markdown_content}
---
**Instructions:**
1. Create a single function `my_register_and_create_menus`.
2. It must register nav menus (`primary-navigation`, `footer-{{slug}}`), define the menu structure in an array, and programmatically create/assign menus if they don't exist.
3. Hook the function to `after_setup_theme`.
4. Output only the raw PHP code block, with no explanations or markdown formatting."""
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        php_code = response.text.strip().replace("```php", "").replace("```", "")
        
        with open(FUNCTIONS_FILE, 'a', encoding='utf-8') as f:
            f.write("\n\n// --- Auto-generated Menu Registration and Creation ---\n" + php_code)
        log_success("Successfully generated and appended menu creation code to functions.php.")
        
    except Exception as e:
        log_error(f"Failed to generate menu creation code: {e}")

def parse_acf_files_for_menu_fields():
    """Parses ACF field files to find select fields for navigation menus."""
    log_info("Parsing ACF field files for menu selectors...")
    menu_field_names = []
    
    for filename in ["header-ACF-fields.txt", "footer-ACF-fields.txt"]:
        filepath = os.path.join(ACF_FIELDS_DIR, filename)
        if not os.path.exists(filepath): continue
        try:
            with open(filepath, 'r', encoding='utf-8') as f: content = f.read()
            sections = re.finditer(r'Tab:.*?Navigation.*?\n(.*?)(?=\n\n\*|\Z)', content, re.DOTALL)
            for section in sections:
                fields = re.findall(r'\*\s+`([a-zA-Z0-9_]+)`\s+\(Select\)', section.group(1))
                if fields:
                    menu_field_names.extend(fields)
                    for field in fields: log_success(f"Found ACF menu field: '{field}' in {filename}")
        except Exception as e: log_error(f"Error parsing {filename}: {e}")
    return list(set(menu_field_names))

def generate_acf_filter_code():
    """
    Finds ACF menu field names and generates the PHP code to populate them.
    """
    log_section_header("Step 4: Generating ACF Dynamic Population Code")
    
    field_names = parse_acf_files_for_menu_fields()
    if not field_names:
        log_error("No ACF menu select fields found. Skipping filter code generation.")
        return

    code_lines = ["\n\n// --- Auto-generated ACF Menu Field Population ---"]
    code_lines.append("/**\n * Dynamically populate all ACF menu dropdown fields with WordPress menus\n */")
    for name in field_names:
        code_lines.append(f"add_filter('acf/load_field/name={name}', 'populate_nav_menu_choices');")
    code_lines.append("""
function populate_nav_menu_choices($field) {
    $field['choices'] = array();
    $menus = wp_get_nav_menus();
    if (!empty($menus)) {
        foreach ($menus as $menu) {
            $field['choices'][$menu->term_id] = $menu->name;
        }
    } else {
        $field['choices'][''] = 'No menus found';
    }
    return $field;
}""")
    
    try:
        with open(FUNCTIONS_FILE, 'a', encoding='utf-8') as f: f.write("\n".join(code_lines))
        log_success("Successfully generated and appended ACF filter code to functions.php.")
    except Exception as e: log_error(f"Could not write ACF filter code to functions.php: {e}")

# --- Main Execution ---
def main():
    """Main function to orchestrate the entire script execution."""
    start_time = time.time()
    log_section_header("Starting WordPress Menu Generation Script")
    log_info(f"Target Project Path: '{PROJECT_FOLDER}'")
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_figma = executor.submit(fetch_figma_frames)
        header_path, footer_path = future_figma.result()

        if header_path and footer_path:
            future_markdown = executor.submit(generate_markdown_from_images, [header_path, footer_path])
            future_markdown.result() 

            log_section_header("Executing Code Generation Tasks in Parallel")
            future_menu_code = executor.submit(generate_menu_creation_code)
            future_acf_code = executor.submit(generate_acf_filter_code)
            
            future_menu_code.result()
            future_acf_code.result()

    total_time = time.time() - start_time
    log_section_header("Script Execution Finished")
    log_success(f"All tasks completed in {total_time:.2f} seconds.")
    log_info(f"Check the log file for details: '{LOG_FILE}'")
    log_info(f"Check the updated file: '{FUNCTIONS_FILE}'")

if __name__ == "__main__":
    main()