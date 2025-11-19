import os
import google.generativeai as genai
import time
import logging
from dotenv import load_dotenv
from pymongo import MongoClient
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import re
import datetime
import sys
import json

# --- Constants ---
LOG_ICONS = {
    "INFO": "ℹ️",
    "SUCCESS": "✅",
    "ERROR": "❌",
    "START": "🚀",
    "END": "🏁",
    "AI": "🤖",
    "FILE": "📄",
    "WRITE": "✍️",
    "CONFIG": "⚙️",
    "SECTION": "📦",
    "DATABASE": "🗄️",
    "QUERY": "🔍"
}

# Master prompt for CPT section ACF generation
MASTER_PROMPT = """
You are an expert WordPress developer specializing in the Advanced Custom Fields (ACF) plugin. Your primary mission is to generate a complete, verbose, and syntactically perfect PHP array for an ACF Field Group for a Custom Post Type section.

You will be provided with:
1. The Custom Post Type slug (e.g., "testimonial", "team", "service-items")
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
- CPT Slug: {cpt_slug}
- Pages: {pages}
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
        "project-folder-path": os.getenv("PROJECT_THEME_PATH"),
        "mongo_uri": os.getenv("MONGO_URI"),
        "delay": int(os.getenv("PROCESSING_DELAY", 4)),
    }
    if not all([config["api_key"], config["model"], config["project-folder-path"], config["mongo_uri"]]):
        raise ValueError("One or more required environment variables are missing from the .env file.")
    return config


def connect_to_mongodb(mongo_uri, logger):
    """Connect to MongoDB database"""
    try:
        logger.info(f"{LOG_ICONS['DATABASE']} Connecting to MongoDB...")
        mongo_client = MongoClient(mongo_uri)
        
        # Extract database name from URI
        db_name = mongo_uri.split('/')[-1].split('?')[0]
        mongo_db = mongo_client[db_name]
        
        # Test connection
        mongo_client.server_info()
        logger.info(f"{LOG_ICONS['SUCCESS']} Connected to MongoDB database: {db_name}")
        return mongo_client, mongo_db
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Failed to connect to MongoDB: {str(e)}")
        raise


def get_latest_document_from_collection(collection):
    """Get the latest document from a MongoDB collection"""
    try:
        latest_doc = collection.find_one(sort=[("_id", -1)])
        return latest_doc
    except Exception as e:
        return None


def clean_section_name(section_name):
    """Clean section name by removing special characters and emojis"""
    if not section_name:
        return ""
    
    # Remove ALL emojis using a comprehensive regex pattern
    # This pattern matches all emoji ranges
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
        u"\U00002500-\U00002BEF"  # chinese char
        u"\U00002702-\U000027B0"
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        u"\U0001f926-\U0001f937"
        u"\U00010000-\U0010ffff"
        u"\u2640-\u2642" 
        u"\u2600-\u2B55"
        u"\u200d"
        u"\u23cf"
        u"\u23e9"
        u"\u231a"
        u"\ufe0f"  # dingbats
        u"\u3030"
                      "]+", re.UNICODE)
    
    cleaned = emoji_pattern.sub('', section_name)
    
    # Remove special prefixes like "v #", "#", etc.
    cleaned = re.sub(r'^(v\s*#\s*|#\s*)', '', cleaned)
    
    # Remove CPT suffix if present
    cleaned = re.sub(r'\s*:-\s*CPT.*$', '', cleaned)
    
    # Clean up extra whitespace
    cleaned = ' '.join(cleaned.split())
    cleaned = cleaned.strip()
    
    return cleaned


def fetch_cpt_sections_from_mongodb(mongo_db, logger):
    """Fetch CPT sections data from MongoDB using aggregation query"""
    logger.info(f"\n{LOG_ICONS['QUERY']} ═══════════════════════════════════════════════")
    logger.info(f"{LOG_ICONS['QUERY']} FETCHING CPT SECTIONS FROM MONGODB")
    logger.info(f"{LOG_ICONS['QUERY']} ═══════════════════════════════════════════════\n")
    
    try:
        # Get all collections in the database
        collection_names = mongo_db.list_collection_names()
        
        if not collection_names:
            logger.error(f"{LOG_ICONS['ERROR']} No collections found in database")
            return None
        
        logger.info(f"{LOG_ICONS['INFO']} Found {len(collection_names)} collection(s): {', '.join(collection_names)}")
        
        # Use the first collection
        collection_name = collection_names[0]
        collection = mongo_db[collection_name]
        logger.info(f"{LOG_ICONS['SUCCESS']} Using collection: {collection_name}")
        
        # Get the latest document from the collection
        latest_document = get_latest_document_from_collection(collection)
        
        if not latest_document:
            logger.error(f"{LOG_ICONS['ERROR']} No document found in collection")
            return None
        
        doc_identifier = latest_document.get('_id', 'Unknown')
        logger.info(f"{LOG_ICONS['SUCCESS']} Processing latest document: {doc_identifier}")
        logger.info(f"{LOG_ICONS['INFO']} Applying blog-exclusion filter (all variants starting with 'blog' will be excluded)")
        
        # MongoDB Aggregation Pipeline
        pipeline = [
            {"$match": {"_id": latest_document['_id']}},
            {"$unwind": "$pages"},
            {"$unwind": "$pages.sections"},
            {"$match": {"pages.sections.type": "CPT (Custom post type)"}},
            {"$match": {
                "pages.sections.name": {
                    "$not": {"$regex": "blog", "$options": "i"}
                }
            }},
            {"$addFields": {"cleanSectionName": "$pages.sections.name"}},
            {"$group": {
                "_id": "$cleanSectionName",
                "pages": {"$addToSet": "$pages.page"}
            }},
            {"$facet": {
                "similarSections": [
                    {"$match": {"$expr": {"$gt": [{"$size": "$pages"}, 1]}}},
                    {"$project": {"_id": 0, "sectionName": "$_id", "pages": 1}}
                ],
                "uniqueSections": [
                    {"$match": {"$expr": {"$eq": [{"$size": "$pages"}, 1]}}},
                    {"$unwind": "$pages"},
                    {"$group": {
                        "_id": "$pages",
                        "sectionNames": {"$addToSet": "$_id"}
                    }},
                    {"$project": {"_id": 0, "page": "$_id", "sectionNames": 1}},
                    {"$sort": {"page": 1}}
                ]
            }}
        ]
        
        # Execute aggregation
        logger.info(f"{LOG_ICONS['QUERY']} Executing MongoDB aggregation pipeline...")
        result = list(collection.aggregate(pipeline))
        
        if not result:
            logger.warning(f"{LOG_ICONS['ERROR']} No CPT sections found in MongoDB")
            return None
        
        cpt_data = result[0]
        
        # Log the raw MongoDB response
        logger.info(f"\n{LOG_ICONS['DATABASE']} ═══════════════════════════════════════════════")
        logger.info(f"{LOG_ICONS['DATABASE']} MONGODB AGGREGATION RESPONSE")
        logger.info(f"{LOG_ICONS['DATABASE']} ═══════════════════════════════════════════════\n")
        
        try:
            formatted_response = json.dumps(cpt_data, indent=2, default=str)
            logger.info(f"Raw MongoDB Response:\n{formatted_response}\n")
        except Exception as e:
            logger.warning(f"{LOG_ICONS['ERROR']} Could not format MongoDB response: {str(e)}")
            logger.info(f"Raw MongoDB Response: {cpt_data}\n")
        
        # Clean section names
        logger.info(f"{LOG_ICONS['INFO']} Cleaning section names and applying exclusion filters")
        
        # Filter similar sections
        filtered_similar = []
        for section in cpt_data.get('similarSections', []):
            cleaned_name = clean_section_name(section['sectionName'])
            section['sectionName'] = cleaned_name
            filtered_similar.append(section)
        
        cpt_data['similarSections'] = filtered_similar
        
        # Filter unique sections
        filtered_unique = []
        for page_data in cpt_data.get('uniqueSections', []):
            cleaned_names = []
            for name in page_data['sectionNames']:
                cleaned_name = clean_section_name(name)
                cleaned_names.append(cleaned_name)
            
            if cleaned_names:
                page_data['sectionNames'] = cleaned_names
                filtered_unique.append(page_data)
        
        cpt_data['uniqueSections'] = filtered_unique
        
        similar_count = len(cpt_data.get('similarSections', []))
        unique_count = sum(len(page['sectionNames']) for page in cpt_data.get('uniqueSections', []))
        
        logger.info(f"\n{LOG_ICONS['SUCCESS']} After filtering: {similar_count} similar sections (appearing on multiple pages)")
        logger.info(f"{LOG_ICONS['SUCCESS']} After filtering: {unique_count} unique sections")
        
        # Log details
        logger.info(f"\n{LOG_ICONS['SECTION']} Similar Sections Details:")
        for section in cpt_data.get('similarSections', []):
            logger.info(f"  • '{section['sectionName']}' appears on pages: {', '.join(section['pages'])}")
        
        logger.info(f"\n{LOG_ICONS['SECTION']} Unique Sections Details:")
        for page_data in cpt_data.get('uniqueSections', []):
            logger.info(f"  • Page '{page_data['page']}': {', '.join(page_data['sectionNames'])}")
        
        return cpt_data
        
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Error fetching CPT sections from MongoDB: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def extract_cpt_slugs_from_functions_php(project_path, logger):
    """Extract all registered CPT slugs from functions.php file"""
    logger.info(f"\n{LOG_ICONS['QUERY']} ═══════════════════════════════════════════════")
    logger.info(f"{LOG_ICONS['QUERY']} EXTRACTING CPT SLUGS FROM FUNCTIONS.PHP")
    logger.info(f"{LOG_ICONS['QUERY']} ═══════════════════════════════════════════════\n")
    
    functions_php_path = os.path.join(project_path, "functions.php")
    
    if not os.path.exists(functions_php_path):
        logger.error(f"{LOG_ICONS['ERROR']} functions.php not found at: {functions_php_path}")
        return {}
    
    logger.info(f"{LOG_ICONS['FILE']} Reading functions.php: {functions_php_path}")
    
    try:
        with open(functions_php_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        cpt_mapping = {}
        
        # Pattern to find register_post_type calls
        register_pattern = r"register_post_type\s*\(\s*['\"]([^'\"]+)['\"]"
        
        # Updated pattern to handle emojis and special characters in comments
        # This pattern captures text between "for" and "with", handling any characters including emojis
        comment_pattern = r"This CPT Post Creation Code for\s+(.+?)\s+with"
        
        register_matches = list(re.finditer(register_pattern, content))
        comment_matches = list(re.finditer(comment_pattern, content, re.UNICODE))
        
        register_list = [(m.group(1), m.start()) for m in register_matches]
        comment_list = [(m.group(1).strip(), m.start()) for m in comment_matches]
        
        logger.info(f"{LOG_ICONS['INFO']} Found {len(register_list)} register_post_type() calls")
        logger.info(f"{LOG_ICONS['INFO']} Found {len(comment_list)} CPT comment markers")
        
        # Match comments with their corresponding register_post_type calls
        for cpt_name_raw, comment_pos in comment_list:
            # Clean the CPT name from emojis
            cpt_name_clean = clean_section_name(cpt_name_raw)
            
            closest_slug = None
            min_distance = float('inf')
            
            for slug, register_pos in register_list:
                if register_pos > comment_pos:
                    distance = register_pos - comment_pos
                    if distance < min_distance and distance < 2000:
                        min_distance = distance
                        closest_slug = slug
            
            if closest_slug:
                # Store both raw and cleaned versions
                cpt_mapping[cpt_name_clean] = closest_slug
                cpt_mapping[cpt_name_raw] = closest_slug  # Also store with emojis for fallback
                logger.info(f"{LOG_ICONS['SUCCESS']} Found CPT: '{cpt_name_clean}' -> slug: '{closest_slug}'")
        
        if not cpt_mapping:
            logger.warning(f"{LOG_ICONS['ERROR']} No CPT mappings found in functions.php")
        else:
            logger.info(f"\n{LOG_ICONS['SUCCESS']} Total CPT mappings extracted: {len(cpt_mapping) // 2}\n")  # Divide by 2 since we store twice
        
        return cpt_mapping
        
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Error reading functions.php: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {}


def get_cpt_slug_for_section(section_name, cpt_slug_mapping, logger):
    """Get the correct CPT slug for a given section name using extracted mappings"""
    if not cpt_slug_mapping:
        logger.warning(f"{LOG_ICONS['ERROR']} CPT slug mapping not initialized")
        slug = re.sub(r'[^\w\s-]', '', section_name).strip().lower().replace(' ', '-')
        return slug
    
    # Try exact match first
    if section_name in cpt_slug_mapping:
        return cpt_slug_mapping[section_name]
    
    # Try case-insensitive match
    for cpt_name, slug in cpt_slug_mapping.items():
        if cpt_name.lower() == section_name.lower():
            return slug
    
    # Try partial match
    for cpt_name, slug in cpt_slug_mapping.items():
        normalized_section = section_name.lower().strip()
        normalized_cpt = cpt_name.lower().strip()
        
        if normalized_cpt in normalized_section or normalized_section in normalized_cpt:
            logger.info(f"{LOG_ICONS['INFO']} Partial match found: '{section_name}' matched with '{cpt_name}' -> '{slug}'")
            return slug
    
    # Fallback: generate slug from section name
    slug = re.sub(r'[^\w\s-]', '', section_name).strip().lower().replace(' ', '-')
    logger.warning(f"{LOG_ICONS['ERROR']} No CPT mapping found for '{section_name}', using generated slug: {slug}")
    return slug


def extract_section_code(template_file_path, section_name, logger):
    """Extract the section code from the template PHP file between markers."""
    try:
        with open(template_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
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


def format_cpt_function_name(cpt_slug):
    """Converts CPT slug to valid PHP function name (e.g., 'solutions-block' -> 'solutions_block')"""
    return re.sub(r'[^a-z0-9]+', '_', cpt_slug.lower()).strip('_')


def get_acf_registration_code(cpt_slug, pages, section_title, acf_php_code):
    """Generates the PHP code for registering the ACF field group for a CPT."""
    group_key = f"group_{hex(int(time.time() * 1000000))[-10:]}"
    pages_str = ', '.join(pages) if isinstance(pages, list) else pages
    
    # Convert CPT slug to valid PHP function name (replace hyphens with underscores)
    function_name = format_cpt_function_name(cpt_slug)
    
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
 * Register ACF Field Group for CPT: {section_title}
 * Pages: {pages_str}
 * ===================================================================
 */
if (!function_exists('import_{function_name}_acf_fields')) {{
function import_{function_name}_acf_fields() {{
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
            'description' => 'Auto-generated ACF fields for {section_title} CPT',
            'show_in_rest' => 0,
        );
        acf_import_field_group($field_group_array);
        update_option('{cpt_slug}-acf-imported', '1');
    }}
}}
add_action('acf/init', 'import_{function_name}_acf_fields');
}}
"""


