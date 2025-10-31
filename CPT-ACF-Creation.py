import os
import google.generativeai as genai
import time
import logging
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import re
import datetime
import sys

# --- Constants ---
LOG_ICONS = {
    "INFO": "ℹ️",
    "SUCCESS": "✅",
    "ERROR": "❌",
    "START": "🚀",
    "END": "🏁",
    "AI": "🤖",
    "FILE": "📄",
    "WRITE": "✏️",
    "CONFIG": "⚙️",
    "SECTION": "📑"
}

# Master prompt for CPT section ACF generation
MASTER_PROMPT = """
You are an expert WordPress developer specializing in the Advanced Custom Fields (ACF) plugin. Your primary mission is to generate a complete, verbose, and syntactically perfect PHP array for an ACF Field Group for a Custom Post Type section.

You will be provided with:
1. The Custom Post Type name (e.g., "testimonial", "team", "service-items")
2. The section's PHP template code that shows how the data is being used

Your task is to analyze the template code and determine:
- Which data fields are needed (excluding WordPress default fields: Title, Content, Featured Image)
- What ACF field types are required (Text, Textarea, Number, Image, File, Gallery, WYSIWYG Editor, Checkbox, Select, Radio Button, True/False, Page Link, Group, Repeater, Link)
- The structure and organization of these fields

**CRITICAL RULES:**

1. **Exclude Default WordPress Fields:** DO NOT create ACF fields for:
   - Post Title (accessed via `get_the_title()` or `the_title()`)
   - Post Content (accessed via `get_the_content()` or `the_content()`)
   - Featured Image (accessed via `get_the_post_thumbnail()` or `the_post_thumbnail()`)

2. **Generate Unique Keys:** Create unique 13-character hexadecimal keys for every field (starting with `field_`)

3. **Field Naming:**
   - `'label'`: Human-readable version (e.g., "Author Name")
   - `'name'`: Snake_case version (e.g., "author_name")

4. **Nested Fields:** For repeater sub-fields, include `'parent_repeater' => 'field_key_of_parent'`

5. **Output Format:** Output ONLY the PHP array for the 'fields' key, without:
   - No surrounding PHP tags (<?php, ?>)
   - No comments
   - No surrounding array structure
   - Just the inner array: `array( array( 'key' => 'field_...', ...), ... )`

**Section Information:**
- CPT Name: {cpt_name}
- Page: {page_name}
- Section: {section_name}

**Template Code to Analyze:**
```php
{template_code}
```

Analyze the template code above and generate the ACF fields array needed to support this template, excluding Title, Content, and Featured Image fields.
"""


