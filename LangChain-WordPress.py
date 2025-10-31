import os
import re
import time
import logging
import shutil
import concurrent.futures
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
from git import Repo, GitCommandError

# LangChain imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
from langchain.schema import HumanMessage
from langchain.callbacks import get_openai_callback

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

def load_environment():
    """Loads environment variables from .env file and validates them."""
    load_dotenv()
    config = {
        "project_path": os.getenv("PROJECT_PATH"),
        "boilerplate_repo": os.getenv("BOILERPLATE_REPO_URL"),
        "wp_theme_folder": os.getenv("WP_THEME_OUTPUT_FOLDER", "generated-wp-theme"),
        "gemini_api_key": os.getenv("GEMINI_API_KEY"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp"),
        "processing_delay": int(os.getenv("PROCESSING_DELAY", 4)),
        "max_workers": int(os.getenv("MAX_WORKERS", 3)),
        "temperature": float(os.getenv("TEMPERATURE", 0.1)),
        "max_tokens": int(os.getenv("MAX_TOKENS", 8192)),
    }
    if not all([config["project_path"], config["boilerplate_repo"], config["gemini_api_key"]]):
        raise ValueError("Error: PROJECT_PATH, BOILERPLATE_REPO_URL, and GEMINI_API_KEY must be set in the .env file.")
    return config

def setup_logging(project_name):
    """Sets up enhanced logging to file and console with colors and icons."""
    global start_time
    start_time = datetime.now()
    
    log_filename = f"generation_log_{project_name}.txt"
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
    logging.info("🚀 WordPress Theme Generator Started (LangChain Version)")
    logging.info(f"📅 Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"📁 Project: {project_name}")
    logging.info("=" * 80)

def clone_boilerplate(repo_url, dest_dir):
    """Clones or pulls the WordPress boilerplate theme."""
    logging.info(f"🔄 Setting up WordPress boilerplate in '{dest_dir}'...")
    if os.path.exists(dest_dir):
        logging.warning(f"📂 Destination folder '{dest_dir}' already exists. Removing it for a fresh clone.")
        try:
            shutil.rmtree(dest_dir)
        except OSError as e:
            logging.error(f"🗑️ Error removing directory {dest_dir}: {e}")
            return False

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

def find_html_files(project_path):
    """Finds all HTML files, prioritizing header and footer."""
    src_path = Path(project_path) / "src"
    if not src_path.exists():
        logging.error(f"📁 Source directory not found: {src_path}")
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

# --- 2. LANGCHAIN AI INTERACTION ---

def setup_langchain_model(config):
    """Initialize LangChain ChatGoogleGenerativeAI model."""
    try:
        model = ChatGoogleGenerativeAI(
            model=config["gemini_model"],
            google_api_key=config["gemini_api_key"],
            temperature=config["temperature"],
            max_tokens=config["max_tokens"],
            convert_system_message_to_human=True
        )
        logging.info(f"🤖 LangChain model initialized: {config['gemini_model']}")
        return model
    except Exception as e:
        logging.error(f"💥 Failed to initialize LangChain model: {e}")
        return None

def create_prompt_templates():
    """Create LangChain prompt templates for different file types."""
    
    # General template for regular pages
    general_template = PromptTemplate(
        input_variables=["filename", "html_content"],
        template="""
You are an expert WordPress developer specializing in creating dynamic templates using the Advanced Custom Fields (ACF) plugin.
Your task is to convert the provided static HTML code into a dynamic WordPress PHP template file and provide a corresponding ACF field structure.

**CRITICAL INSTRUCTIONS:**
1.  Analyze the provided HTML for the file `{filename}`.
2.  Identify all static content (text, images, links, lists, repeaters) that should be manageable from the WordPress admin panel.
3.  Generate the PHP code that uses ACF functions (like `get_field()`, `the_field()`, `have_rows()`, `the_row()`, `get_sub_field()`) to display this dynamic content.
4.  Generate a clear, text-based ACF field structure that a user can follow to create the fields in the ACF plugin UI. Use field types like Text, Text Area, Image, Gallery, Repeater, Group, Link, and True/False where appropriate. Structure it with Tabs for better organization in the WP admin.
5.  The final output MUST be in the following format with the exact delimiters. DO NOT add any other text or explanations outside of these blocks.

[ACF_STRUCTURE_START]
**ACF Field Group Setup for: {filename}**

1.  Create a new Field Group named "**Page Content: {filename}**".
2.  Set the **Location Rules** to show this field group if **Page Template** is equal to **(Your Template Name)**.
3.  Add the following fields, organized by tabs:

*   **Tab: Section Name 1**
    *   `field_name_1` (Field Type) - Description or example.
    *   `field_name_2` (Group)
        *   `nested_field_1` (Text)
*   **Tab: Section Name 2 (if it's a Repeater)**
    *   `repeater_field_name` (Repeater)
        *   `repeater_sub_field_1` (Image)
        *   `repeater_sub_field_2` (Text)

(Continue this structure for all dynamic parts of the page)
[ACF_STRUCTURE_END]

[PHP_CODE_START]
```php
<?php
/**
 * Template Name: {filename} Page
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
    )
    
    # Header template
    header_template = PromptTemplate(
        input_variables=["filename", "html_content"],
        template="""
You are an expert WordPress developer specializing in creating dynamic templates using the Advanced Custom Fields (ACF) plugin.
Your task is to convert the provided static HTML code into dynamic WordPress PHP code that will be inserted into an existing `header.php` file.

**CRITICAL INSTRUCTIONS:**
1.  Analyze the provided HTML for the file `{filename}`.
2.  Identify all static content (text, images, links, lists, repeaters) that should be manageable from the WordPress admin panel.
3.  Generate *ONLY* the PHP code that uses ACF functions (like `get_field()`, `the_field()`, `have_rows()`, `the_row()`, `get_sub_field()`) to display this dynamic content.
    The generated PHP code should be self-contained and ready to be inserted between `<!-- Start Header Content -->` and `<!-- End Header Content -->` comments in an existing `header.php` file.
    DO NOT include `get_header();`, `get_footer();`, `<!DOCTYPE html>`, `<html>`, `<head>`, `<body>` tags, or any other surrounding HTML/PHP boilerplate. Only the dynamic content.
4.  Generate a clear, text-based ACF field structure that a user can follow to create the fields in the ACF plugin UI. Use field types like Text, Text Area, Image, Gallery, Repeater, Group, Link, and True/False where appropriate. Structure it with Tabs for better organization in the WP admin.
5.  The final output MUST be in the following format with the exact delimiters. DO NOT add any other text or explanations outside of these blocks.

[ACF_STRUCTURE_START]
**ACF Field Group Setup for: {filename}**

1.  Create a new Field Group named "**Header Content**".
2.  Set the **Location Rules** to show this field group if **Options Page** is equal to **Header Settings** (You might need to create an ACF Options Page for Header Settings).
3.  Add the following fields, organized by tabs:

*   **Tab: Header General**
    *   `header_logo` (Image) - Main logo for the header.
    *   `header_logo_link` (Link) - Link for the header logo.
    *   `cta_button_text` (Text) - Text for the Call-to-Action button.
    *   `cta_button_link` (Link) - Link for the Call-to-Action button.
*   **Tab: Navigation Menu**
    *   `header_navigation_items` (Repeater)
        *   `item_text` (Text) - Display text for the navigation item.
        *   `item_link` (Link) - URL for the navigation item.

[ACF_STRUCTURE_END]

[PHP_CODE_START]
```php
<?php
// Your generated PHP code goes here.
// It will be inserted into the header.php file.
?>
```
[PHP_CODE_END]

---
HERE IS THE HTML CONTENT TO CONVERT:
---
{html_content}
"""
    )
    
    # Footer template
    footer_template = PromptTemplate(
        input_variables=["filename", "html_content"],
        template="""
You are an expert WordPress developer specializing in creating dynamic templates using the Advanced Custom Fields (ACF) plugin.
Your task is to convert the provided static HTML code into dynamic WordPress PHP code that will be inserted into an existing `footer.php` file.

**CRITICAL INSTRUCTIONS:**
1.  Analyze the provided HTML for the file `{filename}`.
2.  Identify all static content (text, images, links, lists, repeaters) that should be manageable from the WordPress admin panel.
3.  Generate *ONLY* the PHP code that uses ACF functions (like `get_field()`, `the_field()`, `have_rows()`, `the_row()`, `get_sub_field()`) to display this dynamic content.
    The generated PHP code should be self-contained and ready to be inserted between `<!-- Start Footer -->` and `<!-- End Footer -->` comments in an existing `footer.php` file.
    DO NOT include `get_header();`, `get_footer();`, `<!DOCTYPE html>`, `<html>`, `<head>`, `<body>` tags, or any other surrounding HTML/PHP boilerplate. Only the dynamic content.
4.  Generate a clear, text-based ACF field structure that a user can follow to create the fields in the ACF plugin UI. Use field types like Text, Text Area, Image, Gallery, Repeater, Group, Link, and True/False where appropriate. Structure it with Tabs for better organization in the WP admin.
5.  The final output MUST be in the following format with the exact delimiters. DO NOT add any other text or explanations outside of these blocks.

[ACF_STRUCTURE_START]
**ACF Field Group Setup for: {filename}**

1.  Create a new Field Group named "**Footer Content**".
2.  Set the **Location Rules** to show this field group if **Options Page** is equal to **Footer Settings** (You might need to create an ACF Options Page for Footer Settings).
3.  Add the following fields, organized by tabs:

*   **Tab: Footer General**
    *   `footer_logo` (Image) - Logo for the footer.
    *   `footer_description` (Text Area) - Short description for the footer.
*   **Tab: Social Links**
    *   `social_media_links` (Repeater)
        *   `icon_class` (Text) - Font Awesome class for the icon (e.g., `fab fa-facebook-f`).
        *   `link_url` (Link) - URL for the social media link.
*   **Tab: Copyright Info**
    *   `copyright_text` (Text) - Copyright text.

[ACF_STRUCTURE_END]

[PHP_CODE_START]
```php
<?php
// Your generated PHP code goes here.
// It will be inserted into the footer.php file.
?>
```
[PHP_CODE_END]

---
HERE IS THE HTML CONTENT TO CONVERT:
---
{html_content}
"""
    )
    
    return {
        "general": general_template,
        "header": header_template,
        "footer": footer_template
    }

def get_appropriate_template(filename, templates):
    """Select the appropriate template based on filename."""
    if filename == "header.html":
        return templates["header"]
    elif filename == "footer.html":
        return templates["footer"]
    else:
        return templates["general"]

def call_langchain_api(html_content, file_name, config, model, templates):
    """Calls the LangChain API and returns the response with token tracking."""
    global total_tokens_used
    
    logging.info(f"🤖 Contacting LangChain API for '{file_name}'...")
    try:
        # Get appropriate template
        template = get_appropriate_template(file_name, templates)
        
        # Format the prompt
        formatted_prompt = template.format(
            filename=file_name,
            html_content=html_content
        )
        
        logging.debug(f"📝 LangChain Prompt for '{file_name}':\n{formatted_prompt}")
        
        # Create message
        message = HumanMessage(content=formatted_prompt)
        
        # Call the model
        response = model.invoke([message])
        
        # Extract response text
        response_text = response.content if hasattr(response, 'content') else str(response)
        
        # Try to get token usage (may not be available for all models)
        try:
            if hasattr(response, 'response_metadata') and 'usage' in response.response_metadata:
                usage = response.response_metadata['usage']
                prompt_tokens = usage.get('prompt_tokens', 0)
                completion_tokens = usage.get('completion_tokens', 0)
                total_file_tokens = prompt_tokens + completion_tokens
                total_tokens_used += total_file_tokens
                
                logging.info(f"📊 Token usage for '{file_name}': {prompt_tokens} prompt + {completion_tokens} completion = {total_file_tokens} total")
            else:
                logging.info(f"📊 Token usage data not available for '{file_name}'")
        except Exception as token_error:
            logging.debug(f"📊 Could not extract token usage: {token_error}")
        
        logging.info(f"✨ Successfully received response from LangChain for '{file_name}'")
        return response_text
        
    except Exception as e:
        logging.error(f"💥 An error occurred while calling LangChain API for '{file_name}': {e}")
        return None

def parse_langchain_response(response_text):
    """Parses the LangChain response to extract ACF structure and PHP code."""
    try:
        acf_structure_match = re.search(r'\[ACF_STRUCTURE_START\](.*?)\[ACF_STRUCTURE_END\]', response_text, re.DOTALL)
        php_code_match = re.search(r'\[PHP_CODE_START\]\s*```php\s*(.*?)```\s*\[PHP_CODE_END\]', response_text, re.DOTALL)

        acf_structure = acf_structure_match.group(1).strip() if acf_structure_match else ""
        php_code = php_code_match.group(1).strip() if php_code_match else ""

        if not acf_structure or not php_code:
            logging.warning("⚠️ Could not parse the response from LangChain correctly. Delimiters might be missing.")
            logging.debug(f"📄 Full LangChain Response:\n{response_text}")
            return None, None

        return acf_structure, php_code
    except Exception as e:
        logging.error(f"💥 Failed to parse LangChain response: {e}")
        return None, None

# --- 3. FILE PROCESSING & SAVING ---

def sanitize_filename(name):
    """Sanitizes a string to be a valid filename component."""
    name = name.lower()
    name = re.sub(r'[\s_]+', '-', name)
    name = re.sub(r'[^\w\-.]', '', name)
    return name

def process_html_file(html_path, config, wp_theme_path, model, templates):
    """The main function executed by each thread to process one HTML file."""
    global processed_files_count
    
    try:
        file_name = html_path.name
        logging.info(f"🔄 Starting processing for: {file_name}")
        logging.info("-" * 60)

        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # Call LangChain AI
        response_text = call_langchain_api(html_content, file_name, config, model, templates)
        if not response_text:
            return

        # Parse AI response
        acf_structure, php_code = parse_langchain_response(response_text)
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

            new_php_content = pattern.sub(f"\1\n{php_code_to_insert}\n\3", existing_php_content)

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
    logging.info("🎉 WORDPRESS THEME GENERATION COMPLETED! (LangChain Version)")
    logging.info("=" * 80)
    logging.info(f"📊 PROCESS SUMMARY:")
    logging.info(f"   📅 Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"   🏁 End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"   ⏱️  Total Duration: {duration_str}")
    logging.info(f"   📁 Project: {project_name}")
    logging.info(f"   📄 Files Found: {files_count}")
    logging.info(f"   ✅ Files Processed: {processed_files_count}")
    logging.info(f"   ⚡ Avg Time/File: {avg_time_per_file:.1f}s")
    logging.info("-" * 80)
    logging.info(f"🤖 LANGCHAIN AI USAGE:")
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
    logging.info(f"   📋 Log File: 'generation_log_{project_name}.txt'")
    logging.info("=" * 80)
    
    if processed_files_count == files_count:
        logging.info("🎊 All files processed successfully! Your WordPress theme is ready!")
    else:
        logging.warning(f"⚠️  Only {processed_files_count}/{files_count} files were processed successfully.")
    
    logging.info("=" * 80)

def main():
    """Main function to run the entire conversion process."""
    global start_time, total_tokens_used, processed_files_count
    
    try:
        config = load_environment()
        project_name = Path(config["project_path"]).name
        setup_logging(project_name)

        logging.info(f"📁 Project Path: {config['project_path']}")
        logging.info(f"🔧 LangChain Model: {config['gemini_model']}")
        logging.info(f"🌡️  Temperature: {config['temperature']}")
        logging.info(f"🎯 Max Tokens: {config['max_tokens']}")
        logging.info(f"👥 Max Workers: {config['max_workers']}")
        logging.info(f"⏱️  Processing Delay: {config['processing_delay']}s")

        # Initialize LangChain model
        model = setup_langchain_model(config)
        if not model:
            logging.error("💥 Failed to initialize LangChain model. Aborting.")
            return

        # Create prompt templates
        templates = create_prompt_templates()
        logging.info("📝 LangChain prompt templates created successfully")

        wp_theme_path = Path(config["wp_theme_folder"])
        if not clone_boilerplate(config["boilerplate_repo"], wp_theme_path):
            logging.error("💥 Failed to setup boilerplate. Aborting.")
            return

        files_to_process = find_html_files(config["project_path"])
        if not files_to_process:
            logging.warning("⚠️  No HTML files found to process. Exiting.")
            return

        logging.info(f"🚀 Starting processing of {len(files_to_process)} files...")
        logging.info("=" * 80)

        with concurrent.futures.ThreadPoolExecutor(max_workers=config["max_workers"]) as executor:
            # Submit all tasks to the thread pool
            futures = [executor.submit(process_html_file, html_path, config, wp_theme_path, model, templates) for html_path in files_to_process]
            # Wait for all tasks to complete
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()  # To raise exceptions if any occurred in the thread
                except Exception as e:
                    logging.error(f"💥 A thread raised an exception: {e}")
        
        # Log completion summary
        log_completion_summary(project_name, config, len(files_to_process))

    except ValueError as e:
        logging.error(f"⚙️  Configuration Error: {e}")
    except Exception as e:
        logging.error(f"💥 A critical error occurred in the main process: {e}")
        if start_time:
            end_time = datetime.now()
            duration = end_time - start_time
            logging.error(f"⏱️  Script ran for {duration} before failing")

if __name__ == "__main__":
    main()