# generate_data_entry.py

import os
import re
import sys
import time
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

import requests
import google.generativeai as genai
from PIL import Image
from tqdm import tqdm

# --- 1. SCRIPT SETUP AND CONFIGURATION ---

# Load environment variables from .env file
load_dotenv()

# Fetch configuration from environment
PROJECT_THEME_PATH = os.getenv("PROJECT_THEME_PATH")
FIGMA_API_TOKEN = os.getenv("FIGMA_API_TOKEN")
FIGMA_FILE_URL = os.getenv("FIGMA_FILE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")
PROCESSING_DELAY = int(os.getenv("PROCESSING_DELAY", 4))
MAX_WORKER_THREADS = int(os.getenv("MAX_WORKER_THREADS", 10))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", 60))

# --- Custom Logger Class for pretty printing ---
class CustomLogger:
    """A custom logger for formatted console output."""
    ICONS = {
        "INFO": "ℹ️ ",
        "SUCCESS": "✅",
        "WARNING": "⚠️ ",
        "ERROR": "❌",
        "SETUP": "⚙️ ",
        "FIGMA": "🎨",
        "GEMINI": "✨",
        "FILE": "📄",
        "PHP": "🐘",
        "TIMER": "⏱️ "
    }

    def __init__(self, log_file_path):
        self.log_file_path = log_file_path
        # Clear log file on new run
        with open(self.log_file_path, 'w') as f:
            f.write(f"Automation Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*50 + "\n")

    def _log(self, level, message):
        log_message = f"[{level}] {message}"
        print(log_message)
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(log_message + '\n')

    def log(self, icon_key, message):
        icon = self.ICONS.get(icon_key, "➡️ ")
        self._log(f"{icon} {icon_key}", message)

# --- Helper function to sanitize names for filenames ---
def sanitize_filename(name):
    """Sanitizes a string to be a valid filename."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name) # Replace invalid chars
    name = re.sub(r'\s+', '-', name) # Replace whitespace with hyphens
    return name.strip().lower()

# Initialize logger globally
logger = None

# --- 2. FIGMA IMAGE DOWNLOADER ---

def extract_figma_file_key(figma_url):
    """Extracts the file key from a Figma URL."""
    pattern = r'figma\.com/(?:file|design)/([a-zA-Z0-9]+)'
    match = re.search(pattern, figma_url)
    if match:
        return match.group(1)
    else:
        logger.log("ERROR", f"Could not extract file key from Figma URL: {figma_url}")
        return None

def get_figma_nodes(file_key, token):
    """Fetches all nodes from a Figma file."""
    url = f"https://api.figma.com/v1/files/{file_key}"
    headers = {"X-Figma-Token": token}
    try:
        response = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.log("ERROR", f"Failed to fetch Figma file structure: {e}")
        sys.exit(1)

def discover_figma_elements(node, page_nodes, asset_nodes, parent_type=None):
    """
    Recursively finds all downloadable page frames and individual image assets.
    - Pages are identified as CANVAS nodes or top-level FRAMEs on a CANVAS.
    - Assets are identified by having an 'IMAGE' fill type.
    """
    node_name = node.get('name', 'untitled')
    node_type = node.get('type', '')
    node_id = node.get('id')

    # Heuristic 1: Identify page frames for download.
    # A downloadable page is a CANVAS or a FRAME that is a direct child of a CANVAS.
    is_page_frame = False
    if node_type == 'CANVAS':
        is_page_frame = True
    elif node_type == 'FRAME' and parent_type == 'CANVAS':
        is_page_frame = True

    if is_page_frame:
        page_name = sanitize_filename(node_name)
        if node_id and node_id not in page_nodes:
            page_nodes[node_id] = {'name': f"{page_name}-page", 'type': 'page'}

    # Heuristic 2: Identify individual image assets by checking for image fills.
    has_image_fill = False
    if 'fills' in node and isinstance(node.get('fills'), list):
        for fill in node['fills']:
            if isinstance(fill, dict) and fill.get('type') == 'IMAGE':
                has_image_fill = True
                break
    
    if has_image_fill:
        clean_name = sanitize_filename(node_name)
        if not clean_name: # Fallback for unnamed layers
            clean_name = f"image-asset_{node_id.replace(':', '-')}"

        # Ensure unique name to prevent overwrites
        final_asset_name = clean_name
        counter = 1
        # Create a set of existing names for faster lookups
        existing_asset_names = {asset['name'] for asset in asset_nodes.values()}
        while final_asset_name in existing_asset_names:
            final_asset_name = f"{clean_name}-{counter}"
            counter += 1

        if node_id and node_id not in asset_nodes:
            asset_nodes[node_id] = {
                'name': final_asset_name,
                'type': 'asset',
                'original_name': node_name,
            }

    # Always recurse through children to find all nested elements.
    if 'children' in node and isinstance(node.get('children'), list):
        for child in node['children']:
            discover_figma_elements(child, page_nodes, asset_nodes, parent_type=node_type)

def download_single_image(node_id, image_url, image_details, target_dir):
    """Downloads a single image with retry logic and returns its path and details."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(image_url, timeout=DOWNLOAD_TIMEOUT, stream=True)
            response.raise_for_status()
            
            base_name = image_details.get('name', f'figma-asset-{node_id}')
            file_path = os.path.join(target_dir, f"{base_name}.png")
            
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            return {'path': file_path, 'details': image_details}
        except requests.exceptions.RequestException as e:
            logger.log("WARNING", f"Download attempt {attempt + 1}/{max_retries} failed for node {node_id}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                logger.log("ERROR", f"Failed to download image for node {node_id} after {max_retries} attempts")
                return None

def download_figma_assets(figma_url, token, project_dir):
    """
    Fetches assets and page designs from Figma in a two-step process.
    """
    logger.log("FIGMA", "Starting Figma asset and page download process...")

    file_key = extract_figma_file_key(figma_url)
    if not file_key:
        return {}, {}
    
    logger.log("FIGMA", f"Extracted file key: {file_key}")
    
    # 1. Get all nodes from Figma file structure
    figma_data = get_figma_nodes(file_key, token)
    if not figma_data:
        return {}, {}
        
    # 2. Find and categorize all downloadable nodes (assets vs. pages)
    page_nodes = {}
    asset_nodes = {}
    discover_figma_elements(figma_data['document'], page_nodes, asset_nodes)
    
    if not page_nodes and not asset_nodes:
        logger.log("WARNING", "No downloadable assets or page frames found in Figma file.")
        return {}, {}

    logger.log("FIGMA", f"Discovered {len(page_nodes)} page frames and {len(asset_nodes)} image assets.")
    if asset_nodes:
        asset_names = [info['name'] for info in asset_nodes.values()]
        logger.log("FIGMA", f"  -> Image Assets: {', '.join(asset_names[:3])}{'...' if len(asset_names) > 3 else ''}")
    if page_nodes:
        page_names = [info['name'] for info in page_nodes.values()]
        logger.log("FIGMA", f"  -> Page Designs: {', '.join(page_names[:3])}{'...' if len(page_names) > 3 else ''}")

    # 3. Create destination directories
    assets_dir = os.path.join(project_dir, "assets", "images")
    page_images_dir = os.path.join(project_dir, "full-pages-images")
    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(page_images_dir, exist_ok=True)
    logger.log("FILE", f"Ensured asset images directory exists: {assets_dir}")
    logger.log("FILE", f"Ensured full page images directory exists: {page_images_dir}")

    headers = {"X-Figma-Token": token}
    downloaded_assets = {}
    downloaded_pages = {}

    # 4. STEP 1: Download all ASSET images first.
    if asset_nodes:
        logger.log("FIGMA", "--- Starting Step 1: Downloading Section-Level Asset Images ---")
        ids_param = ",".join(asset_nodes.keys())
        url = f"https://api.figma.com/v1/images/{file_key}?ids={ids_param}&format=png&scale=1"
        try:
            response = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT)
            response.raise_for_status()
            image_urls = response.json().get('images', {})

            if image_urls:
                with ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS, thread_name_prefix="FigmaAssetDownloader") as executor:
                    future_to_node = {executor.submit(download_single_image, node_id, url, asset_nodes[node_id], assets_dir): node_id for node_id, url in image_urls.items()}
                    
                    progress = tqdm(as_completed(future_to_node), total=len(future_to_node), desc="Downloading Asset Images")
                    for future in progress:
                        result = future.result()
                        if result:
                            file_name = os.path.basename(result['path'])
                            downloaded_assets[file_name] = result
                logger.log("SUCCESS", f"Downloaded {len(downloaded_assets)} assets to '{assets_dir}'")
        except requests.exceptions.RequestException as e:
            logger.log("ERROR", f"Failed to get asset image URLs from Figma API: {e}")

    # 5. STEP 2: Download all PAGE images after assets are done.
    if page_nodes:
        logger.log("FIGMA", "--- Starting Step 2: Downloading Full Page Frame Images ---")
        ids_param = ",".join(page_nodes.keys())
        url = f"https://api.figma.com/v1/images/{file_key}?ids={ids_param}&format=png&scale=1"
        try:
            response = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT)
            response.raise_for_status()
            image_urls = response.json().get('images', {})
            
            if image_urls:
                with ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS, thread_name_prefix="FigmaPageDownloader") as executor:
                    future_to_node = {executor.submit(download_single_image, node_id, url, page_nodes[node_id], page_images_dir): node_id for node_id, url in image_urls.items()}

                    progress = tqdm(as_completed(future_to_node), total=len(future_to_node), desc="Downloading Page Designs")
                    for future in progress:
                        result = future.result()
                        if result:
                            file_name = os.path.basename(result['path'])
                            downloaded_pages[file_name] = result
                logger.log("SUCCESS", f"Downloaded {len(downloaded_pages)} page designs to '{page_images_dir}'")
        except requests.exceptions.RequestException as e:
            logger.log("ERROR", f"Failed to get page design image URLs from Figma API: {e}")

    logger.log("SUCCESS", "Figma download process complete.")
    return downloaded_pages, downloaded_assets


