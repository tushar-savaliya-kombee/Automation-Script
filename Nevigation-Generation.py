import os
import sys
import time
import requests
import json
import re
from datetime import datetime
from threading import Thread, Lock
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai

# Force UTF-8 encoding for console output
if sys.platform.startswith('win'):
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Load environment variables
load_dotenv()

# Global variables
log_lock = Lock()
log_file = None # Will be set dynamically in initialize_log
start_time = time.time()
total_prompt_tokens = 0
total_completion_tokens = 0
total_tokens = 0
token_lock = Lock()

# Icons for beautiful logging
ICONS = {
    'start': '🚀',
    'success': '✅',
    'error': '❌',
    'info': 'ℹ️',
    'warning': '⚠️',
    'process': '⚙️',
    'download': '⬇️',
    'upload': '⬆️',
    'file': '📄',
    'folder': '📁',
    'ai': '🤖',
    'time': '⏱️',
    'check': '✔️',
    'arrow': '➡️',
    'menu': '📋',
    'code': '💻',
    'figma': '🎨'
}

def log_message(message, icon='info', level='INFO'):
    """Thread-safe logging with beautiful formatting"""
    with log_lock:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        icon_symbol = ICONS.get(icon, ICONS['info'])
        
        log_line = f"{icon_symbol} [{timestamp}] [{level}] {message}"
        
        # Print to console
        print(log_line)
        sys.stdout.flush() # Ensure immediate output
        
        # Write to log file
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_line + '\n')
        except Exception as e:
            print(f"⚠️  Failed to write to log file: {str(e)}")

def log_section(title):
    """Log a section header"""
    separator = "=" * 80
    with log_lock:
        log_line = f"\n{separator}\n{'🔷 ' + title.upper()}\n{separator}\n"
        print(log_line)
        sys.stdout.flush() # Ensure immediate output
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_line)
        except:
            pass

def initialize_log(project_theme_path):
    """Initialize the log file"""
    global log_file
    log_file = os.path.join(project_theme_path, "Log-for-Header&footer.txt")

    header = f"""
{'=' * 80}
🎯 WORDPRESS MENU GENERATOR - EXECUTION LOG
{'=' * 80}
📅 Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'=' * 80}
"""
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(header)
    except Exception as e:
        print(f"❌ Failed to initialize log file: {str(e)}")

def fetch_figma_file(file_url, api_token):
    """Fetch Figma file data"""
    log_section("Fetching Figma File")
    
    try:
        # Extract file key from URL
        file_key_match = re.search(r'design/([a-zA-Z0-9]+)', file_url)
        if not file_key_match:
            raise ValueError("Invalid Figma URL format")
        
        file_key = file_key_match.group(1)
        log_message(f"Extracted File Key: {file_key}", 'check')
        
        # Fetch file data
        headers = {'X-Figma-Token': api_token}
        api_url = f'https://api.figma.com/v1/files/{file_key}'
        
        log_message(f"Requesting Figma API: {api_url}", 'download')
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        
        log_message("Successfully fetched Figma file data", 'success')
        return response.json()
    
    except Exception as e:
        log_message(f"Failed to fetch Figma file: {str(e)}", 'error', 'ERROR')
        return None

def find_page_frame(figma_data):
    """Find the main page frame in Figma data"""
    log_section("Searching for Page Frame")
    
    page_frame = None
    
    def search_for_page(node, depth=0):
        """Search for page frame (not nested, top-level search)"""
        nonlocal page_frame
        
        if not node or page_frame:
            return
        
        node_name = node.get('name', '').lower()
        node_type = node.get('type', '')
        
        # Check if this is a page frame (FRAME type with common page indicators)
        if node_type == 'FRAME':
            # Look for page indicators in the name
            page_indicators = ['page', 'join us', 'home', 'about', 'contact', 'landing']
            
            if any(indicator in node_name for indicator in page_indicators):
                page_frame = node
                log_message(f"Found Page Frame: {node.get('name')}", 'check')
                return
        
        # Search children at the same level
        if 'children' in node and depth < 3:  # Limit depth to avoid deep nesting
            for child in node.get('children', []):
                search_for_page(child, depth + 1)
    
    # Start search from document root
    document = figma_data.get('document', {})
    search_for_page(document)
    
    if page_frame:
        log_message("Page frame located successfully", 'success')
    else:
        log_message("Page frame not found", 'warning', 'WARNING')
    
    return page_frame

