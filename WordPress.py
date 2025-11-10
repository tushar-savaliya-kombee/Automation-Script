import os
import re
import time
import logging
import shutil
import concurrent.futures
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai
from git import Repo, GitCommandError

# --- 1. CONFIGURATION & SETUP ---

# Global variables for tracking
start_time = None
total_tokens_used = 0
processed_files_count = 0

class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors and icons for different log levels."""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[32m',     # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',    # Red
        'CRITICAL': '\033[35m', # Magenta
        'RESET': '\033[0m'      # Reset
    }
    
    # Icons for different log levels
    ICONS = {
        'DEBUG': '🔍',
        'INFO': '✅',
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '🚨'
    }
    
    def format(self, record):
        # Get color and icon for the log level
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        icon = self.ICONS.get(record.levelname, '📝')
        reset = self.COLORS['RESET']
        
        # Format timestamp
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        # Create formatted message
        formatted_msg = f"{color}{icon} [{timestamp}] [{record.levelname}]{reset} - {record.getMessage()}"
        
        return formatted_msg

class FileFormatter(logging.Formatter):
    """Formatter for file output without colors but with icons."""
    
    ICONS = {
        'DEBUG': '🔍',
        'INFO': '✅',
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '🚨'
    }
    
    def format(self, record):
        icon = self.ICONS.get(record.levelname, '📝')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return f"{icon} [{timestamp}] [{record.levelname}] - {record.getMessage()}"

def _get_unique_path_with_increment(base_path_str):
    """Generates a unique path by appending an incrementing number if the path already exists."""
    path = Path(base_path_str)
    if not path.exists():
        return path.as_posix() # Return as string for consistency with os.getenv

    parent = path.parent
    stem = path.stem
    suffix = path.suffix

    i = 1
    while True:
        new_path_str = f"{parent / stem}-{i}{suffix}"
        new_path = Path(new_path_str)
        if not new_path.exists():
            return new_path.as_posix()
        i += 1

def load_environment():
    """Loads environment variables from .env file and validates them."""
    load_dotenv()
    config = {
        "WEB_PROJECT_PATH": os.getenv("WEB_PROJECT_PATH"),
        "boilerplate_repo": os.getenv("BOILERPLATE_REPO_URL"),
        "wp_theme_folder": os.getenv("WP_THEME_OUTPUT_FOLDER", "generated-wp-theme"),
        "gemini_api_key": os.getenv("GEMINI_API_KEY"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "processing_delay": int(os.getenv("PROCESSING_DELAY", 4)),
        "max_workers": int(os.getenv("MAX_WORKERS", 3)),
    }
    if not all([config["WEB_PROJECT_PATH"], config["boilerplate_repo"], config["gemini_api_key"]]):
        raise ValueError("Error: WEB_PROJECT_PATH, BOILERPLATE_REPO_URL, and GEMINI_API_KEY must be set in the .env file.")
    
    # Generate a unique WordPress theme output folder
    config["wp_theme_folder"] = _get_unique_path_with_increment(config["wp_theme_folder"])
    
    return config

def setup_logging(project_name, wp_theme_folder_name):
    """Sets up enhanced logging to file and console with colors and icons."""
    global start_time
    start_time = datetime.now()
    
    # Use the unique theme folder name to create a unique log file name
    log_filename = f"Log_For_{wp_theme_folder_name}.txt"
    if os.path.exists(log_filename):
        os.remove(log_filename) # Start with a fresh log file for each run

    # Clear any existing handlers
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    # Create logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter())
    logger.addHandler(console_handler)
    
    # File handler without colors
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setFormatter(FileFormatter())
    logger.addHandler(file_handler)
    
    # Log startup banner
    logging.info("=" * 80)
    logging.info("🚀 WordPress Theme Generator Started")
    logging.info(f"📅 Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"📁 Project: {project_name}")
    logging.info("=" * 80)

def clone_boilerplate(repo_url, dest_dir):
    """Clones or pulls the WordPress boilerplate theme."""
    logging.info(f"📥 Setting up WordPress boilerplate in '{dest_dir}'...")
    # No need to check os.path.exists(dest_dir) or remove it here,
    # as _get_unique_path_with_increment ensures dest_dir is unique.

    try:
        Repo.clone_from(repo_url, dest_dir)
        logging.info(f"📦 Successfully cloned boilerplate from {repo_url}")
        return True
    except GitCommandError as e:
        logging.error(f"🔧 Git command failed: {e}")
        return False
    except Exception as e:
        logging.error(f"💥 An unexpected error occurred during cloning: {e}")
        return False

def find_html_files(WEB_PROJECT_PATH):
    """Finds all HTML files, prioritizing header and footer."""
    src_path = Path(WEB_PROJECT_PATH) / "src"
    if not src_path.exists():
        logging.error(f"📂 Source directory not found: {src_path}")
        return []

    all_files = list(src_path.glob('**/*.html'))
    prioritized_files = []
    other_files = []

    # Separate header and footer
    header_path = src_path / "components" / "header.html"
    footer_path = src_path / "components" / "footer.html"

    if header_path in all_files:
        prioritized_files.append(header_path)
        all_files.remove(header_path)
    if footer_path in all_files:
        prioritized_files.append(footer_path)
        all_files.remove(footer_path)

    # The rest of the files in the src root
    other_files = [f for f in all_files if f.parent == src_path]

    # Combine lists
    final_list = prioritized_files + sorted(other_files)
    logging.info(f"🔍 Found {len(final_list)} HTML files to process: {[f.name for f in final_list]}")
    return final_list

# --- 2. AI INTERACTION ---

def detect_alternating_layout(html_content):
    """Detects if the HTML likely contains an alternating layout using Tailwind order classes."""
    # This regex looks for class attributes containing "order-[number]" with optional responsive prefixes.
    pattern = r'class="[^"]*\s(sm:|md:|lg:|xl:)?order-\d'
    if re.search(pattern, html_content):
        logging.info("🕵️ Alternating layout pattern detected. Adding specific instructions to the prompt.")
        return True
    return False

def extract_html_sections(html_content):
    """Extracts HTML sections marked with comments like <!-- START: section-name --> and <!-- END: section-name -->"""
    sections = {}
    # Pattern to match section comments and capture content between them
    # Updated pattern to be more flexible with section names
    pattern = r'<!--\s*START:\s*([^>]+?)\s*-->(.*?)<!--\s*END:\s*\1\s*-->'
    matches = re.findall(pattern, html_content, re.DOTALL | re.IGNORECASE)
    
    for section_name, section_content in matches:
        sections[section_name.strip()] = section_content.strip()
    
    return sections

def get_gemini_prompt(html_content, filename):
    """Generates the detailed prompt for the Gemini API, dynamically adding instructions if needed."""
    
    # Extract sections from HTML
    sections = extract_html_sections(html_content)
    section_info = ""
    if sections:
        section_names = list(sections.keys())
        section_info = f"""
    **SECTION PRESERVATION:**
    The HTML contains the following marked sections: {', '.join(section_names)}
    You MUST preserve these section markers in your PHP output by wrapping each converted section with:
    <!-- START: section-name --> and <!-- END: section-name -->
    
    For example, if you find <!-- START: Hero-section --> in the HTML, wrap the converted PHP code for that section with:
    <!-- START: Hero-section -->
    [your generated PHP code for this section]
    <!-- END: Hero-section -->
    """
    
    # Base prompt for standard page templates
    base_prompt = f"""
    You are an expert WordPress developer specializing in creating dynamic templates using the Advanced Custom Fields (ACF) plugin.
    Your task is to convert the provided static HTML code into a dynamic WordPress PHP template file and provide a corresponding ACF field structure.

    **CRITICAL INSTRUCTIONS:**
    1.  Analyze the provided HTML for the file `{filename}`.
    2.  Identify all static content (text, images, links, lists, repeaters) that should be manageable from the WordPress admin panel.
    3.  **MANDATORY: Prefix Custom PHP Functions:** All custom PHP functions you generate (e.g., `get_image_tag` or `custom_helper_function`) MUST be prefixed with a unique identifier like `yourthemename_` (e.g., `yourthemename_get_image_tag`). This prevents naming conflicts with WordPress core functions or other plugins.
    4.  **MANDATORY IMAGE ATTRIBUTES:** For every `<img>` tag, you MUST include `loading="lazy"` and set the `title` attribute to the same value as the `alt` attribute. Example: `<img src="..." alt="Description" title="Description" loading="lazy">`.
    5.  **MANDATORY LINK ATTRIBUTES:** For every `<a>` tag, you MUST include a `title` attribute. When using ACF Link fields, the `title` attribute should be populated with the 'title' value from the ACF Link Array. Example: `<a href="{{url}}" title="{{title}}" target="{{target}}">{{text}}</a>`.
    6.  **OPTIMIZATION CRITICAL: Fetch ALL page data at once at the beginning of the PHP file using `$page_fields = get_fields(get_the_ID());` and then access all fields via this array (e.g., `$page_fields['your_field_name']`). For repeater fields, use standard `foreach` loops on the `$page_fields['your_repeater_field']` array. DO NOT use `have_rows()` and `the_row()` for repeaters.**        
        **IMPORTANT: Use `echo wp_kses_post()` for outputting content from WYSIWYG or Text Area fields to allow for HTML tags. Use `esc_html()` or `esc_url()` for simple text fields or URLs for better security. For image fields, ALWAYS include a check for `is_array()` to ensure proper handling of return formats.**
        **Example for repeater fields:**
        ```php
        <?php
        $repeater_items = $page_fields['your_repeater_field'] ?? [];
        if (!empty($repeater_items)) :
            foreach ($repeater_items as $item) :
                $sub_field_value = esc_html($item['your_sub_field'] ?? '');
                // ... use $sub_field_value ...
            endforeach;
        endif;
        ?>
        ```
    7.  Generate a clear, text-based ACF field structure that a user can follow to create the fields in the ACF plugin UI. Use field types like Text, Text Area, Image, Gallery, Repeater, Group, Link, and True/False where appropriate. Structure it with Tabs for better organization in the WP admin.
    8.  **CRITICAL CONDITIONAL RENDERING:** Each section of the generated PHP code MUST be wrapped in a PHP `if` condition that checks for the existence of its essential ACF data before rendering. For example, if a section's primary content is driven by a repeater field named `hero_slides`, the section should be wrapped like this:
        ```php
        <?php if (!empty($page_fields['hero_slides'])) : // Check if essential data exists ?>
            <!-- START: Hero-section -->
            <!-- Your section PHP code here -->
            <!-- END: Hero-section -->
        <?php endif; ?>
        ```
        Apply this conditional logic to ALL sections.
    {section_info}"""

    # Dynamically add instructions for alternating layouts if detected
    if filename not in ["header.html", "footer.html"] and detect_alternating_layout(html_content):
        alternating_layout_instructions = """
    7.  **ALTERNATING LAYOUT DETECTED**: For sections that alternate (e.g., image-left, then image-right), you MUST implement the PHP logic correctly inside the repeater loop. Use a counter variable, check if it's even (`$counter % 2 == 0`), and conditionally apply Tailwind CSS classes like `md:order-1` and `md:order-2` to reverse the column order. **Follow this correct example pattern, using the `$page_fields` array and `foreach` loop for repeaters:**
        ```php
        <?php
        $item_index = 0;
        $repeater_items = $page_fields['your_repeater_field'] ?? [];
        if (!empty($repeater_items)) :
            foreach ($repeater_items as $item) :
                $item_index++;
                $is_even = ($item_index % 2 == 0);
        ?>
            <div class="flex flex-col md:flex-row">
                <div class="md:w-1/2 <?php if ($is_even) echo 'md:order-2'; ?>">
                    <?php // Image Column Content (e.g., esc_url($item['image_field']['url'] ?? '')) ?>
                </div>
                <div class="md:w-1/2 <?php if ($is_even) echo 'md:order-1'; ?>">
                    <?php // Text Column Content (e.g., esc_html($item['text_field'] ?? '')) ?>
                </div>
            </div>
        <?php
            endforeach;
        endif;
        ?>
        ```
    8.  The final output MUST be in the following format with the exact delimiters. DO NOT add any other text or explanations outside of these blocks.
        """
        base_prompt += alternating_layout_instructions
    else:
        # Add the standard closing instruction if the special one isn't used
        base_prompt += """
    9.  The final output MUST be in the following format with the exact delimiters. DO NOT add any other text or explanations outside of these blocks.
        """
    
    # Final structure for a standard page template
    prompt = base_prompt + f"""
    [ACF_STRUCTURE_START]
    **ACF Field Group Setup for: {filename}**

    1.  Create a new Field Group named "**Page Content: {filename.replace('.html', '').replace('-', ' ').title()}**".
    2.  Set the **Location Rules** to show this field group if **Page Template** is equal to **(Your Template Name)**.
    3.  Add the following fields, organized by tabs:

    *   **Tab: Section Name 1**
        *   `field_name_1` (Field Type) - Description or example.
        *   `field_name_2` (Group)
            *   `nested_field_1` (Text)
    *   **Tab: Section Name 2 (if it's a Repeater)**
        *   `repeater_field_name` (Repeater)
            *   `repeater_sub_field_1` (Image)
            *   `repeater_sub_field_2` (Text) - If an icon is used, strictly follow the format "fa fa-iconname" (e.g., `fa fa-running`, `fa fa-dumbbell`, `fa fa-biking`).

    (Continue this structure for all dynamic parts of the page)
    [ACF_STRUCTURE_END]

    [PHP_CODE_START]
    ```php
    <?php
    /**
     * Template Name: {filename.replace('.html', '').replace('-', ' ').title()} Page
     */
    
    // Your generated PHP code goes here.
    // Ensure you include get_header(); and get_footer(); for page templates.
    // Do not include them for header.php or footer.php conversions.
    ?>
    ```
    [PHP_CODE_END]

    ---
    HERE IS THE HTML CONTENT TO CONVERT:
    ---
    {html_content}
    """
    
    if filename == "header.html":
        # Extract sections for header
        sections = extract_html_sections(html_content)
        section_info = ""
        if sections:
            section_names = list(sections.keys())
            section_info = f"""
    **SECTION PRESERVATION:**
    The HTML contains the following marked sections: {', '.join(section_names)}
    You MUST preserve these section markers in your PHP output by wrapping each converted section with:
    <!-- START: section-name --> and <!-- END: section-name -->
    """
        
        prompt = f"""
    You are an expert WordPress developer specializing in creating dynamic templates using the Advanced Custom Fields (ACF) plugin.
    Your task is to convert the provided static HTML code into dynamic WordPress PHP code that will be inserted into an existing `header.php` file.

    **CRITICAL INSTRUCTIONS:**
    1.  Analyze the provided HTML for the file `{filename}`.
    2.  Identify all static content (text, images, links, lists, repeaters) that should be manageable from the WordPress admin panel.
    3.  **MANDATORY: Prefix Custom PHP Functions:** All custom PHP functions you generate (e.g., `get_image_tag` or `custom_helper_function`) MUST be prefixed with a unique identifier like `yourthemename_` (e.g., `yourthemename_get_image_tag`). This prevents naming conflicts with WordPress core functions or other plugins.
    4.  **MANDATORY IMAGE ATTRIBUTES:** For every `<img>` tag, you MUST include `loading="lazy"` and set the `title` attribute to the same value as the `alt` attribute. Example: `<img src="..." alt="Description" title="Description" loading="lazy">`.
    5.  **MANDATORY LINK ATTRIBUTES:** For every `<a>` tag, you MUST include a `title` attribute. When using ACF Link fields, the `title` attribute should be populated with the 'title' value from the ACF Link Array. Example: `<a href="{{url}}" title="{{title}}" target="{{target}}">{{text}}</a>`.
    6.  **OPTIMIZATION CRITICAL: Fetch ALL header data at once at the beginning of the PHP file using `$header_fields = get_fields('option');` and then access all fields via this array (e.g., `$header_fields['your_field_name']`). For repeater fields, use standard `foreach` loops on the `$header_fields['your_repeater_field']` array. DO NOT use `have_rows()` and `the_row()` for repeaters.**
        **IMPORTANT: Use `echo wp_kses_post()` for outputting content from WYSIWYG or Text Area fields to allow for HTML tags. Use `esc_html()` or `esc_url()` for simple text fields or URLs for better security. For image fields, ALWAYS include a check for `is_array()` to ensure proper handling of return formats.**
        
        **HEADER TAG INSTRUCTIONS - VERY IMPORTANT:**
        - DO NOT generate the opening `<header>` tag or any of its attributes (like class="main-header sticky...")
        - The boilerplate already has: `<header class="main-header sticky top-0 z-50 w-full bg-white shadow-sm">`
        - Your generated code will be inserted INSIDE this existing header tag
        - Start directly with the content inside the header (e.g., container divs, navigation, etc.)
        - DO NOT include closing `</header>` tag
        
        **NAVIGATION MENU INSTRUCTIONS - CRITICAL:**
        - For navigation menus, use WordPress's native menu system with `wp_nav_menu()` function
        - DO NOT use ACF Repeater fields for navigation items
        - Use an ACF Select field to let users choose which WordPress menu to display
        - **IMPORTANT: When generating the `wp_nav_menu()` function, DO NOT include the following parameters: `'container' => false`, `'items_wrap' => '%3$s'`, and `'depth' => 1`. These are handled by the boilerplate.**
        - Example implementation:
        ```php
        <?php
        $menu_id = get_field('select_navigation_menu', 'option');
        if ($menu_id) {{
            wp_nav_menu([
                'menu' => $menu_id,
                'menu_class' => 'flex gap-6', // Adjust classes to match the HTML structure
                'container' => false,
            ]);
        }}
        ?>
        ```
        - Adapt the `menu_class` and other parameters to match the styling from the original HTML
        - The menu will be managed through WordPress's Appearance > Menus interface
        
        The generated PHP code should be self-contained and ready to be inserted between `<!-- Start Header Content -->` and `<!-- End Header Content -->` comments in an existing `header.php` file.
        DO NOT include `get_header();`, `get_footer();`, `<!DOCTYPE html>`, `<html>`, `<head>`, `<body>` tags, or any other surrounding HTML/PHP boilerplate. Only the dynamic content that goes INSIDE the existing header tag.
    7.  Generate a clear, text-based ACF field structure that a user can follow to create the fields in the ACF plugin UI. Use field types like Text, Text Area, Image, Gallery, Select, Group, Link, and True/False where appropriate. Structure it with Tabs for better organization in the WP admin.
    8.  **CRITICAL CONDITIONAL RENDERING:** For the header, the conditional logic should check for essential data at the top-most level. For example, if the header's primary content is driven by a logo image field named `header_logo` or a navigation menu select field named `select_navigation_menu`, the entire header content should be wrapped like this:
        ```php
        <?php if (!empty($header_fields['header_logo']) || !empty($header_fields['select_navigation_menu'])) : // Check if essential header data exists ?>
            <!-- Start Header Content -->
            <!-- Your header PHP code here -->
            <!-- End Header Content -->
        <?php endif; ?>
        ```
        Apply this conditional logic to the entire header section.
    {section_info}5.  The final output MUST be in the following format with the exact delimiters. DO NOT add any other text or explanations outside of these blocks.

    [ACF_STRUCTURE_START]
    **ACF Field Group Setup for: {filename}**

    1.  Create a new Field Group named "**Header Content**".
    2.  Set the **Location Rules** to show this field group if **Options Page** is equal to **Header Settings** (You might need to create an ACF Options Page for Header Settings).
    3.  Add the following fields, organized by tabs:

    *   **Tab: Header General**
        *   `header_logo` (Image) - Main logo for the header.
            - Return Format: Link Array
            - Instructions: "Ensure this field returns a Link Array (url, title, target)."
        *   `header_logo_link` (Link) - Link for the header logo.
            - Return Format: Link Array
            - Instructions: "Ensure this field returns a Link Array (url, title, target)."
        *   `cta_button_text` (Text) - Text for the Call-to-Action button.
        *   `cta_button_link` (Link) - Link for the Call-to-Action button.
    *   **Tab: Navigation Menu**
        *   `select_navigation_menu` (Select) - Select a WordPress navigation menu to display in the header.
            - Field Type: Select
            - Instructions: "Select a WordPress navigation menu to display in the header. Menus can be created and managed in Appearance > Menus."
            - Choices: Will be dynamically populated with available WordPress menus
            - Return Format: Menu ID (value)
            - Allow Null: Yes
            - UI: Yes
            - Note: This field should be configured to fetch WordPress menus dynamically using the `acf/load_field` filter or manually populated with menu IDs.

    [ACF_STRUCTURE_END]

    [PHP_CODE_START]
    ```php
    <?php
    // Your generated PHP code goes here.
    // Remember: DO NOT include the <header> opening tag - it already exists in the boilerplate
    // Start directly with the content that goes inside the header tag
    // Use wp_nav_menu() for navigation instead of ACF repeaters
    ?>
    ```
    [PHP_CODE_END]

    ---
    HERE IS THE HTML CONTENT TO CONVERT:
    ---
    {html_content}
    """
    elif filename == "footer.html":
        # Extract sections for footer
        sections = extract_html_sections(html_content)
        section_info = ""
        if sections:
            section_names = list(sections.keys())
            section_info = f"""
    **SECTION PRESERVATION:**
    The HTML contains the following marked sections: {', '.join(section_names)}
    You MUST preserve these section markers in your PHP output by wrapping each converted section with:
    <!-- START: section-name --> and <!-- END: section-name -->
    """
        
        prompt = f"""
    You are an expert WordPress developer specializing in creating dynamic templates using the Advanced Custom Fields (ACF) plugin.
    Your task is to convert the provided static HTML code into dynamic WordPress PHP code that will be inserted into an existing `footer.php` file.

    **CRITICAL INSTRUCTIONS:**
    1.  Analyze the provided HTML for the file `{filename}`.
    2.  Identify all static content (text, images, links, lists, repeaters) that should be manageable from the WordPress admin panel.
    3.  **MANDATORY: Prefix Custom PHP Functions:** All custom PHP functions you generate (e.g., `get_image_tag` or `custom_helper_function`) MUST be prefixed with a unique identifier like `yourthemename_` (e.g., `yourthemename_get_image_tag`). This prevents naming conflicts with WordPress core functions or other plugins.
    4.  **MANDATORY IMAGE ATTRIBUTES:** For every `<img>` tag, you MUST include `loading="lazy"` and set the `title` attribute to the same value as the `alt` attribute. Example: `<img src="..." alt="Description" title="Description" loading="lazy">`.
    5.  **MANDATORY LINK ATTRIBUTES:** For every `<a>` tag, you MUST include a `title` attribute. When using ACF Link fields, the `title` attribute should be populated with the 'title' value from the ACF Link Array. Example: `<a href="{{url}}" title="{{title}}" target="{{target}}">{{text}}</a>`.
    6.  **OPTIMIZATION CRITICAL: Fetch ALL header data at once at the beginning of the PHP file using `$header_fields = get_fields('option');` and then access all fields via this array (e.g., `$header_fields['your_field_name']`). For repeater fields, use standard `foreach` loops on the `$header_fields['your_repeater_field']` array. DO NOT use `have_rows()` and `the_row()` for repeaters.**     
        **IMPORTANT: Use `echo wp_kses_post()` for outputting content from WYSIWYG or Text Area fields to allow for HTML tags. Use `esc_html()` or `esc_url()` for simple text fields or URLs for better security. For image fields, ALWAYS include a check for `is_array()` to ensure proper handling of return formats.**
        
        **NAVIGATION MENU INSTRUCTIONS - CRITICAL:**
        - If the footer contains navigation menus, use WordPress's native menu system with `wp_nav_menu()` function
        - DO NOT use ACF Repeater fields for navigation/menu items
        - Use an ACF Select field to let users choose which WordPress menu to display
        - Example implementation:
        ```php
        <?php
        $footer_menu_id = get_field('select_footer_menu', 'option');
        if ($footer_menu_id) {{
            wp_nav_menu([
                'menu' => $footer_menu_id,
                'menu_class' => 'footer-menu flex gap-4', // Adjust classes to match the HTML structure
                'container' => false,
            ]);
        }}
        ?>
        ```
        - Adapt the `menu_class` and other parameters to match the styling from the original HTML
        - The menu will be managed through WordPress's Appearance > Menus interface
        
        The generated PHP code should be self-contained and ready to be inserted between `<!-- Start Footer -->` and `<!-- End Footer -->` comments in an existing `footer.php` file.
        DO NOT include `get_header();`, `get_footer();`, `<!DOCTYPE html>`, `<html>`, `<head>`, `<body>` tags, or any other surrounding HTML/PHP boilerplate. Only the dynamic content.
    6.  Generate a clear, text-based ACF field structure that a user can follow to create the fields in the ACF plugin UI. Use field types like Text, Text Area, Image, Gallery, Select, Group, Link, and True/False where appropriate. Structure it with Tabs for better organization in the WP admin.
    7.  **CRITICAL CONDITIONAL RENDERING:** For the footer, the conditional logic should check for essential data at the top-most level. For example, if the footer's primary content is driven by a logo image field named `footer_logo` or a copyright text field named `copyright_text`, the entire footer content should be wrapped like this:
        ```php
        <?php if (!empty($footer_fields['footer_logo']) || !empty($footer_fields['copyright_text'])) : // Check if essential footer data exists ?>
            <!-- Start Footer -->
            <!-- Your footer PHP code here -->
            <!-- End Footer -->
        <?php endif; ?>
        ```
        Apply this conditional logic to the entire footer section.
    {section_info}7.  The final output MUST be in the following format with the exact delimiters. DO NOT add any other text or explanations outside of these blocks.

    [ACF_STRUCTURE_START]
    **ACF Field Group Setup for: {filename}**

    1.  Create a new Field Group named "**Footer Content**".
    2.  Set the **Location Rules** to show this field group if **Options Page** is equal to **Footer Settings** (You might need to create an ACF Options Page for Footer Settings).
    3.  Add the following fields, organized by tabs:

    *   **Tab: Footer General**
        *   `footer_logo` (Image) - Logo for the footer.
            - Return Format: Image Array
            - Instructions: "Ensure this field returns an Image Array (url, alt, ID)."
        *   `footer_description` (Text Area) - Short description for the footer.
    *   **Tab: Footer Navigation** (if applicable)
        *   `select_footer_menu` (Select) - Select a WordPress navigation menu to display in the footer.
            - Field Type: Select
            - Instructions: "Select a WordPress navigation menu to display in the footer. Menus can be created and managed in Appearance > Menus."
            - Choices: Will be dynamically populated with available WordPress menus
            - Return Format: Menu ID (value)
            - Allow Null: Yes
            - UI: Yes
            - Note: This field should be configured to fetch WordPress menus dynamically using the `acf/load_field` filter or manually populated with menu IDs.
    *   **Tab: Social Links**
        *   `social_media_links` (Repeater)
            *   `icon_class` (Text) - Font Awesome class for the icon (e.g., `fab fa-facebook-f`). Strictly follow the format "fa fa-iconname" (e.g., `fa fa-running`, `fa fa-dumbbell`, `fa fa-biking`).
            *   `link_url` (Link) - URL for the social media link.
                - Return Format: Link Array
                - Instructions: "Ensure this field returns a Link Array (url, title, target)."
    *   **Tab: Copyright Info**
        *   `copyright_text` (Text) - Copyright text.

    [ACF_STRUCTURE_END]

    [PHP_CODE_START]
    ```php
    <?php
    // Your generated PHP code goes here.
    // It will be inserted into the footer.php file.
    // Use wp_nav_menu() for navigation menus instead of ACF repeaters
    ?>
    ```
    [PHP_CODE_END]

    ---
    HERE IS THE HTML CONTENT TO CONVERT:
    ---
    {html_content}
    """

    return prompt

def call_gemini_api(html_content, file_name, config):
    """Calls the Gemini API and returns the response with token tracking."""
    global total_tokens_used
    
    logging.info(f"🤖 Contacting Gemini API for '{file_name}'...")
    try:
        genai.configure(api_key=config["gemini_api_key"])
        model = genai.GenerativeModel(config["gemini_model"])
        prompt = get_gemini_prompt(html_content, file_name)
        logging.debug(f"📝 Gemini Prompt for '{file_name}':\n{prompt}")
        
        response = model.generate_content(prompt)
        
        # Track token usage if available
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
            completion_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
            total_file_tokens = prompt_tokens + completion_tokens
            total_tokens_used += total_file_tokens
            
            logging.info(f"📊 Token usage for '{file_name}': {prompt_tokens} prompt + {completion_tokens} completion = {total_file_tokens} total")
        else:
            logging.info(f"📊 Token usage data not available for '{file_name}'")
        
        logging.info(f"✨ Successfully received response from Gemini for '{file_name}'")
        return response.text
    except Exception as e:
        logging.error(f"💥 An error occurred while calling Gemini API for '{file_name}': {e}")
        return None

def parse_gemini_response(response_text):
    """Parses the Gemini response to extract ACF structure and PHP code."""
    try:
        acf_structure_match = re.search(r'\[ACF_STRUCTURE_START\](.*?)\[ACF_STRUCTURE_END\]', response_text, re.DOTALL)
        php_code_match = re.search(r'\[PHP_CODE_START\]\s*```php\s*(.*?)```\s*\[PHP_CODE_END\]', response_text, re.DOTALL)

        acf_structure = acf_structure_match.group(1).strip() if acf_structure_match else ""
        php_code = php_code_match.group(1).strip() if php_code_match else ""

        if not acf_structure or not php_code:
            logging.warning("⚠️ Could not parse the response from Gemini correctly. Delimiters might be missing.")
            logging.debug(f"📄 Full Gemini Response:\n{response_text}")
            return None, None

        return acf_structure, php_code
    except Exception as e:
        logging.error(f"💥 Failed to parse Gemini response: {e}")
        return None, None

# --- 3. FILE PROCESSING & SAVING ---

def sanitize_filename(name):
    """Sanitizes a string to be a valid filename component."""
    name = name.lower()
    name = re.sub(r'[\s_]+', '-', name)
    name = re.sub(r'[^\w\-.]', '', name)
    return name

def process_html_file(html_path, config, wp_theme_path):
    """The main function executed by each thread to process one HTML file."""
    global processed_files_count
    
    try:
        file_name = html_path.name
        logging.info(f"📄 Starting processing for: {file_name}")
        logging.info("-" * 60)

        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # Log detected sections
        sections = extract_html_sections(html_content)
        if sections:
            section_names = list(sections.keys())
            logging.info(f"📋 Detected {len(sections)} sections in {file_name}: {', '.join(section_names)}")
            logging.info(f"🔧 Generated PHP will preserve section markers for easy identification")
        else:
            logging.info(f"📋 No section markers found in {file_name} - standard conversion will be applied")

        # Call AI
        response_text = call_gemini_api(html_content, file_name, config)
        if not response_text:
            return

        # Parse AI response
        acf_structure, php_code = parse_gemini_response(response_text)
        if not acf_structure or not php_code:
            logging.error(f"💥 Processing failed for {file_name} due to parsing error")
            return

        # Determine output paths
        acf_fields_dir = wp_theme_path / "ACF Fields"
        acf_fields_dir.mkdir(exist_ok=True)

        base_name = html_path.stem # e.g., 'index', 'aboutus'
        sanitized_name = sanitize_filename(base_name)

        if file_name == "header.html":
            php_target_path = wp_theme_path / "header.php"
            acf_target_path = acf_fields_dir / f"header-ACF-fields.txt"
        elif file_name == "footer.html":
            php_target_path = wp_theme_path / "footer.php"
            acf_target_path = acf_fields_dir / f"footer-ACF-fields.txt"
        else:
            template_dir = wp_theme_path / "template"
            template_dir.mkdir(exist_ok=True)
            # Special case for index.html -> template-home.php
            template_name = "home" if sanitized_name == "index" else sanitized_name
            php_target_path = template_dir / f"{template_name}.php"
            acf_target_path = acf_fields_dir / f"{template_name}-ACF-fields.txt"

        # Save the generated files
        logging.info(f"💾 Saving PHP file to: {php_target_path}")
        if file_name == "header.html" or file_name == "footer.html":
            # Read existing content
            with open(php_target_path, 'r', encoding='utf-8') as f:
                existing_php_content = f.read()

            # Define start and end markers
            if file_name == "header.html":
                start_marker = "<!-- Start Header Content -->"
                end_marker = "<!-- End Header Content -->"
            else:  # file_name == "footer.html"
                start_marker = "<!-- Start Footer -->"
                end_marker = "<!-- End Footer -->"

            # Replace content between markers
            # Using re.DOTALL to make '.' match newlines
            pattern = re.compile(f"({re.escape(start_marker)})(.*?)({re.escape(end_marker)})", re.DOTALL)
            
            # Insert the generated PHP code, ensuring it's wrapped in <?php ?> tags if not already
            if not php_code.strip().startswith("<?php"):
                php_code_to_insert = f"<?php\n{php_code}\n?>"
            else:
                php_code_to_insert = php_code

            new_php_content = pattern.sub(f"\\1\n{php_code_to_insert}\n\\3", existing_php_content)

            with open(php_target_path, 'w', encoding='utf-8') as f:
                f.write(new_php_content)
        else:
            with open(php_target_path, 'w', encoding='utf-8') as f:
                f.write(php_code)

        logging.info(f"📋 Saving ACF Structure to: {acf_target_path}")
        with open(acf_target_path, 'w', encoding='utf-8') as f:
            f.write(acf_structure)

        processed_files_count += 1
        logging.info(f"✅ Successfully processed: {file_name} ({processed_files_count} files completed)")

        # Apply delay
        logging.info(f"⏳ Waiting for {config['processing_delay']} seconds before next API call...")
        time.sleep(config['processing_delay'])

    except Exception as e:
        logging.exception(f"💥 An unexpected error occurred in thread for {html_path.name}: {e}")

# --- 4. MAIN ORCHESTRATOR ---

def log_completion_summary(project_name, config, files_count):
    """Logs the completion summary with timing and token usage."""
    global start_time, total_tokens_used, processed_files_count
    
    end_time = datetime.now()
    total_duration = end_time - start_time
    
    # Format duration
    hours, remainder = divmod(total_duration.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours > 0:
        duration_str = f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
    elif minutes > 0:
        duration_str = f"{int(minutes)}m {int(seconds)}s"
    else:
        duration_str = f"{int(seconds)}s"
    
    # Calculate average processing time per file
    avg_time_per_file = total_duration.total_seconds() / max(processed_files_count, 1)
    
    logging.info("=" * 80)
    logging.info("🎉 WORDPRESS THEME GENERATION COMPLETED!")
    logging.info("=" * 80)
    logging.info(f"📊 PROCESS SUMMARY:")
    logging.info(f"   📅 Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"   🏁 End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"   ⏱️ Total Duration: {duration_str}")
    logging.info(f"   📁 Project: {project_name}")
    logging.info(f"   📄 Files Found: {files_count}")
    logging.info(f"   ✅ Files Processed: {processed_files_count}")
    logging.info(f"   ⚡ Avg Time/File: {avg_time_per_file:.1f}s")
    logging.info("-" * 80)
    logging.info(f"🤖 GEMINI AI USAGE:")
    if total_tokens_used > 0:
        logging.info(f"   🎯 Total Tokens Used: {total_tokens_used:,}")
        logging.info(f"   📊 Avg Tokens/File: {total_tokens_used // max(processed_files_count, 1):,}")
        
        # Estimate cost (approximate - adjust based on current Gemini pricing)
        estimated_cost = (total_tokens_used / 1000) * 0.00075  # Rough estimate
        logging.info(f"   💰 Estimated Cost: ~${estimated_cost:.4f} USD")
    else:
        logging.info(f"   📊 Token usage data not available")
    logging.info("-" * 80)
    logging.info(f"📂 OUTPUT LOCATIONS:")
    logging.info(f"   🎨 WordPress Theme: '{config['wp_theme_folder']}'")
    log_file_name = f"generation_log_{Path(config['wp_theme_folder']).name}.txt"
    logging.info(f"   📋 Log File: '{log_file_name}'")
    logging.info("-" * 80)
    logging.info(f"🏷️ SECTION PRESERVATION:")
    logging.info(f"   📌 HTML sections marked with <!-- START: name --> and <!-- END: name -->")
    logging.info(f"   📌 are preserved in the generated PHP files for easy identification")
    logging.info("=" * 80)
    
    if processed_files_count == files_count:
        logging.info("🎊 All files processed successfully! Your WordPress theme is ready!")
    else:
        logging.warning(f"⚠️ Only {processed_files_count}/{files_count} files were processed successfully.")
    
    logging.info("=" * 80)

def main():
    """Main function to run the entire conversion process."""
    global start_time, total_tokens_used, processed_files_count
    
    try:
        config = load_environment()
        project_name = Path(config["WEB_PROJECT_PATH"]).name
        
        # Pass the unique wp_theme_folder name to setup_logging
        wp_theme_folder_name = Path(config["wp_theme_folder"]).name
        setup_logging(project_name, wp_theme_folder_name)

        logging.info(f"📂 Project Path: {config['WEB_PROJECT_PATH']}")
        logging.info(f"🔧 Gemini Model: {config['gemini_model']}")
        logging.info(f"👥 Max Workers: {config['max_workers']}")
        logging.info(f"⏱️ Processing Delay: {config['processing_delay']}s")

        wp_theme_path = Path(config["wp_theme_folder"])
        if not clone_boilerplate(config["boilerplate_repo"], wp_theme_path):
            logging.error("💥 Failed to setup boilerplate. Aborting.")
            return

        files_to_process = find_html_files(config["WEB_PROJECT_PATH"])
        if not files_to_process:
            logging.warning("⚠️ No HTML files found to process. Exiting.")
            return

        logging.info(f"🚀 Starting processing of {len(files_to_process)} files...")
        logging.info("=" * 80)

        with concurrent.futures.ThreadPoolExecutor(max_workers=config["max_workers"]) as executor:
            # Submit all tasks to the thread pool
            futures = [executor.submit(process_html_file, html_path, config, wp_theme_path) for html_path in files_to_process]
            # Wait for all tasks to complete
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()  # To raise exceptions if any occurred in the thread
                except Exception as e:
                    logging.error(f"💥 A thread raised an exception: {e}")
        
        # Log completion summary
        log_completion_summary(project_name, config, len(files_to_process))

    except ValueError as e:
        logging.error(f"⚙️ Configuration Error: {e}")
    except Exception as e:
        logging.error(f"💥 A critical error occurred in the main process: {e}")
        if start_time:
            end_time = datetime.now()
            duration = end_time - start_time
            logging.error(f"⏱️ Script ran for {duration} before failing")

if __name__ == "__main__":
    main()