def clean_page_name_for_file(page_name):
    """Clean page name to match actual template file names (remove emojis and special chars)"""
    # Remove all emojis
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
        u"\U00002500-\U00002BEF"  # chinese char
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        u"\U0001f926-\U0001f937"
        u"\U00010000-\U0010ffff"
        u"\u2640-\u2642" 
        u"\u2600-\u2B55"
        u"\u200d"
        u"\u23cf"
        u"\u23e9"
        u"\u231a"
        u"\ufe0f"  # dingbats
        u"\u3030"
                      "]+", re.UNICODE)
    
    cleaned = emoji_pattern.sub('', page_name)
    cleaned = cleaned.strip().lower().replace(' ', '')
    return cleaned


def process_similar_section(section_data, config, cpt_slug_mapping, logger):
    """Process a similar section (appears on multiple pages)"""
    section_name = section_data['sectionName']
    pages = section_data['pages']
    
    logger.info(f"\n{LOG_ICONS['AI']} Processing SIMILAR section: '{section_name}' (Pages: {', '.join(pages)})")
    
    # Get CPT slug
    cpt_slug = get_cpt_slug_for_section(section_name, cpt_slug_mapping, logger)
    
    # Collect template code from all pages
    all_template_code = []
    for page_name in pages:
        # Clean page name to match actual file names
        page_slug = clean_page_name_for_file(page_name)
        template_file = os.path.join(config["project-folder-path"], "template", f"{page_slug}.php")
        
        logger.info(f"{LOG_ICONS['FILE']} Looking for template: {template_file}")
        
        section_code = extract_section_code(template_file, section_name, logger)
        if section_code:
            all_template_code.append(f"/* From {page_name} page */\n{section_code}")
    
    if not all_template_code:
        logger.error(f"{LOG_ICONS['ERROR']} Could not extract any template code for '{section_name}'")
        return None
    
    # Combine all template codes
    combined_template_code = "\n\n".join(all_template_code)
    
    try:
        model = genai.GenerativeModel(config["model"])
        prompt = MASTER_PROMPT.format(
            cpt_slug=cpt_slug,
            pages=", ".join(pages),
            section_name=section_name,
            template_code=combined_template_code
        )
        
        response = model.generate_content(prompt)
        time.sleep(config["delay"])
        
        # Clean the response
        generated_text = response.text.strip()
        generated_text = re.sub(r'^```php\s*', '', generated_text)
        generated_text = re.sub(r'^```\s*', '', generated_text)
        generated_text = re.sub(r'\s*```$', '', generated_text)
        
        match = re.search(r'array\([\s\S]*\);?', generated_text, re.IGNORECASE)
        
        if not match:
            raise ValueError("AI response did not contain a valid PHP array definition.")
        
        generated_text = match.group(0).strip()
        if generated_text.endswith(';'):
            generated_text = generated_text[:-1]
        
        token_count = model.count_tokens(prompt).total_tokens
        
        return {
            "section_name": section_name,
            "cpt_slug": cpt_slug,
            "pages": pages,
            "acf_code": generated_text,
            "tokens": token_count
        }
        
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Error processing similar section '{section_name}': {e}")
        return None