def find_header_footer_frames(page_frame):
    """Find header and footer frames ONLY as direct children of page frame"""
    log_section("Searching for Header & Footer Frames in Page")
    
    frames = {'header': None, 'footer': None}
    
    if not page_frame:
        log_message("No page frame provided", 'error', 'ERROR')
        return frames
    
    # Only search direct children of the page frame
    children = page_frame.get('children', [])
    
    log_message(f"Searching through {len(children)} direct children of page frame", 'process')
    
    for child in children:
        child_name = child.get('name', '').lower()
        child_type = child.get('type', '')
        
        # Only process FRAME type children
        if child_type != 'FRAME':
            continue
        
        log_message(f"Checking frame: {child.get('name')}", 'info')
        
        # Check if this is a header frame
        if 'header' in child_name and not frames['header']:
            frames['header'] = child
            log_message(f"✓ Found Header Frame: {child.get('name')}", 'check')
        
        # Check if this is a footer frame
        elif 'footer' in child_name and not frames['footer']:
            frames['footer'] = child
            log_message(f"✓ Found Footer Frame: {child.get('name')}", 'check')
        
        # Stop if both found
        if frames['header'] and frames['footer']:
            break
    
    if frames['header']:
        log_message("Header frame located successfully", 'success')
    else:
        log_message("Header frame not found in page children", 'warning', 'WARNING')
    
    if frames['footer']:
        log_message("Footer frame located successfully", 'success')
    else:
        log_message("Footer frame not found in page children", 'warning', 'WARNING')
    
    return frames

def download_frame_image(file_key, node_id, api_token, output_path, frame_type):
    """Download frame image from Figma"""
    log_message(f"Downloading {frame_type} frame image...", 'download')
    
    try:
        headers = {'X-Figma-Token': api_token}
        images_url = f'https://api.figma.com/v1/images/{file_key}?ids={node_id}&format=png&scale=2'
        
        response = requests.get(images_url, headers=headers)
        response.raise_for_status()
        
        image_data = response.json()
        image_url = image_data.get('images', {}).get(node_id)
        
        if not image_url:
            raise ValueError(f"No image URL returned for {frame_type}")
        
        log_message(f"Image URL received for {frame_type}", 'check')
        
        # Download the actual image
        img_response = requests.get(image_url)
        img_response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(img_response.content)
        
        log_message(f"{frame_type.capitalize()} image saved: {output_path}", 'success')
        return True
    
    except Exception as e:
        log_message(f"Failed to download {frame_type} image: {str(e)}", 'error', 'ERROR')
        return False

def generate_markdown_from_image(image_path, frame_type, gemini_api_key, gemini_model):
    """Use Gemini AI to generate markdown from image"""
    global total_prompt_tokens, total_completion_tokens, total_tokens
    
    log_message(f"Analyzing {frame_type} image with Gemini AI ({gemini_model})...", 'ai')
    
    try:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel(gemini_model)
        
        # Upload image
        with open(image_path, 'rb') as f:
            image_data = f.read()
        
        if frame_type == 'header':
            prompt = """
Analyze this HEADER design image and extract all navigation menu items.

CRITICAL INSTRUCTIONS - EXCLUDE THE FOLLOWING:
- Company Logo/Brand logo
- Icons (search, cart, user profile, hamburger menu)
- Call-to-Action BUTTONS with colored backgrounds (e.g., "Let's Talk", "Get Started", "Contact Us", "Sign Up", "Book Now", "Join Now")
- Search bars or input fields
- Social media icons
- Any element with button styling (rounded corners, filled background, contrasting colors)

ONLY INCLUDE:
- Plain text navigation links (typically in the center of header)
- Standard menu items without button styling
- Navigation links that are part of the main menu

Common navigation items include: (e.g., Home, About, About Us, Services, Products, Solutions, Who We Are, Industries, Case Studies, Blogs, Hire Experts, etc.)

Output format (STRICT MARKDOWN):

Header Navigation (Primary Navigation)
1. [Menu Item 1]
2. [Menu Item 2]
3. [Menu Item 3]

REMEMBER: 
- Do NOT include buttons with colored backgrounds
- Do NOT include CTAs (Call-to-Action buttons)
- Only plain text menu links
- List items in the order they appear from left to right
"""
        else:  # footer
            prompt = """
Analyze this FOOTER design image and extract navigation menu items grouped by sections.

CRITICAL INSTRUCTIONS:
- IGNORE social media links/icons (Facebook, Twitter, Instagram, LinkedIn, etc.)
- IGNORE email addresses and phone numbers
- ONLY extract TEXT NAVIGATION LINKS grouped under section headings
- Common footer sections: "Quick Links", "Company", "Resources", "Services", "About"

Output format (STRICT MARKDOWN):

Footer Navigation Structure :-

[Section Name 1] (Footer-[Section Name]-Navigation)
1. [Link 1]
2. [Link 2]
3. [Link 3]

[Section Name 2] (Footer-[Section Name]-Navigation)
1. [Link 1]
2. [Link 2]

REMEMBER: Do NOT include social media sections or icons. Only navigation text links.
"""
        
        response = model.generate_content([prompt, {'mime_type': 'image/png', 'data': image_data}])
        
        # Track token usage
        if hasattr(response, 'usage_metadata'):
            with token_lock:
                prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                completion_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens
                total_tokens += (prompt_tokens + completion_tokens)
                log_message(f"Tokens used - Prompt: {prompt_tokens}, Completion: {completion_tokens}", 'info')
        
        log_message(f"Gemini AI analysis completed for {frame_type}", 'success')
        return response.text
    
    except Exception as e:
        log_message(f"Gemini AI analysis failed for {frame_type}: {str(e)}", 'error', 'ERROR')
        return None