# --- 3. PARSE functions.php FOR ACF BLOCKS ---

def extract_acf_blocks(functions_php_path):
    """Extracts ACF field group registration blocks from functions.php."""
    logger.log("PHP", f"Reading ACF blocks from: {functions_php_path}")
    if not os.path.exists(functions_php_path):
        logger.log("ERROR", f"functions.php not found at path: {functions_php_path}")
        sys.exit(1)
        
    with open(functions_php_path, 'r', encoding='utf-8') as f:
        content = f.read()

    pattern = re.compile(
        r"function\s+import_(\w+)_acf_fields\(\)\s*\{(.+?)\}\s*add_action\('acf/init',\s*'import_\w+_acf_fields'\);",
        re.DOTALL | re.IGNORECASE
    )
    
    matches = pattern.finditer(content)
    
    acf_blocks = {}
    for match in matches:
        page_name_raw = match.group(1).strip()
        page_name = sanitize_filename(page_name_raw)
        php_block = match.group(2)
        acf_blocks[page_name] = php_block
        logger.log("PHP", f"  -> Found ACF field group for page: '{page_name_raw}'")
        
    if not acf_blocks:
        logger.log("WARNING", "No ACF field group registration blocks found in functions.php.")
    
    return acf_blocks

# --- 4. GENERATE MARKDOWN DATA ENTRY FILES WITH GEMINI ---