def process_unique_section(page_name, section_name, config, cpt_slug_mapping, logger):
    """Process a unique section (appears on single page)"""
    logger.info(f"\n{LOG_ICONS['AI']} Processing UNIQUE section: '{section_name}' (Page: {page_name})")
    
    # Get CPT slug
    cpt_slug = get_cpt_slug_for_section(section_name, cpt_slug_mapping, logger)
    
    # Get template file - clean page name to match actual file names
    page_slug = clean_page_name_for_file(page_name)
    template_file = os.path.join(config["project-folder-path"], "template", f"{page_slug}.php")
    
    logger.info(f"{LOG_ICONS['FILE']} Looking for template: {template_file}")
    
    # Extract section code
    section_code = extract_section_code(template_file, section_name, logger)
    if not section_code:
        logger.error(f"{LOG_ICONS['ERROR']} Could not extract section code for '{section_name}'")
        return None
    
    try:
        model = genai.GenerativeModel(config["model"])
        prompt = MASTER_PROMPT.format(
            cpt_slug=cpt_slug,
            pages=page_name,
            section_name=section_name,
            template_code=section_code
        )
        
        response = model.generate_content(prompt)
        time.sleep(config["delay"])
        
        # Clean the response
        generated_text = response.text.strip()
        generated_text = re.sub(r'^```php\s*', '', generated_text)
        generated_text = re.sub(r'^```\s*', '', generated_text)
        generated_text = re.sub(r'\s*```$', '', generated_text)
        
        match = re.search(r'array\([\s\S]*\);?', generated_text, re.IGNORECASE)
        
        if not match:
            raise ValueError("AI response did not contain a valid PHP array definition.")
        
        generated_text = match.group(0).strip()
        if generated_text.endswith(';'):
            generated_text = generated_text[:-1]
        
        token_count = model.count_tokens(prompt).total_tokens
        
        return {
            "section_name": section_name,
            "cpt_slug": cpt_slug,
            "pages": [page_name],
            "acf_code": generated_text,
            "tokens": token_count
        }
        
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Error processing unique section '{section_name}': {e}")
        return None