def create_header_footer_content(PROJECT_THEME_PATH):
    """Main function to create header and footer content"""
    log_section("Creating Header & Footer Content")
    
    # Get API credentials
    figma_url = os.getenv('FIGMA_FILE_URL')
    figma_token = os.getenv('FIGMA_API_TOKEN')
    gemini_key = os.getenv('GEMINI_API_KEY')
    gemini_model = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash')  # Default fallback
    
    if not all([figma_url, figma_token, gemini_key]):
        log_message("Missing API credentials in .env file", 'error', 'ERROR')
        return False
    
    log_message(f"Using Gemini Model: {gemini_model}", 'ai')
    
    # Create output folder
    output_folder = os.path.join(PROJECT_THEME_PATH, 'Header&Footer-Content')
    os.makedirs(output_folder, exist_ok=True)
    log_message(f"Created output folder: {output_folder}", 'folder')
    
    # Fetch Figma data
    figma_data = fetch_figma_file(figma_url, figma_token)
    if not figma_data:
        return False
    
    # Find page frame first
    page_frame = find_page_frame(figma_data)
    if not page_frame:
        log_message("Could not find page frame", 'error', 'ERROR')
        return False
    
    # Find header and footer frames as direct children of page frame
    frames = find_header_footer_frames(page_frame)
    
    # Extract file key
    file_key = re.search(r'design/([a-zA-Z0-9]+)', figma_url).group(1)
    
    # Download images and generate markdown
    markdown_parts = []
    
    for frame_type, frame_data in frames.items():
        if frame_data:
            node_id = frame_data.get('id')
            image_path = os.path.join(output_folder, f'{frame_type}.png')
            
            # Download image
            if download_frame_image(file_key, node_id, figma_token, image_path, frame_type):
                # Generate markdown
                markdown = generate_markdown_from_image(image_path, frame_type, gemini_key, gemini_model)
                if markdown:
                    markdown_parts.append(markdown)
    
    # Combine and save markdown
    if markdown_parts:
        combined_markdown = '\n\n'.join(markdown_parts)
        output_file = os.path.join(output_folder, 'Header&Footer-Content.txt')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(combined_markdown)
        
        log_message(f"Markdown saved: {output_file}", 'success')
        return True
    else:
        log_message("No markdown content generated", 'error', 'ERROR')
        return False