def generate_markdown_file(page_name, acf_block, page_design_image, downloaded_assets):
    """Generates a markdown data entry file using Gemini AI."""
    logger.log("GEMINI", f"Generating markdown for '{page_name}' page...")
    
    data_entry_dir = os.path.join(PROJECT_THEME_PATH, "Data-Entrys")
    os.makedirs(data_entry_dir, exist_ok=True)
    output_path = os.path.join(data_entry_dir, f"{page_name}_Data_Entry.txt")

    asset_list = "\n".join([f"- {name}" for name in downloaded_assets.keys()])

    prompt = f"""
        You are a WordPress data entry assistant. Your task is to generate a detailed data entry plan in Markdown format.
        You will be given the PHP code for an Advanced Custom Fields (ACF) group, a full-page design image, and a list of available image assets.
        Analyze the design image section by section and fill in the content for each ACF field defined in the PHP code.

        **INSTRUCTIONS:**
        1.  Go through each field in the provided PHP ACF array.
        2.  For each field, extract its `key`, `label`, `name`, and `type`.
        3.  Based on the page design image, generate realistic content for each field.
        4.  For 'image', 'gallery', or 'repeater' fields with images, select the most appropriate image filename from the "AVAILABLE ASSETS" list. The asset names are descriptive (e.g., 'hero-section-image.png', 'team-member-1.png').
        5.  For text fields (`text`, `textarea`, `wysiwyg`), extract the text directly from the corresponding section in the image.
        6.  For `repeater` fields, create multiple items as seen in the design.
        7.  **IMPORTANT:** Completely SKIP any fields with the type: 'url', 'file', 'post_object', 'page_link', 'link'. Do not include them in the output.
        8.  **CRITICAL:** For fields with type 'true_false', use boolean values (true or false) NOT strings. Example: content: true (not content: 'true' or content: 'Yes').
        9.  Format the output strictly as Markdown, as shown in the example below.

        **EXAMPLE OUTPUT FORMAT:**
        ```markdown
        # Page Data Entry: Home

        ## Hero Section
        - key: 'field_65c8a7b9c0d1f'
          label: 'Hero Background Image'
          name: 'hero_background_image'
          type: 'image'
          content: 'hero-background.png'
        - key: 'field_65c8a7b9c0d20'
          label: 'Hero Title'
          name: 'hero_title'
          type: 'text'
          content: 'Transform Your Fitness Journey'
        - key: 'field_65c8a7b9c0d21'
          label: 'Show This Section'
          name: 'show_section'
          type: 'true_false'
          content: true

        ## About Us Section
        - key: 'field_...
        ...
        ```

        ---
        **PHP ACF DEFINITION for '{page_name}':**
        ```php
        {acf_block}
        ```
        ---
        **AVAILABLE ASSETS:**
        {asset_list}
        ---
        Now, analyze the attached page design image and generate the complete markdown data entry file.
        """
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        logger.log("GEMINI", f"  -> Sending request to Gemini for '{page_name}' with page design image...")
        
        response = model.generate_content([prompt, page_design_image])
        
        markdown_content = response.text.replace("```markdown", "").replace("```", "").strip()
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        
        logger.log("SUCCESS", f"Successfully created markdown file: {output_path}")
        logger.log("GEMINI", f"  -> Token usage for '{page_name}' markdown: {response.usage_metadata}")
        return output_path
        
    except Exception as e:
        logger.log("ERROR", f"Failed to generate markdown for '{page_name}': {e}")
        return None