def setup_logging(project_name, project_path):
    """Sets up a logger to file and console."""
    log_filename = "Log-For-CPT-ACF-Creation.txt"
    log_filepath = os.path.join(project_path, log_filename)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(log_filepath, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    with open(log_filepath, 'w', encoding='utf-8') as f:
        f.write(f"Log for CPT ACF Generation - Project: {project_name} - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*80 + "\n")
    return logging.getLogger()


def load_configuration():
    """Loads and validates configuration from the .env file."""
    load_dotenv()
    config = {
        "api_key": os.getenv("GEMINI_API_KEY"),
        "model": os.getenv("GEMINI_MODEL"),
        "PROJECT_PATH_FOR_CPT_GENERATION": os.getenv("PROJECT_PATH_FOR_CPT_GENERATION"),
        "delay": int(os.getenv("PROCESSING_DELAY", 4)),
    }
    if not all([config["api_key"], config["model"], config["PROJECT_PATH_FOR_CPT_GENERATION"]]):
        raise ValueError("One or more required environment variables are missing from the .env file.")
    return config


def parse_cpt_sections_file(file_path, logger):
    """
    Parses the CPT-Sections-Data.txt file and extracts page -> section mappings.
    Returns a list of dictionaries with page_name, section_name, and cpt_indicator.
    """
    sections = []
    current_page = None
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Split by page sections (lines starting with 'v 🗂️')
        page_blocks = re.split(r'\n(?=v 🗂️)', content)
        
        for block in page_blocks:
            if not block.strip():
                continue
                
            lines = block.strip().split('\n')
            
            # Extract page name from first line
            page_match = re.search(r'v 🗂️\s*(.+?)(?:\s*$)', lines[0])
            if page_match:
                current_page = page_match.group(1).strip()
                
                # Look for section lines (starting with '>')
                for line in lines[1:]:
                    section_match = re.search(r'>\s*(?:🗂️|#)?\s*(.+?)(?:\s*:-\s*CPT)', line)
                    if section_match:
                        section_name = section_match.group(1).strip()
                        sections.append({
                            'page_name': current_page,
                            'section_name': section_name,
                            'is_cpt': True
                        })
        
        logger.info(f"{LOG_ICONS['SUCCESS']} Found {len(sections)} CPT sections to process")
        return sections
        
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Error parsing CPT sections file: {e}")
        return []


def extract_section_code(template_file_path, section_name, logger):
    """
    Extracts the section code from the template PHP file between markers.
    Looks for: <!-- START: {section_name} --> ... <!-- END: {section_name} -->
    """
    try:
        with open(template_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Create flexible pattern to match section markers
        pattern = rf'(?://)?<!--\s*START:\s*{re.escape(section_name)}\s*-->(.+?)(?://)?<!--\s*END:\s*{re.escape(section_name)}\s*-->'
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        
        if match:
            section_code = match.group(1).strip()
            logger.info(f"{LOG_ICONS['SECTION']} Extracted {len(section_code)} characters from '{section_name}' section")
            return section_code
        else:
            logger.warning(f"{LOG_ICONS['ERROR']} Section markers not found for '{section_name}'")
            return None
            
    except FileNotFoundError:
        logger.error(f"{LOG_ICONS['ERROR']} Template file not found: {template_file_path}")
        return None
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Error extracting section code: {e}")
        return None


def format_cpt_slug(name):
    """Converts section name to CPT slug (e.g., 'Team Section' -> 'team_section')"""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def get_acf_registration_code(cpt_slug, page_title, section_title, acf_php_code):
    """Generates the PHP code for registering the ACF field group for a CPT."""
    group_key = f"group_{hex(int(time.time() * 1000000))[-10:]}"
    
    location_array = f"""array(
                array(
                    array(
                        'param' => 'post_type',
                        'operator' => '==',
                        'value' => '{cpt_slug}',
                    ),
                ),
            )"""

    return f"""
/**
 * ===================================================================
 * Register ACF Field Group for CPT: {section_title} ({page_title})
 * ===================================================================
 */
if (!function_exists('import_{cpt_slug}_acf_fields')) {{
function import_{cpt_slug}_acf_fields() {{
    if ( function_exists('acf_import_field_group') && get_option('{cpt_slug}-acf-imported') !== '1' ) {{
        $field_group_array = array(
            'key' => '{group_key}',
            'title' => 'CPT Fields: {section_title}',
            'fields' => {acf_php_code},
            'location' => {location_array},
            'menu_order' => 0,
            'position' => 'normal',
            'style' => 'default',
            'label_placement' => 'top',
            'instruction_placement' => 'label',
            'hide_on_screen' => '',
            'active' => true,
            'description' => 'Auto-generated ACF fields for {section_title} CPT on {page_title}',
            'show_in_rest' => 0,
        );
        acf_import_field_group($field_group_array);
        update_option('{cpt_slug}-acf-imported', '1');
    }}
}}
add_action('acf/init', 'import_{cpt_slug}_acf_fields');
}}
"""


def process_cpt_section(section_info, config, logger):
    """
    Processes a single CPT section: extracts template code and generates ACF fields via AI.
    """
    page_name = section_info['page_name']
    section_name = section_info['section_name']
    
    # Convert page name to template filename
    page_slug = page_name.lower().replace(' ', '')
    template_file = os.path.join(config["PROJECT_PATH_FOR_CPT_GENERATION"], "template", f"{page_slug}.php")
    
    logger.info(f"{LOG_ICONS['AI']} Processing '{section_name}' section from '{page_name}' page...")
    
    # Extract section code from template
    section_code = extract_section_code(template_file, section_name, logger)
    if not section_code:
        logger.error(f"{LOG_ICONS['ERROR']} Could not extract section code, skipping...")
        return None
    
    # Generate CPT slug
    cpt_slug = format_cpt_slug(section_name)
    
    try:
        model = genai.GenerativeModel(config["model"])
        prompt = MASTER_PROMPT.format(
            cpt_name=cpt_slug,
            page_name=page_name,
            section_name=section_name,
            template_code=section_code
        )
        
        response = model.generate_content(prompt)
        time.sleep(config["delay"])
        
        # Clean the response
        generated_text = response.text.strip()
        
        # Remove markdown code blocks if present
        generated_text = re.sub(r'^```php\s*', '', generated_text)
        generated_text = re.sub(r'^```\s*', '', generated_text)
        generated_text = re.sub(r'\s*```$', '', generated_text)
        
        # Extract array definition
        match = re.search(r'array\([\s\S]*\);?', generated_text, re.IGNORECASE)
        
        if not match:
            raise ValueError("AI response did not contain a valid PHP array definition.")
        
        generated_text = match.group(0).strip()
        if generated_text.endswith(';'):
            generated_text = generated_text[:-1]
        
        token_count = model.count_tokens(prompt).total_tokens
        
        return {
            "page_name": page_name,
            "section_name": section_name,
            "cpt_slug": cpt_slug,
            "acf_code": generated_text,
            "tokens": token_count
        }
        
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Error processing '{section_name}': {e}")
        if 'response' in locals():
            logger.error(f"Failed AI Response: {response.text[:500]}...")
        return None


def main():
    """Main function to orchestrate CPT ACF generation."""
    start_time = time.time()
    
    # Setup
    try:
        config = load_configuration()
        genai.configure(api_key=config["api_key"])
        project_path = config["PROJECT_PATH_FOR_CPT_GENERATION"]
        project_name = os.path.basename(project_path)
        logger = setup_logging(project_name, project_path)
        logger.info(f"{LOG_ICONS['CONFIG']} Configuration loaded successfully.")
    except ValueError as e:
        logger.error(f"{LOG_ICONS['ERROR']} {e}")
        return
    
    logger.info(f"{LOG_ICONS['START']} Starting CPT ACF Field Generation for project: '{project_name}'")
    
    # Parse CPT sections file
    cpt_sections_file = os.path.join(config["PROJECT_PATH_FOR_CPT_GENERATION"], "Figma-analysis-data", "CPT-Sections-Data.txt")
    if not os.path.exists(cpt_sections_file):
        logger.error(f"{LOG_ICONS['ERROR']} CPT-Sections-Data.txt not found at: {cpt_sections_file}")
        return
    
    sections = parse_cpt_sections_file(cpt_sections_file, logger)
    if not sections:
        logger.warning(f"{LOG_ICONS['INFO']} No CPT sections found to process.")
        return
    
    logger.info(f"{LOG_ICONS['FILE']} Found {len(sections)} CPT sections to process.")
    
    # Process sections concurrently
    acf_registration_blocks = []
    total_tokens_used = 0
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_section = {
            executor.submit(process_cpt_section, section, config, logger): section 
            for section in sections
        }
        
        for future in tqdm(as_completed(future_to_section), total=len(sections), desc="Processing CPT Sections"):
            result = future.result()
            if result:
                acf_code = get_acf_registration_code(
                    result["cpt_slug"],
                    result["page_name"],
                    result["section_name"],
                    result["acf_code"]
                )
                acf_registration_blocks.append(acf_code)
                total_tokens_used += result["tokens"]
                logger.info(f"{LOG_ICONS['SUCCESS']} Generated ACF fields for '{result['section_name']}' CPT")
    
    # Write to functions.php
    if acf_registration_blocks:
        functions_php_path = os.path.join(config["PROJECT_PATH_FOR_CPT_GENERATION"], "functions.php")
        logger.info(f"{LOG_ICONS['WRITE']} Appending CPT ACF code to {functions_php_path}...")
        
        try:
            with open(functions_php_path, 'a', encoding='utf-8') as f:
                f.write("\n\n// --- AUTO-GENERATED CPT ACF REGISTRATION BLOCKS ---\n")
                f.write("\n".join(acf_registration_blocks))
            
            logger.info(f"{LOG_ICONS['SUCCESS']} Successfully appended CPT ACF code to functions.php")
        except Exception as e:
            logger.error(f"{LOG_ICONS['ERROR']} Could not write to functions.php: {e}")
    
    # Summary
    end_time = time.time()
    total_time = end_time - start_time
    
    summary = f"""
==================================================================
                    CPT ACF GENERATION SUMMARY
==================================================================
{LOG_ICONS['SUCCESS']} Project: {project_name}
{LOG_ICONS['FILE']} Total CPT Sections Processed: {len(sections)}
{LOG_ICONS['SUCCESS']} Successfully Generated: {len(acf_registration_blocks)}
{LOG_ICONS['AI']} Estimated Tokens Used: {total_tokens_used}
{LOG_ICONS['END']} Total Execution Time: {total_time:.2f} seconds
==================================================================
"""
    logger.info(summary)


if __name__ == "__main__":
    main()