def generate_menu_registration_code(markdown_content, gemini_api_key, gemini_model):
    """Generate WordPress menu registration code using Gemini AI"""
    global total_prompt_tokens, total_completion_tokens, total_tokens
    
    log_section("Generating Menu Registration Code")
    
    try:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel(gemini_model)
        
        prompt = f"""
Based on this navigation structure:

{markdown_content}

Generate WordPress PHP code for menu registration following this pattern:

1. Create register_nav_menus() with all locations:
   - 'primary-navigation' for header
   - 'footer-[slug]' for each footer section (e.g., footer-quick-links, footer-company)
   - Use clean slugs (lowercase, hyphens, no spaces)

2. Create wp_create_nav_menu() for each menu with items:
   - Use slugified URLs: home_url('/[slug]/')
   - Convert menu item names to clean URL slugs
   - Proper menu-to-location mapping
   - Check if menu exists before creating

3. Use after_setup_theme hook with proper function name

IMPORTANT:
- NO <?php opening tag
- Use proper WordPress function names
- Create actual menu items based on the provided structure
- Use clean, production-ready code
- Include error_log for debugging
- Handle existing menus gracefully

Output ONLY the PHP code, nothing else.
"""
        
        response = model.generate_content(prompt)
        
        # Track token usage
        if hasattr(response, 'usage_metadata'):
            with token_lock:
                prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                completion_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens
                total_tokens += (prompt_tokens + completion_tokens)
                log_message(f"Tokens used - Prompt: {prompt_tokens}, Completion: {completion_tokens}", 'info')
        
        code = response.text.strip()
        
        # Clean up code blocks if present
        code = re.sub(r'^```php\s*', '', code)
        code = re.sub(r'^```\s*', '', code)
        code = re.sub(r'\s*```$', '', code)
        
        log_message("Menu registration code generated successfully", 'success')
        return code
    
    except Exception as e:
        log_message(f"Failed to generate menu registration code: {str(e)}", 'error', 'ERROR')
        return None

def extract_menu_field_names(PROJECT_THEME_PATH):
    """Extract menu field names from ACF files"""
    log_section("Extracting ACF Menu Field Names")
    
    field_names = []
    acf_folder = os.path.join(PROJECT_THEME_PATH, 'ACF Fields')
    
    # Files to check
    files_to_check = [
        ('header-ACF-fields.txt', 'Tab: Navigation Menu'),
        ('footer-ACF-fields.txt', 'Tab: Footer Navigation')
    ]
    
    for filename, tab_marker in files_to_check:
        file_path = os.path.join(acf_folder, filename)
        
        if not os.path.exists(file_path):
            log_message(f"File not found: {filename}", 'warning', 'WARNING')
            continue
        
        log_message(f"Processing: {filename}", 'process')
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Find the relevant tab section
            if tab_marker in content:
                # Extract field names using regex
                # Look for patterns like: `field_name` (Select)
                pattern = r'`(select_[^`]+)`\s*\(Select\)'
                matches = re.findall(pattern, content)
                
                for match in matches:
                    field_names.append(match)
                    log_message(f"Found field: {match}", 'check')
        
        except Exception as e:
            log_message(f"Error reading {filename}: {str(e)}", 'error', 'ERROR')
    
    log_message(f"Total menu fields found: {len(field_names)}", 'success')
    return field_names

def generate_acf_filter_code(field_names, gemini_api_key, gemini_model):
    """Generate ACF filter code for menu dropdowns"""
    global total_prompt_tokens, total_completion_tokens, total_tokens
    
    log_section("Generating ACF Filter Code")
    
    if not field_names:
        log_message("No menu field names to process", 'warning', 'WARNING')
        return None
    
    try:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel(gemini_model)
        
        field_list = '\n'.join([f'- {name}' for name in field_names])
        
        prompt = f"""
Generate WordPress PHP code to populate ACF menu dropdown fields.

Field names to handle:
{field_list}

Requirements:
1. Create add_filter for EACH field: add_filter('acf/load_field/name=FIELDNAME', 'populate_nav_menu_choices')
2. Create ONE reusable function populate_nav_menu_choices($field) that:
   - Clears existing choices: $field['choices'] = array();
   - Gets all WordPress menus using wp_get_nav_menus()
   - Populates with menu->term_id => menu->name
   - Handles empty state with message

IMPORTANT:
- NO <?php opening tag
- Clean, production-ready code
- Include descriptive comment header
- One function, multiple filters

Output ONLY the PHP code, nothing else.
"""
        
        response = model.generate_content(prompt)
        
        # Track token usage
        if hasattr(response, 'usage_metadata'):
            with token_lock:
                prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                completion_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens
                total_tokens += (prompt_tokens + completion_tokens)
                log_message(f"Tokens used - Prompt: {prompt_tokens}, Completion: {completion_tokens}", 'info')
        
        code = response.text.strip()
        
        # Clean up code blocks
        code = re.sub(r'^```php\s*', '', code)
        code = re.sub(r'^```\s*', '', code)
        code = re.sub(r'\s*```$', '', code)
        
        log_message("ACF filter code generated successfully", 'success')
        return code
    
    except Exception as e:
        log_message(f"Failed to generate ACF filter code: {str(e)}", 'error', 'ERROR')
        return None