# --- 5. GENERATE PHP DATA ENTRY FUNCTIONS WITH GEMINI ---

def generate_php_function(page_name, markdown_file_path):
    """Generates a WordPress PHP function for data entry using Gemini AI with task-based architecture."""
    logger.log("GEMINI", f"Generating PHP data entry function for '{page_name}'...")
    
    with open(markdown_file_path, 'r', encoding='utf-8') as f:
        markdown_content = f.read()

    is_options_page = page_name.lower() in ['header', 'footer']
    function_name = f"kombee_get_{page_name.replace('-', '_')}_tasks"
    
    if is_options_page:
        post_id_logic = "'option'"
        page_check_logic = "// Header/Footer uses ACF Options page - no page check needed"
    else:
        post_id_logic = f"$page->ID"
        page_check_logic = f"""$page = get_page_by_path('{page_name}');
    if (!$page) {{
        error_log('Kombee Import: "{page_name}" page not found. Skipping tasks.');
        return [];
    }}"""

    prompt = f"""
        You are an expert WordPress developer specializing in ACF and task-based data import systems. 
        Your task is to generate a PHP function that returns an array of tasks for background processing.
        
        **CRITICAL ARCHITECTURE REQUIREMENTS:**
        1. Generate a function named `{function_name}` that returns an array of tasks
        2. Each task is an associative array with specific structure based on field type
        3. Use the task-based system for background processing via WP-Cron
        4. Handle different task types: 'sideload_and_update_field', 'update_field', 'update_repeater_with_images'
        
        **TASK TYPES AND STRUCTURE:**
        
        **For IMAGE fields:**
        ```php
        $tasks[] = ['type' => 'sideload_and_update_field', 'description' => '{page_name.title()}: Field Description', 'post_id' => $post_id, 'field_key' => 'field_key_here', 'image_url' => $img_base . 'image-name.png'];
        ```
        
        **For TEXT/TEXTAREA/WYSIWYG fields:**
        ```php
        $tasks[] = ['type' => 'update_field', 'description' => '{page_name.title()}: Field Description', 'post_id' => $post_id, 'field_key' => 'field_key_here', 'value' => 'field_value_here'];
        ```
        
        **For REPEATER fields with images:**
        ```php
        $repeater_rows = [
            ['field_key1' => 'value1', 'field_key2' => $img_base . 'image.png', 'field_key3' => true],
            ['field_key1' => 'value2', 'field_key2' => $img_base . 'image2.png', 'field_key3' => false],
        ];
        $tasks[] = ['type' => 'update_repeater_with_images', 'description' => '{page_name.title()}: Repeater Description', 'post_id' => $post_id, 'field_key' => 'repeater_field_key', 'rows' => $repeater_rows, 'image_sub_field_keys' => ['field_key2']];
        ```
        
        **For REPEATER fields without images:**
        ```php
        $repeater_rows = [
            ['field_key1' => 'value1', 'field_key2' => 'value2'],
            ['field_key1' => 'value3', 'field_key2' => 'value4'],
        ];
        $tasks[] = ['type' => 'update_field', 'description' => '{page_name.title()}: Repeater Description', 'post_id' => $post_id, 'field_key' => 'repeater_field_key', 'value' => $repeater_rows];
        ```

        **FUNCTION TEMPLATE:**
        ```php
        function {function_name}() {{
            $tasks = [];
            $img_base = get_template_directory_uri() . '/assets/images/';
            {page_check_logic}
            $post_id = {post_id_logic};

            // --- Section Comments for Organization ---
            // Generate tasks based on the markdown content
            
            return $tasks;
        }}
        ```

        **IMPORTANT RULES:**
        1. **Post ID**: Use {post_id_logic} for post_id in all tasks
        2. **Boolean Values**: For true_false fields, use `true`/`false` (not strings)
        3. **Image URLs**: Always use `$img_base . 'filename.png'` format
        4. **Task Descriptions**: Use format "{page_name.title()}: Field Description"
        5. **Section Comments**: Add comments like `// --- Hero Section ---` to organize tasks
        6. **Field Keys**: Use exact field keys from the markdown
        7. **Image Sub-fields**: For repeaters with images, specify `image_sub_field_keys` array
        
        **PROVIDED DATA ENTRY MARKDOWN for '{page_name}':**
        ```markdown
        {markdown_content}
        ```

        Generate ONLY the PHP function that returns the tasks array. Do not include any other code.
        """
        
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        logger.log("GEMINI", f"  -> Sending request to Gemini for '{page_name}' PHP function...")
        
        response = model.generate_content(prompt)
        
        php_code = response.text.replace("```php", "").replace("```", "").strip()
        
        logger.log("SUCCESS", f"Successfully generated PHP function for '{page_name}'.")
        logger.log("GEMINI", f"  -> Token usage for '{page_name}' PHP: {response.usage_metadata}")
        return php_code

    except Exception as e:
        logger.log("ERROR", f"Failed to generate PHP for '{page_name}': {e}")
        return None