def main():
    """Main function to orchestrate CPT ACF generation."""
    start_time = time.time()
    
    # Setup
    try:
        config = load_configuration()
        genai.configure(api_key=config["api_key"])
        project_path = config["project-folder-path"]
        project_name = os.path.basename(project_path)
        logger = setup_logging(project_name, project_path)
        logger.info(f"{LOG_ICONS['CONFIG']} Configuration loaded successfully.")
    except ValueError as e:
        print(f"{LOG_ICONS['ERROR']} {e}")
        return
    
    logger.info(f"\n{LOG_ICONS['START']} ═══════════════════════════════════════════════")
    logger.info(f"{LOG_ICONS['START']} Starting CPT ACF Field Generation")
    logger.info(f"{LOG_ICONS['START']} Project: '{project_name}'")
    logger.info(f"{LOG_ICONS['START']} ═══════════════════════════════════════════════\n")
    
    # Connect to MongoDB
    try:
        mongo_client, mongo_db = connect_to_mongodb(config["mongo_uri"], logger)
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Cannot proceed without MongoDB connection")
        return
    
    # Fetch CPT sections from MongoDB
    cpt_data = fetch_cpt_sections_from_mongodb(mongo_db, logger)
    if not cpt_data:
        logger.error(f"{LOG_ICONS['ERROR']} No CPT data retrieved from MongoDB")
        mongo_client.close()
        return
    
    # Extract CPT slugs from functions.php
    cpt_slug_mapping = extract_cpt_slugs_from_functions_php(project_path, logger)
    
    # Prepare sections for processing
    sections_to_process = []
    
    # Add similar sections
    for section in cpt_data.get('similarSections', []):
        sections_to_process.append({
            'type': 'similar',
            'data': section
        })
    
    # Add unique sections
    for page_data in cpt_data.get('uniqueSections', []):
        for section_name in page_data['sectionNames']:
            sections_to_process.append({
                'type': 'unique',
                'page_name': page_data['page'],
                'section_name': section_name
            })
    
    logger.info(f"\n{LOG_ICONS['FILE']} Total sections to process: {len(sections_to_process)}")
    logger.info(f"{LOG_ICONS['FILE']} Similar sections: {len(cpt_data.get('similarSections', []))}")
    logger.info(f"{LOG_ICONS['FILE']} Unique sections: {sum(len(p['sectionNames']) for p in cpt_data.get('uniqueSections', []))}\n")
    
    # Process sections
    acf_registration_blocks = []
    total_tokens_used = 0
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        
        for section in sections_to_process:
            if section['type'] == 'similar':
                future = executor.submit(
                    process_similar_section,
                    section['data'],
                    config,
                    cpt_slug_mapping,
                    logger
                )
            else:  # unique
                future = executor.submit(
                    process_unique_section,
                    section['page_name'],
                    section['section_name'],
                    config,
                    cpt_slug_mapping,
                    logger
                )
            futures.append(future)
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing CPT Sections"):
            result = future.result()
            if result:
                acf_code = get_acf_registration_code(
                    result["cpt_slug"],
                    result["pages"],
                    result["section_name"],
                    result["acf_code"]
                )
                acf_registration_blocks.append(acf_code)
                total_tokens_used += result["tokens"]
                logger.info(f"{LOG_ICONS['SUCCESS']} Generated ACF fields for '{result['section_name']}' CPT")
    
    # Close MongoDB connection
    mongo_client.close()
    logger.info(f"\n{LOG_ICONS['DATABASE']} MongoDB connection closed")
    
    # Write to functions.php
    if acf_registration_blocks:
        functions_php_path = os.path.join(config["project-folder-path"], "functions.php")
        logger.info(f"\n{LOG_ICONS['WRITE']} ═══════════════════════════════════════════════")
        logger.info(f"{LOG_ICONS['WRITE']} Writing ACF Registration Code to functions.php")
        logger.info(f"{LOG_ICONS['WRITE']} ═══════════════════════════════════════════════\n")
        
        try:
            with open(functions_php_path, 'a', encoding='utf-8') as f:
                f.write("\n\n// ═══════════════════════════════════════════════════════════════")
                f.write("\n// AUTO-GENERATED CPT ACF REGISTRATION BLOCKS")
                f.write(f"\n// Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                f.write("\n// ═══════════════════════════════════════════════════════════════\n")
                f.write("\n".join(acf_registration_blocks))
            
            logger.info(f"{LOG_ICONS['SUCCESS']} Successfully appended {len(acf_registration_blocks)} ACF registration blocks to functions.php")
        except Exception as e:
            logger.error(f"{LOG_ICONS['ERROR']} Could not write to functions.php: {e}")
    else:
        logger.warning(f"{LOG_ICONS['ERROR']} No ACF registration blocks generated")
    
    # Summary
    end_time = time.time()
    total_time = end_time - start_time
    
    logger.info(f"\n{'='*70}")
    logger.info(f"{'='*70}")
    logger.info(f"           CPT ACF GENERATION SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"{LOG_ICONS['SUCCESS']} Project: {project_name}")
    logger.info(f"{LOG_ICONS['FILE']} Total Sections Processed: {len(sections_to_process)}")
    logger.info(f"{LOG_ICONS['SUCCESS']} Successfully Generated: {len(acf_registration_blocks)}")
    logger.info(f"{LOG_ICONS['AI']} Estimated Tokens Used: {total_tokens_used}")
    logger.info(f"{LOG_ICONS['END']} Total Execution Time: {total_time:.2f} seconds")
    logger.info(f"{'='*70}")
    logger.info(f"{'='*70}\n")


if __name__ == "__main__":
    main()