def append_to_functions_php(PROJECT_THEME_PATH, menu_code, filter_code):
    """Append generated code to functions.php"""
    log_section("Updating functions.php")
    
    functions_file = os.path.join(PROJECT_THEME_PATH, 'functions.php')
    
    try:
        # Prepare content to append
        content_to_append = "\n\n" + "// " + "=" * 76 + "\n"
        content_to_append += "// AUTO-GENERATED MENU CODE - DO NOT EDIT MANUALLY\n"
        content_to_append += "// Generated: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + "\n"
        content_to_append += "// " + "=" * 76 + "\n\n"
        
        if menu_code:
            content_to_append += menu_code + "\n\n"
            log_message("Menu registration code prepared", 'check')
        
        if filter_code:
            content_to_append += filter_code + "\n"
            log_message("ACF filter code prepared", 'check')
        
        # Append to file
        with open(functions_file, 'a', encoding='utf-8') as f:
            f.write(content_to_append)
        
        log_message(f"Successfully updated: {functions_file}", 'success')
        return True
    
    except Exception as e:
        log_message(f"Failed to update functions.php: {str(e)}", 'error', 'ERROR')
        return False

def main():
    """Main execution function"""
    PROJECT_THEME_PATH = os.getenv('PROJECT_THEME_PATH', os.getcwd())
    initialize_log(PROJECT_THEME_PATH)
    log_message("WordPress Menu Generator Started", 'start', 'START')
    
    # Get project folder from environment or current directory
    PROJECT_THEME_PATH = os.getenv('PROJECT_THEME_PATH', os.getcwd())
    log_message(f"Project Folder: {PROJECT_THEME_PATH}", 'folder')
    
    # Thread results storage
    results = {'header_footer': False, 'field_names': None}
    
    # Thread 1: Create header/footer content
    def thread1():
        results['header_footer'] = create_header_footer_content(PROJECT_THEME_PATH)
    
    # Thread 2: Extract menu field names
    def thread2():
        results['field_names'] = extract_menu_field_names(PROJECT_THEME_PATH)
    
    # Start threads
    log_message("Starting parallel processing with multithreading", 'process')
    t1 = Thread(target=thread1, name="HeaderFooterThread")
    t2 = Thread(target=thread2, name="ACFFieldThread")
    
    t1.start()
    t2.start()
    
    # Wait for completion
    t1.join()
    t2.join()
    
    log_message("All threads completed", 'success')
    
    # Check if header/footer content was created
    if not results['header_footer']:
        log_message("Header/Footer content creation failed", 'error', 'ERROR')
        return
    
    # Read the markdown content
    markdown_file = os.path.join(PROJECT_THEME_PATH, 'Header&Footer-Content', 'Header&Footer-Content.txt')
    
    if not os.path.exists(markdown_file):
        log_message("Markdown file not found", 'error', 'ERROR')
        return
    
    with open(markdown_file, 'r', encoding='utf-8') as f:
        markdown_content = f.read()
    
    log_message("Markdown content loaded", 'check')
    
    # Get Gemini API key and model
    gemini_key = os.getenv('GEMINI_API_KEY')
    gemini_model = os.getenv('GEMINI_MODEL', 'gemini-1.5-flash')  # Default fallback
    
    log_message(f"Using Gemini Model: {gemini_model}", 'ai')
    
    # Generate menu registration code
    menu_code = generate_menu_registration_code(markdown_content, gemini_key, gemini_model)
    
    # Generate ACF filter code
    filter_code = None
    if results['field_names']:
        filter_code = generate_acf_filter_code(results['field_names'], gemini_key, gemini_model)
    
    # Append to functions.php
    if menu_code or filter_code:
        append_to_functions_php(PROJECT_THEME_PATH, menu_code, filter_code)
    
    # Final summary
    log_section("Generation Summary")
    elapsed_time = time.time() - start_time
    
    # Create summary
    summary = f"""
{'=' * 80}
GENERATION SUMMARY
{'=' * 80}
✅ Project: {os.path.basename(PROJECT_THEME_PATH)}
🤖 Gemini API Token Usage:
   • Prompt Tokens: {total_prompt_tokens}
   • Completion Tokens: {total_completion_tokens}
   • Total Tokens: {total_tokens}
🏁 Total Execution Time: {elapsed_time:.2f} seconds
{'=' * 80}
"""
    
    # Log summary
    print(summary)
    sys.stdout.flush()
    
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(summary)
    except:
        pass
    
    log_message("WordPress Menu Generator Completed Successfully", 'success', 'SUCCESS')

if __name__ == "__main__":
    main()