# --- 6. MAIN ORCHESTRATION SCRIPT ---

def main():
    """Main function to orchestrate the entire data entry generation process."""
    start_time = time.time()
    
    global logger
    log_file_path = os.path.join(PROJECT_THEME_PATH, "LOG-for_DATA_Entry.txt")
    logger = CustomLogger(log_file_path)

    logger.log("SETUP", "Starting WordPress ACF Data Entry Automation Script")
    logger.log("SETUP", f"Project Path: {PROJECT_THEME_PATH}")
    
    if not all([PROJECT_THEME_PATH, FIGMA_API_TOKEN, FIGMA_FILE_URL, GEMINI_API_KEY]):
        logger.log("ERROR", "One or more required .env variables are missing. Exiting.")
        sys.exit(1)
    
    genai.configure(api_key=GEMINI_API_KEY)

    # --- Step 1: Download Figma Assets ---
    downloaded_pages, downloaded_assets = download_figma_assets(FIGMA_FILE_URL, FIGMA_API_TOKEN, PROJECT_THEME_PATH)
    time.sleep(PROCESSING_DELAY)

    # --- Step 2: Extract ACF Blocks from functions.php ---
    functions_php_path = os.path.join(PROJECT_THEME_PATH, 'functions.php')
    acf_blocks = extract_acf_blocks(functions_php_path)
    if not acf_blocks:
        logger.log("ERROR", "No ACF blocks found. Cannot proceed. Exiting.")
        sys.exit(1)
    time.sleep(PROCESSING_DELAY)

    # --- Step 3: Generate Markdown Data Entry Files ---
    markdown_files = {}
    for page_name, acf_block in acf_blocks.items():
        # Find the corresponding page design image
        possible_name = f"{page_name}-page.png"
        image_path = None
        
        if possible_name in downloaded_pages:
            image_path = downloaded_pages[possible_name]['path']
            logger.log("INFO", f"Found page design '{possible_name}' for ACF group '{page_name}'")
        else:
            logger.log("WARNING", f"Could not find a direct match for page design '{possible_name}'. This might happen if the ACF group name (e.g., 'homepage') differs from the Figma page name (e.g., 'home-page').")
            # Fallback for slight name mismatches like 'homepage' vs 'home-page'
            for key in downloaded_pages.keys():
                if page_name.replace('-', '') == key.replace('-page.png', '').replace('-', ''):
                    image_path = downloaded_pages[key]['path']
                    logger.log("INFO", f"Found a likely match: Using '{key}' for ACF group '{page_name}'")
                    break

        if not image_path:
             logger.log("ERROR", f"Could not find a suitable page design image for '{page_name}'. Skipping markdown generation for this page.")
             continue
        
        try:
            page_design_image = Image.open(image_path)
            md_path = generate_markdown_file(page_name, acf_block, page_design_image, downloaded_assets)
            if md_path:
                markdown_files[page_name] = md_path
            time.sleep(PROCESSING_DELAY)
        except Exception as e:
            logger.log("ERROR", f"Failed to open or process image {image_path}: {e}")

    if not markdown_files:
        logger.log("ERROR", "No markdown data entry files were generated. Cannot create PHP functions. Exiting.")
        sys.exit(1)

    # --- Step 4: Generate PHP Functions and Append to functions.php ---
    all_generated_php = []
    for page_name, md_path in markdown_files.items():
        php_code = generate_php_function(page_name, md_path)
        if php_code:
            all_generated_php.append(php_code)
        time.sleep(PROCESSING_DELAY)
        
    if all_generated_php:
        logger.log("FILE", "Appending generated PHP functions to functions.php...")
        
        page_function_calls = []
        for page_name in markdown_files.keys():
            function_name = f"kombee_get_{page_name.replace('-', '_')}_tasks"
            page_function_calls.append(f"    $master_task_list = array_merge($master_task_list, {function_name}());")
        
        master_system = f"""

/*
 * ===================================================================
 * =========== AUTO-GENERATED DATA ENTRY FUNCTIONS ===========
 * The following code was generated by the automation script.
 * ===================================================================
 */

/**
 * =================================================================================
 * Part 1: The Main Trigger - Collects tasks from all pages and schedules the job.
 * =================================================================================
 */
add_action('admin_init', 'kombee_schedule_initial_import');

function kombee_schedule_initial_import() {{
    if (get_option('kombee_import_complete') || get_option('kombee_import_status') === 'in_progress') {{
        return;
    }}

    $master_task_list = [];
{chr(10).join(page_function_calls)}

    if (empty($master_task_list)) {{
        return;
    }}

    update_option('kombee_import_tasks', $master_task_list);
    update_option('kombee_import_status', 'in_progress');
    update_option('kombee_import_progress', 0);
    update_option('kombee_import_total', count($master_task_list));

    if (!wp_next_scheduled('kombee_import_cron_hook')) {{
        wp_schedule_single_event(time(), 'kombee_import_cron_hook');
    }}
}}

/**
 * =================================================================================
 * Part 2: Task Generators - One function per page/section to define its tasks.
 * =================================================================================
 */

{chr(10).join(all_generated_php)}

/**
 * =================================================================================
 * Part 3: The Worker - Processes one task from the queue via WP-Cron.
 * =================================================================================
 */
add_action('kombee_import_cron_hook', 'kombee_process_import_queue');
function kombee_process_import_queue() {{
    $tasks    = get_option('kombee_import_tasks', []);
    $progress = get_option('kombee_import_progress', 0);
    $total    = get_option('kombee_import_total', 0);

    if (empty($tasks) || $progress >= $total) {{
        kombee_import_cleanup();
        return;
    }}

    $task = $tasks[$progress];

    switch ($task['type']) {{
        case 'sideload_and_update_field':
            $image_id = kombee_upload_image_from_url($task['image_url']);
            if (!is_wp_error($image_id)) {{
                update_field($task['field_key'], $image_id, $task['post_id']);
            }}
            break;

        case 'update_repeater_with_images':
            $processed_rows = [];
            foreach ($task['rows'] as $row_data) {{
                $new_row = [];
                foreach ($row_data as $sub_field_key => $value) {{
                    if (isset($task['image_sub_field_keys']) && in_array($sub_field_key, $task['image_sub_field_keys'])) {{
                        $image_id = kombee_upload_image_from_url($value);
                        $new_row[$sub_field_key] = !is_wp_error($image_id) ? $image_id : '';
                    }} else {{
                        $new_row[$sub_field_key] = $value;
                    }}
                }}
                $processed_rows[] = $new_row;
            }}
            update_field($task['field_key'], $processed_rows, $task['post_id']);
            break;
            
        case 'update_field':
            update_field($task['field_key'], $task['value'], $task['post_id']);
            break;
    }}

    update_option('kombee_import_progress', $progress + 1);
    
    if (($progress + 1) < $total) {{
        wp_schedule_single_event(time() + 2, 'kombee_import_cron_hook');
    }} else {{
        kombee_import_cleanup();
    }}
}}

/**
 * =================================================================================
 * Part 4: The Notifier & Helpers - UI, image upload, and cleanup.
 * =================================================================================
 */
add_action('admin_notices', 'kombee_import_progress_notice');
function kombee_import_progress_notice() {{
    if (get_option('kombee_import_status') !== 'in_progress') return;

    $progress = get_option('kombee_import_progress', 0);
    $total    = get_option('kombee_import_total', 1);
    $tasks    = get_option('kombee_import_tasks', []);
    $percent  = ($total > 0) ? round(($progress / $total) * 100) : 0;
    $current_task_description = isset($tasks[$progress]['description']) ? esc_html($tasks[$progress]['description']) : 'Processing...';
    ?>
    <div class="notice notice-info is-dismissible">
        <h3>Theme Data Setup in Progress</h3>
        <p>Setting up initial theme data in the background. This may take several minutes. You can refresh this page periodically to see updates.</p>
        <div style="width: 100%; background-color: #e0e0e0; border-radius: 4px; overflow: hidden;">
            <div style="width: <?php echo $percent; ?>%; background-color: #0073aa; color: white; text-align: center; line-height: 24px; height: 24px; transition: width 0.5s ease-in-out;">
                <?php echo $percent; ?>%
            </div>
        </div>
        <p><strong>Step <?php echo (int)$progress + 1; ?> of <?php echo (int)$total; ?>:</strong> <?php echo $current_task_description; ?></p>
    </div>
    <script>
        setTimeout(function() {{
           if (document.querySelector('.notice-info h3') && document.querySelector('.notice-info h3').innerText.includes('Theme Data Setup')) {{
               window.location.reload();
           }}
        }}, 10000); // Refresh every 10 seconds
    </script>
    <?php
}}

function kombee_upload_image_from_url($image_url) {{
    if (!function_exists('media_sideload_image')) {{
        require_once(ABSPATH . 'wp-admin/includes/image.php');
        require_once(ABSPATH . 'wp-admin/includes/file.php');
        require_once(ABSPATH . 'wp-admin/includes/media.php');
    }}
    return media_sideload_image($image_url, 0, null, 'id');
}}

function kombee_import_cleanup() {{
    update_option('kombee_import_complete', '1');
    delete_option('kombee_import_status');
    delete_option('kombee_import_tasks');
    delete_option('kombee_import_progress');
    delete_option('kombee_import_total');
    wp_clear_scheduled_hook('kombee_import_cron_hook');
    error_log('Kombee initial data import finished successfully.');
}}
"""
        
        with open(functions_php_path, 'a', encoding='utf-8') as f:
            f.write(master_system)
        
        logger.log("SUCCESS", "Complete task-based data entry system has been appended to functions.php.")
    else:
        logger.log("WARNING", "No PHP functions were generated to append.")

    end_time = time.time()
    total_time = end_time - start_time
    logger.log("TIMER", f"Script finished in {total_time:.2f} seconds.")


if __name__ == "__main__":
    main()