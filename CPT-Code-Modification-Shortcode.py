import os
import re
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
from pymongo import MongoClient
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import google.generativeai as genai
from PIL import Image
import io

# Load environment variables
load_dotenv()

# Global variables
PROJECT_THEME_PATH = os.getenv('PROJECT_THEME_PATH')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = os.getenv('GEMINI_MODEL')
FIGMA_FILE_URL = os.getenv('FIGMA_FILE_URL')
FIGMA_API_TOKEN = os.getenv('FIGMA_API_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')

# Thread-safe logging
log_lock = threading.Lock()
log_entries = []

# Token tracking
total_input_tokens = 0
total_output_tokens = 0
token_lock = threading.Lock()

# Pricing (per million tokens)
INPUT_TOKEN_PRICE = 0.30
OUTPUT_TOKEN_PRICE = 2.50

# Rate limit handling
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 35  # seconds (Gemini free tier: 10 requests/minute)

def log_message(message: str, icon: str = "📝", level: str = "INFO"):
    """Thread-safe logging with beautiful formatting"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {icon} [{level}] {message}"
    
    with log_lock:
        log_entries.append(log_entry)
        print(log_entry)

def retry_on_rate_limit(func):
    """Decorator to retry Gemini API calls on rate limit errors"""
    def wrapper(*args, **kwargs):
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e)
                # Check if it's a rate limit error (429)
                if '429' in error_str or 'quota' in error_str.lower() or 'rate limit' in error_str.lower():
                    if attempt < MAX_RETRIES - 1:
                        # Extract retry delay from error message if available
                        retry_match = re.search(r'retry in (\d+\.?\d*)', error_str)
                        if retry_match:
                            delay = float(retry_match.group(1)) + 2  # Add 2 seconds buffer
                        else:
                            delay = INITIAL_RETRY_DELAY * (attempt + 1)  # Exponential backoff
                        
                        log_message(f"Rate limit hit. Waiting {delay:.1f} seconds before retry {attempt + 2}/{MAX_RETRIES}...", "⏳", "WARNING")
                        time.sleep(delay)
                        continue
                    else:
                        log_message(f"Rate limit exceeded after {MAX_RETRIES} attempts", "❌", "ERROR")
                        raise
                else:
                    # Not a rate limit error, raise immediately
                    raise
        return None
    return wrapper

def write_log_file():
    """Write all logs to file with beautiful formatting"""
    log_file_path = os.path.join(PROJECT_THEME_PATH, "Log-for-CPT-Code-Modification.txt")
    
    with open(log_file_path, 'w', encoding='utf-8') as f:
        f.write("=" * 100 + "\n")
        f.write("🚀 CPT DATA FETCH & CODE GENERATION LOG\n")
        f.write("=" * 100 + "\n")
        f.write(f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"📁 Project Path: {PROJECT_THEME_PATH}\n")
        f.write("=" * 100 + "\n\n")
        
        for entry in log_entries:
            f.write(entry + "\n")
        
        f.write("\n" + "=" * 100 + "\n")
        f.write("✅ LOG FILE GENERATION COMPLETED\n")
        f.write("=" * 100 + "\n")
    
    log_message(f"Log file written to: {log_file_path}", "💾", "INFO")

def track_tokens(input_tokens: int, output_tokens: int):
    """Track token usage in thread-safe manner"""
    global total_input_tokens, total_output_tokens
    
    with token_lock:
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

def calculate_cost() -> Tuple[float, float, float]:
    """Calculate total cost based on token usage"""
    input_cost = (total_input_tokens / 1_000_000) * INPUT_TOKEN_PRICE
    output_cost = (total_output_tokens / 1_000_000) * OUTPUT_TOKEN_PRICE
    total_cost = input_cost + output_cost
    return input_cost, output_cost, total_cost

def clean_section_name(name: str) -> str:
    """Clean section name by removing special characters"""
    return re.sub(r'[^\w\s-]', '', name).strip()

def should_exclude_section(name: str) -> bool:
    """Check if section should be excluded"""
    excluded_keywords = [
        'header', 'footer', 'navigation', 'nav', 'menu',
        'blog', 'blogs', 'post', 'posts', 'article', 'articles'  # Exclude all blog-related sections
    ]
    name_lower = name.lower()
    is_excluded = any(keyword in name_lower for keyword in excluded_keywords)
    
    if is_excluded:
        log_message(f"Excluding section: '{name}' (matches excluded keyword)", "🚫", "DEBUG")
    
    return is_excluded

def get_project_folder_name(project_theme_path: str) -> str:
    """Extract the project folder name from the PROJECT_THEME_PATH and convert to MongoDB collection name format."""
    folder_name = os.path.basename(os.path.normpath(project_theme_path))
    # Convert hyphens to underscores for MongoDB collection name
    return folder_name.replace('-', '_')

def sanitize_page_name_for_file(page_name: str) -> str:
    """Sanitize page name for file system (remove emojis and special chars)"""
    # Remove emojis and special characters
    cleaned = re.sub(r'[^\w\s-]', '', page_name)
    # Remove extra whitespace and convert to lowercase
    cleaned = re.sub(r'\s+', '-', cleaned.strip().lower())
    # Remove leading/trailing hyphens
    cleaned = cleaned.strip('-')
    log_message(f"Sanitized: '{page_name}' -> '{cleaned}'", "🔄", "DEBUG")
    return cleaned

def sanitize_page_name_for_file_compact(page_name: str) -> str:
    """Sanitize page name for file system without hyphens (compact version)"""
    # Remove emojis and special characters
    cleaned = re.sub(r'[^\w\s]', '', page_name)
    # Remove all whitespace and convert to lowercase
    cleaned = re.sub(r'\s+', '', cleaned.strip().lower())
    return cleaned

def find_page_file(template_dir: str, page_name: str) -> Optional[str]:
    """Find the actual page file by trying different naming patterns"""
    sanitized_name = sanitize_page_name_for_file(page_name)
    sanitized_compact = sanitize_page_name_for_file_compact(page_name)
    
    # Try multiple patterns
    patterns = [
        f"{sanitized_name}.php",
        f"page-{sanitized_name}.php",
        f"template-{sanitized_name}.php",
        f"{sanitized_compact}.php",  # Compact version without hyphens
        f"page-{sanitized_compact}.php",
        f"template-{sanitized_compact}.php",
    ]
    
    for pattern in patterns:
        filepath = os.path.join(template_dir, pattern)
        if os.path.exists(filepath):
            log_message(f"Found page file: {filepath}", "✅", "DEBUG")
            return filepath
    
    # If not found, list all files and try to match
    if os.path.exists(template_dir):
        all_files = os.listdir(template_dir)
        log_message(f"Available templates: {', '.join(all_files)}", "📂", "DEBUG")
        
        # Try fuzzy matching with both sanitized versions
        for file in all_files:
            if file.endswith('.php'):
                file_lower = file.lower().replace('.php', '')
                # Check if sanitized names match the file
                if (sanitized_name == file_lower or 
                    sanitized_compact == file_lower or
                    sanitized_name in file_lower or 
                    file_lower in sanitized_name or
                    sanitized_compact in file_lower or
                    file_lower in sanitized_compact):
                    filepath = os.path.join(template_dir, file)
                    log_message(f"Found via fuzzy match: {filepath}", "✅", "DEBUG")
                    return filepath
    
    log_message(f"Page file not found: {page_name} (tried: {sanitized_name}, {sanitized_compact})", "⚠️", "WARNING")
    return None

def get_latest_document_from_collection(collection):
    """Get the latest document from collection"""
    try:
        latest_doc = collection.find_one(sort=[('_id', -1)])
        return latest_doc
    except Exception as e:
        log_message(f"Error getting latest document: {str(e)}", "❌", "ERROR")
        return None

def fetch_cpt_sections_from_mongodb(db):
    """Fetch CPT sections data from MongoDB using aggregation query"""
    log_message("Fetching CPT sections from MongoDB", "📊", "INFO")
    
    try:
        project_collection_name = get_project_folder_name(PROJECT_THEME_PATH)
        collection = db[project_collection_name]
        
        # Verify if the collection actually exists and has documents
        if project_collection_name not in db.list_collection_names() or collection.estimated_document_count() == 0:
            log_message(f"No collection named '{project_collection_name}' or it's empty in database '{db.name}'", "❌", "ERROR")
            return None

        log_message(f"Using collection: {project_collection_name}", "📁", "INFO")
        
        latest_document = get_latest_document_from_collection(collection)
        
        if not latest_document:
            log_message("No document found in collection", "❌", "ERROR")
            return None
        
        doc_identifier = latest_document.get('_id', 'Unknown')
        log_message(f"Processing latest document: {doc_identifier}", "📋", "INFO")
        
        pipeline = [
            {"$match": {"_id": latest_document['_id']}},
            {"$unwind": "$pages"},
            {"$unwind": "$pages.sections"},
            {"$match": {"pages.sections.type": "CPT (Custom post type)"}},
            {
                "$addFields": {
                    "cleanSectionName": "$pages.sections.name"
                }
            },
            {
                "$group": {
                    "_id": "$cleanSectionName",
                    "pages": {"$addToSet": "$pages.page"}
                }
            },
            {
                "$facet": {
                    "similarSections": [
                        {"$match": {"$expr": {"$gt": [{"$size": "$pages"}, 1]}}},
                        {"$project": {"_id": 0, "sectionName": "$_id", "pages": 1}}
                    ],
                    "uniqueSections": [
                        {"$match": {"$expr": {"$eq": [{"$size": "$pages"}, 1]}}},
                        {"$unwind": "$pages"},
                        {
                            "$group": {
                                "_id": "$pages",
                                "sectionNames": {"$addToSet": "$_id"}
                            }
                        },
                        {"$project": {"_id": 0, "page": "$_id", "sectionNames": 1}},
                        {"$sort": {"page": 1}}
                    ]
                }
            }
        ]
        
        result = list(collection.aggregate(pipeline))
        
        if not result:
            log_message("No CPT sections found in MongoDB", "⚠️", "WARNING")
            return None
        
        cpt_data = result[0]
        
        log_message("Cleaning section names and applying exclusion filters", "🔍", "INFO")
        
        filtered_similar = []
        for section in cpt_data.get('similarSections', []):
            cleaned_name = clean_section_name(section['sectionName'])
            if not should_exclude_section(cleaned_name):
                section['sectionName'] = cleaned_name
                filtered_similar.append(section)
        
        cpt_data['similarSections'] = filtered_similar
        
        filtered_unique = []
        for page_data in cpt_data.get('uniqueSections', []):
            cleaned_names = []
            for name in page_data['sectionNames']:
                cleaned_name = clean_section_name(name)
                if not should_exclude_section(cleaned_name):
                    cleaned_names.append(cleaned_name)
            
            if cleaned_names:
                page_data['sectionNames'] = cleaned_names
                filtered_unique.append(page_data)
        
        cpt_data['uniqueSections'] = filtered_unique
        
        similar_count = len(cpt_data.get('similarSections', []))
        unique_count = len(cpt_data.get('uniqueSections', []))
        
        log_message(f"After filtering: {similar_count} similar sections (appearing on multiple pages)", "📊", "INFO")
        log_message(f"After filtering: {unique_count} pages with unique sections", "📊", "INFO")
        
        for section in cpt_data.get('similarSections', []):
            log_message(f"Similar Section: '{section['sectionName']}' appears on pages: {', '.join(section['pages'])}", "🔄", "INFO")
        
        for page_data in cpt_data.get('uniqueSections', []):
            log_message(f"Unique Sections on '{page_data['page']}': {', '.join(page_data['sectionNames'])}", "📄", "INFO")
        
        return cpt_data
    
    except Exception as e:
        log_message(f"Error fetching CPT sections from MongoDB: {str(e)}", "❌", "ERROR")
        return None

def extract_figma_file_key(url: str) -> Optional[str]:
    """Extract Figma file key from URL"""
    match = re.search(r'figma\.com/design/([a-zA-Z0-9]+)', url)
    if match:
        return match.group(1)
    return None

def get_figma_file_data(file_key: str, token: str) -> Optional[Dict]:
    """Fetch Figma file data"""
    url = f"https://api.figma.com/v1/files/{file_key}"
    headers = {"X-Figma-Token": token}
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        log_message(f"Error fetching Figma file data: {str(e)}", "❌", "ERROR")
        return None

def find_section_node_id(figma_data: Dict, section_name: str, page_name: str) -> Optional[str]:
    """Find node ID for a specific section in Figma, searching within the correct page context"""
    try:
        document = figma_data.get('document', {})
        
        # Clean page name for matching (remove emojis and special chars)
        clean_page_name = re.sub(r'[^\w\s-]', '', page_name).strip().lower()
        
        # Step 1: Find the page frame (only at top level or first few levels)
        page_frame = None
        
        def find_page_frame(node, depth=0, max_depth=3):
            """Find page frame - only search top levels to avoid false matches"""
            if depth > max_depth:
                return None
            
            node_name = node.get('name', '').strip()
            node_type = node.get('type', '')
            
            # Only consider FRAME or CANVAS types as potential pages
            if node_type in ['FRAME', 'CANVAS']:
                node_name_lower = node_name.lower()
                # Strict matching: page name should be close to node name
                if (clean_page_name == node_name_lower or 
                    node_name_lower == clean_page_name or
                    (len(clean_page_name) > 3 and clean_page_name in node_name_lower and len(node_name) < 50)):
                    return node
            
            # Search children
            if 'children' in node and depth < max_depth:
                for child in node['children']:
                    result = find_page_frame(child, depth + 1, max_depth)
                    if result:
                        return result
            
            return None
        
        page_frame = find_page_frame(document)
        
        # Step 2: Search for section within the page frame
        if page_frame:
            log_message(f"Found page frame: '{page_frame.get('name')}' for page '{page_name}'", "🔍", "DEBUG")
            
            def search_in_frame(node, depth=0):
                """Search for section within the page frame"""
                node_name = node.get('name', '')
                
                # Check if this is the section we're looking for
                if section_name.lower() in node_name.lower():
                    node_id = node.get('id')
                    log_message(f"Found section '{section_name}' in page '{page_name}' with node ID: {node_id}", "✅", "DEBUG")
                    return node_id
                
                # Search children
                if 'children' in node:
                    for child in node['children']:
                        result = search_in_frame(child, depth + 1)
                        if result:
                            return result
                
                return None
            
            result = search_in_frame(page_frame)
            if result:
                return result
        
        # Step 3: Fallback to global search if page frame not found
        log_message(f"Page frame not found for '{page_name}', using global search", "⚠️", "DEBUG")
        
        def global_search(node):
            node_name = node.get('name', '')
            if section_name.lower() in node_name.lower():
                return node.get('id')
            
            if 'children' in node:
                for child in node['children']:
                    result = global_search(child)
                    if result:
                        return result
            return None
        
        return global_search(document)
    
    except Exception as e:
        log_message(f"Error finding section node: {str(e)}", "❌", "ERROR")
        return None

def download_figma_image(file_key: str, node_id: str, token: str, output_path: str) -> bool:
    """Download image from Figma"""
    url = f"https://api.figma.com/v1/images/{file_key}"
    headers = {"X-Figma-Token": token}
    params = {
        "ids": node_id,
        "format": "png",
        "scale": 2
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        image_url = data.get('images', {}).get(node_id)
        
        if not image_url:
            log_message(f"No image URL returned for node {node_id}", "⚠️", "WARNING")
            return False
        
        img_response = requests.get(image_url)
        img_response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(img_response.content)
        
        return True
    
    except Exception as e:
        log_message(f"Error downloading image: {str(e)}", "❌", "ERROR")
        return False

def download_section_images(cpt_data: Dict, figma_data: Dict, file_key: str, token: str, output_dir: str) -> Dict[str, List[str]]:
    """Download all section images from Figma with multithreading"""
    log_message("Starting image download process", "🖼️", "INFO")
    
    os.makedirs(output_dir, exist_ok=True)
    section_images = {}
    
    def download_task(section_name: str, page: str, index: int):
        safe_section_name = re.sub(r'[^\w\s-]', '', section_name).replace(' ', '_')
        safe_page_name = re.sub(r'[^\w\s-]', '', page).replace(' ', '_')
        # Include page name in filename to distinguish images from different pages
        filename = f"{safe_section_name}-{safe_page_name}-{index}.png"
        filepath = os.path.join(output_dir, filename)
        
        node_id = find_section_node_id(figma_data, section_name, page)
        
        if node_id:
            log_message(f"Downloading '{section_name}' from page '{page}' (Node: {node_id})", "⬇️", "INFO")
            if download_figma_image(file_key, node_id, token, filepath):
                log_message(f"Successfully downloaded: {filename} (Node: {node_id})", "✅", "INFO")
                return (section_name, filepath)
            else:
                log_message(f"Failed to download: {filename}", "❌", "ERROR")
        else:
            log_message(f"Node not found for '{section_name}' in page '{page}'", "⚠️", "WARNING")
        
        return (section_name, None)
    
    tasks = []
    
    for section in cpt_data.get('similarSections', []):
        section_name = section['sectionName']
        pages = section['pages']
        
        for idx, page in enumerate(pages, 1):
            tasks.append((section_name, page, idx))
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(download_task, *task): task for task in tasks}
        
        for future in as_completed(futures):
            section_name, filepath = future.result()
            if filepath:
                if section_name not in section_images:
                    section_images[section_name] = []
                section_images[section_name].append(filepath)
    
    log_message(f"Downloaded images for {len(section_images)} sections", "✅", "INFO")
    return section_images

@retry_on_rate_limit
def analyze_images_with_gemini(image_paths: List[str], section_name: str) -> str:
    """Analyze images with Gemini AI to check if layouts are similar"""
    log_message(f"Analyzing images for section: {section_name}", "🤖", "INFO")
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        images = []
        for path in image_paths:
            img = Image.open(path)
            images.append(img)
        
        prompt = f"""You are analyzing Figma design layouts for a section named "{section_name}".

I have provided {len(images)} images of the same section from different pages.

Your task is to determine if these sections have the SAME LAYOUT AND STRUCTURE.

Consider:
- Overall layout structure (grid, flexbox, positioning)
- Component arrangement and hierarchy
- Visual design patterns
- Content structure (not the actual content, but how it's organized)

Ignore:
- Actual text content
- Specific images or icons used
- Minor color variations
- Exact spacing values

Answer with ONLY ONE WORD: "YES" if the layouts are essentially the same, or "NO" if they are significantly different.

Your answer:"""
        
        response = model.generate_content([prompt] + images)
        
        usage_metadata = response.usage_metadata
        track_tokens(
            usage_metadata.prompt_token_count,
            usage_metadata.candidates_token_count
        )
        
        answer = response.text.strip().upper()
        
        if "YES" in answer:
            decision = "YES"
        elif "NO" in answer:
            decision = "NO"
        else:
            decision = "NO"
        
        log_message(f"Gemini decision for {section_name}: {decision}", "🎯", "INFO")
        return decision
    
    except Exception as e:
        log_message(f"Error analyzing images with Gemini: {str(e)}", "❌", "ERROR")
        return "NO"

@retry_on_rate_limit
def generate_shortcode_with_gemini(section_name: str, page_code: str) -> Optional[str]:
    """Generate shortcode using Gemini AI"""
    log_message(f"Generating shortcode for: {section_name}", "⚙️", "INFO")
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        cpt_slug = re.sub(r'[^\w]', '_', section_name.lower())
        shortcode_name = cpt_slug  # Use same slug for consistency
        
        prompt = f"""You are a WordPress developer. Generate a WordPress shortcode function for a CPT (Custom Post Type) section.

IMPORTANT: Use these exact names:
- Shortcode name: {shortcode_name}
- CPT slug: {cpt_slug}
- Function name: {shortcode_name}_shortcode

Section Name: {section_name}

Original HTML code:
{page_code}

Requirements:
1. Create function named: {shortcode_name}_shortcode()
2. Use WP_Query to fetch from CPT: '{cpt_slug}'
3. Loop through posts dynamically
4. Use WordPress functions: get_the_title(), get_the_content(), get_the_post_thumbnail()
5. Use escaping: esc_html(), esc_url()
6. Keep HTML structure and CSS classes
7. Add wp_reset_postdata() after loop
8. Register with: add_shortcode('{shortcode_name}', '{shortcode_name}_shortcode');

Generate ONLY PHP code. No markdown, no explanations.

Code:
```php
// Shortcode function code here
```
"""
        
        response = model.generate_content(prompt)
        
        usage_metadata = response.usage_metadata
        track_tokens(
            usage_metadata.prompt_token_count,
            usage_metadata.candidates_token_count
        )
        
        code = response.text.strip()
        
        code = re.sub(r'```php\s*', '', code)
        code = re.sub(r'```\s*$', '', code)
        
        log_message(f"Shortcode generated for: {section_name}", "✅", "INFO")
        return code
    
    except Exception as e:
        log_message(f"Error generating shortcode: {str(e)}", "❌", "ERROR")
        return None

@retry_on_rate_limit
def modify_section_code_with_gemini(section_name: str, page_code: str, cpt_slug: str) -> Optional[str]:
    """Modify section code to fetch data from CPT dynamically"""
    log_message(f"Modifying section code for: {section_name}", "🔧", "INFO")
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        prompt = f"""You are a WordPress developer. Modify the following HTML/PHP code to dynamically fetch data from a Custom Post Type.

Section Name: {section_name}
CPT Slug: {cpt_slug}

Original Code:
{page_code}

Your task:
1. Add WP_Query to fetch posts from the CPT: {cpt_slug}
2. Wrap the content in a conditional check (if posts exist)
3. Loop through posts and replace static content with dynamic data
4. Use proper WordPress functions: get_the_title(), get_the_content(), get_the_post_thumbnail(), etc.
5. Use proper escaping: esc_html(), esc_url(), etc.
6. Keep the HTML structure and CSS classes intact
7. Add wp_reset_postdata() after the loop

Generate ONLY the modified PHP code. No explanations.

Format:
```php
// Modified code here
```
"""
        
        response = model.generate_content(prompt)
        
        usage_metadata = response.usage_metadata
        track_tokens(
            usage_metadata.prompt_token_count,
            usage_metadata.candidates_token_count
        )
        
        code = response.text.strip()
        
        code = re.sub(r'```php\s*', '', code)
        code = re.sub(r'```\s*$', '', code)
        
        log_message(f"Section code modified for: {section_name}", "✅", "INFO")
        return code
    
    except Exception as e:
        log_message(f"Error modifying section code: {str(e)}", "❌", "ERROR")
        return None

def find_section_in_all_pages(template_dir: str, section_name: str) -> Optional[Tuple[str, str]]:
    """Search for section markers across all PHP files in template directory"""
    try:
        if not os.path.exists(template_dir):
            return None
        
        for file in os.listdir(template_dir):
            if file.endswith('.php'):
                filepath = os.path.join(template_dir, file)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # Check if this file contains the section marker
                    if f"<!-- START: {section_name} -->" in content:
                        log_message(f"Found section '{section_name}' in unexpected file: {file}", "🔍", "INFO")
                        return (filepath, file)
                except:
                    continue
        
        return None
    except Exception as e:
        log_message(f"Error searching for section: {str(e)}", "❌", "ERROR")
        return None

def extract_section_code_from_page(page_file: str, section_name: str) -> Optional[str]:
    """Extract section code from page file using markers"""
    try:
        with open(page_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Try different marker formats
        marker_patterns = [
            (f"<!-- START: {section_name} -->", f"<!-- END: {section_name} -->"),
            (f"<!-- START:{section_name} -->", f"<!-- END:{section_name} -->"),
            (f"<!--START: {section_name}-->", f"<!--END: {section_name}-->"),
        ]
        
        for start_marker, end_marker in marker_patterns:
            start_idx = content.find(start_marker)
            end_idx = content.find(end_marker)
            
            if start_idx != -1 and end_idx != -1:
                section_code = content[start_idx + len(start_marker):end_idx].strip()
                log_message(f"Found section code: {section_name} (length: {len(section_code)})", "✅", "DEBUG")
                return section_code
        
        # If not found, search for all markers in the file to help debug
        log_message(f"No markers found for: {section_name}", "⚠️", "WARNING")
        log_message(f"Expected marker: <!-- START: {section_name} -->", "🔍", "DEBUG")
        
        # Find all START markers in the file for debugging
        all_start_markers = re.findall(r'<!-- START: ([^>]+) -->', content)
        if all_start_markers:
            log_message(f"Available markers in {os.path.basename(page_file)}: {', '.join(all_start_markers)}", "🔍", "DEBUG")
        
        return None
    
    except Exception as e:
        log_message(f"Error extracting section code: {str(e)}", "❌", "ERROR")
        return None

def update_page_file_with_code(page_file: str, section_name: str, new_code: str, is_shortcode: bool = False):
    """Update page file with new code"""
    try:
        with open(page_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        start_marker = f"<!-- START: {section_name} -->"
        end_marker = f"<!-- END: {section_name} -->"
        
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)
        
        if start_idx != -1 and end_idx != -1:
            if is_shortcode:
                shortcode_slug = re.sub(r'[^\w]', '_', section_name.lower())
                # Fixed: Proper closing marker format
                replacement = f"{start_marker}\n<?php echo do_shortcode('[{shortcode_slug}]'); ?>\n{end_marker}"
            else:
                replacement = f"{start_marker}\n{new_code}\n{end_marker}"
            
            new_content = content[:start_idx] + replacement + content[end_idx + len(end_marker):]
            
            with open(page_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            log_message(f"Updated page file: {page_file}", "✅", "INFO")
            return True
        else:
            log_message(f"Markers not found in page file: {page_file}", "⚠️", "WARNING")
            return False
    
    except Exception as e:
        log_message(f"Error updating page file: {str(e)}", "❌", "ERROR")
        return False

def append_shortcode_to_file(shortcode_file: str, shortcode_code: str):
    """Append shortcode to shortcodes.php file"""
    try:
        with open(shortcode_file, 'a', encoding='utf-8') as f:
            f.write("\n\n")
            f.write("// " + "=" * 70 + "\n")
            f.write(f"// Auto-generated shortcode - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("// " + "=" * 70 + "\n")
            f.write(shortcode_code)
            f.write("\n")
        
        log_message(f"Shortcode appended to: {shortcode_file}", "✅", "INFO")
        return True
    
    except Exception as e:
        log_message(f"Error appending shortcode: {str(e)}", "❌", "ERROR")
        return False

def process_similar_section(section_data: Dict, section_images: Dict, project_path: str) -> Dict:
    """Process a similar section (multithreaded task)"""
    section_name = section_data['sectionName']
    pages = section_data['pages']
    
    log_message(f"Processing similar section: {section_name}", "🔄", "INFO")
    
    result = {
        'section_name': section_name,
        'pages': pages,
        'decision': 'NO',
        'method': 'Direct Modification',
        'status': 'Failed'
    }
    
    if section_name not in section_images or len(section_images[section_name]) < 2:
        log_message(f"Not enough images for section: {section_name}", "⚠️", "WARNING")
        return result
    
    image_paths = section_images[section_name]
    decision = analyze_images_with_gemini(image_paths, section_name)
    result['decision'] = decision
    
    # Define template_dir at the beginning so it's available in both branches
    template_dir = os.path.join(project_path, 'template')
    
    if decision == "YES":
        log_message(f"Creating shortcode for: {section_name}", "📝", "INFO")
        result['method'] = 'Shortcode'
        
        # Find the first available page file
        page_file = None
        for page in pages:
            page_file = find_page_file(template_dir, page)
            if page_file:
                break
        
        if not page_file:
            result['error'] = "No page file found"
            log_message(f"No page file found for any page", "❌", "ERROR")
            return result
        
        section_code = extract_section_code_from_page(page_file, section_name)
        
        # If not found in expected page, search all pages
        if not section_code:
            log_message(f"Searching for section '{section_name}' in all template files...", "🔍", "INFO")
            found_result = find_section_in_all_pages(template_dir, section_name)
            if found_result:
                page_file, found_filename = found_result
                section_code = extract_section_code_from_page(page_file, section_name)
                if section_code:
                    log_message(f"Section found in {found_filename}, using it as source", "✅", "INFO")
        
        if os.path.exists(page_file):
            pass  # section_code already extracted above
            
            if section_code:
                shortcode_code = generate_shortcode_with_gemini(section_name, section_code)
                
                if shortcode_code:
                    shortcode_file = os.path.join(project_path, 'includes', 'shortcodes.php')
                    os.makedirs(os.path.dirname(shortcode_file), exist_ok=True)
                    
                    if append_shortcode_to_file(shortcode_file, shortcode_code):
                        # Update all pages with shortcode call
                        success_count = 0
                        for page in pages:
                            page_file = find_page_file(template_dir, page)
                            if page_file and update_page_file_with_code(page_file, section_name, "", is_shortcode=True):
                                success_count += 1
                        
                        if success_count > 0:
                            result['status'] = 'Success'
                            log_message(f"Shortcode created and applied to {success_count} pages", "✅", "INFO")
                        else:
                            result['error'] = "Failed to update page files"
    else:
        log_message(f"Modifying code directly for: {section_name}", "🔧", "INFO")
        result['method'] = 'Direct Modification'
        
        success_count = 0
        for page in pages:
            page_file = find_page_file(template_dir, page)
            
            if not page_file:
                log_message(f"Page file not found for: {page}", "⚠️", "WARNING")
                continue
            
            section_code = extract_section_code_from_page(page_file, section_name)
            
            if not section_code:
                log_message(f"Section code not found in: {page}", "⚠️", "WARNING")
                continue
            
            cpt_slug = re.sub(r'[^\w]', '_', section_name.lower())
            modified_code = modify_section_code_with_gemini(section_name, section_code, cpt_slug)
            
            if modified_code and update_page_file_with_code(page_file, section_name, modified_code, is_shortcode=False):
                success_count += 1
                log_message(f"Successfully modified: {section_name} on {page}", "✅", "INFO")
        
        if success_count > 0:
            result['status'] = 'Success'
            log_message(f"Direct modification applied to {success_count} pages", "✅", "INFO")
        else:
            result['error'] = "Failed to modify any pages"
    
    return result

def main():
    """Main execution function"""
    start_time = time.time()
    
    log_message("=" * 100, "🚀", "INFO")
    log_message("CPT DATA FETCH & CODE GENERATION SCRIPT STARTED", "🚀", "INFO")
    log_message("=" * 100, "🚀", "INFO")
    
    # Validate environment variables
    required_vars = [PROJECT_THEME_PATH, GEMINI_API_KEY, GEMINI_MODEL, FIGMA_FILE_URL, FIGMA_API_TOKEN, MONGO_URI]
    if not all(required_vars):
        log_message("Missing required environment variables", "❌", "ERROR")
        write_log_file()
        return
    
    log_message(f"Project Path: {PROJECT_THEME_PATH}", "📁", "INFO")
    log_message(f"Figma File URL: {FIGMA_FILE_URL}", "🎨", "INFO")
    log_message(f"MongoDB URI: {MONGO_URI}", "🗄️", "INFO")
    
    # Connect to MongoDB
    log_message("Connecting to MongoDB", "🔌", "INFO")
    try:
        client = MongoClient(MONGO_URI)
        db_name = MONGO_URI.split('/')[-1] # Correctly derive db_name from MONGO_URI
        db = client[db_name]
        log_message(f"Connected to database: {db_name}", "✅", "INFO")
    except Exception as e:
        log_message(f"MongoDB connection failed: {str(e)}", "❌", "ERROR")
        write_log_file()
        return
    
    # Fetch CPT data from MongoDB
    cpt_data = fetch_cpt_sections_from_mongodb(db)
    
    if not cpt_data:
        log_message("No CPT data fetched", "❌", "ERROR")
        write_log_file()
        return
    
    # Extract Figma file key
    file_key = extract_figma_file_key(FIGMA_FILE_URL)
    if not file_key:
        log_message("Invalid Figma URL", "❌", "ERROR")
        write_log_file()
        return
    
    log_message(f"Figma File Key: {file_key}", "🔑", "INFO")
    
    # Fetch Figma file data
    figma_data = get_figma_file_data(file_key, FIGMA_API_TOKEN)
    if not figma_data:
        log_message("Failed to fetch Figma data", "❌", "ERROR")
        write_log_file()
        return
    
    log_message("Figma file data fetched successfully", "✅", "INFO")
    
    # Download section images
    output_dir = os.path.join(PROJECT_THEME_PATH, "Figma-analysis-data")
    section_images = download_section_images(cpt_data, figma_data, file_key, FIGMA_API_TOKEN, output_dir)
    
    # Process similar sections with multithreading
    log_message("=" * 100, "🔄", "INFO")
    log_message("PROCESSING SIMILAR SECTIONS", "🔄", "INFO")
    log_message("=" * 100, "🔄", "INFO")
    
    similar_results = []
    
    if cpt_data.get('similarSections'):
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(process_similar_section, section, section_images, PROJECT_THEME_PATH): section 
                for section in cpt_data['similarSections']
            }
            
            for future in as_completed(futures):
                result = future.result()
                similar_results.append(result)
                
                log_message("=" * 80, "📊", "INFO")
                log_message(f"Section: {result['section_name']}", "📌", "INFO")
                log_message(f"Pages: {', '.join(result['pages'])}", "📄", "INFO")
                log_message(f"Gemini Decision: {result['decision']}", "🎯", "INFO")
                log_message(f"Method: {result['method']}", "⚙️", "INFO")
                log_message(f"Status: {result['status']}", "✅" if result['status'] == 'Success' else "❌", "INFO")
                log_message("=" * 80, "📊", "INFO")
    
    # Process unique sections
    log_message("=" * 100, "📝", "INFO")
    log_message("PROCESSING UNIQUE SECTIONS", "📝", "INFO")
    log_message("=" * 100, "📝", "INFO")
    
    unique_results = []
    
    template_dir = os.path.join(PROJECT_THEME_PATH, 'template')
    
    for page_data in cpt_data.get('uniqueSections', []):
        page = page_data['page']
        section_names = page_data['sectionNames']
        
        log_message(f"Processing unique sections for page: {page}", "📄", "INFO")
        
        page_file = find_page_file(template_dir, page)
        
        if not page_file:
            for section_name in section_names:
                result = {
                    'section_name': section_name,
                    'page': page,
                    'method': 'Direct Modification',
                    'status': 'Failed',
                    'error': 'Page file not found'
                }
                unique_results.append(result)
                log_message(f"Page file not found for: {page}", "❌", "ERROR")
            continue
        
        for section_name in section_names:
            result = {
                'section_name': section_name,
                'page': page,
                'method': 'Direct Modification',
                'status': 'Failed',
                'error': None
            }
            
            if page_file:
                section_code = extract_section_code_from_page(page_file, section_name)
                
                # If not found, search all pages
                if not section_code:
                    log_message(f"Searching for section '{section_name}' in all template files...", "🔍", "INFO")
                    found_result = find_section_in_all_pages(template_dir, section_name)
                    if found_result:
                        alt_page_file, found_filename = found_result
                        section_code = extract_section_code_from_page(alt_page_file, section_name)
                        if section_code:
                            log_message(f"Section found in {found_filename}, using it as source", "✅", "INFO")
                            page_file = alt_page_file  # Update to use the correct file
                
                if not section_code:
                    result['error'] = 'Section markers not found'
                    log_message(f"Section code not found for: {section_name} in any template file", "⚠️", "WARNING")
                    unique_results.append(result)
                    continue
                
                cpt_slug = re.sub(r'[^\w]', '_', section_name.lower())
                modified_code = modify_section_code_with_gemini(section_name, section_code, cpt_slug)
                
                if not modified_code:
                    result['error'] = 'Code generation failed'
                    log_message(f"Failed to generate code for: {section_name}", "❌", "ERROR")
                    unique_results.append(result)
                    continue
                
                if update_page_file_with_code(page_file, section_name, modified_code, is_shortcode=False):
                    result['status'] = 'Success'
                    log_message(f"Successfully modified: {section_name} on {page}", "✅", "INFO")
                else:
                    result['error'] = 'Failed to update file'
                    log_message(f"Failed to update file for: {section_name}", "❌", "ERROR")
            
            unique_results.append(result)
            
            log_message("=" * 80, "📊", "INFO")
            log_message(f"Section: {result['section_name']}", "📌", "INFO")
            log_message(f"Page: {result['page']}", "📄", "INFO")
            log_message(f"Method: {result['method']}", "⚙️", "INFO")
            log_message(f"Status: {result['status']}", "✅" if result['status'] == 'Success' else "❌", "INFO")
            log_message("=" * 80, "📊", "INFO")
    
    # Calculate execution time
    end_time = time.time()
    execution_time = end_time - start_time
    
    # Calculate costs
    input_cost, output_cost, total_cost = calculate_cost()
    
    # Final summary
    log_message("=" * 100, "📊", "INFO")
    log_message("EXECUTION SUMMARY", "📊", "INFO")
    log_message("=" * 100, "📊", "INFO")
    
    log_message(f"Total Similar Sections Processed: {len(similar_results)}", "🔄", "INFO")
    similar_success = sum(1 for r in similar_results if r['status'] == 'Success')
    log_message(f"Similar Sections Success: {similar_success}/{len(similar_results)}", "✅", "INFO")
    
    log_message(f"Total Unique Sections Processed: {len(unique_results)}", "📝", "INFO")
    unique_success = sum(1 for r in unique_results if r['status'] == 'Success')
    log_message(f"Unique Sections Success: {unique_success}/{len(unique_results)}", "✅", "INFO")
    
    log_message("=" * 100, "💰", "INFO")
    log_message("TOKEN USAGE & COST ANALYSIS", "💰", "INFO")
    log_message("=" * 100, "💰", "INFO")
    
    log_message(f"Total Input Tokens: {total_input_tokens:,}", "📥", "INFO")
    log_message(f"Total Output Tokens: {total_output_tokens:,}", "📤", "INFO")
    log_message(f"Total Tokens: {total_input_tokens + total_output_tokens:,}", "📊", "INFO")
    
    log_message(f"Input Cost: ${input_cost:.6f}", "💵", "INFO")
    log_message(f"Output Cost: ${output_cost:.6f}", "💵", "INFO")
    log_message(f"Total Cost: ${total_cost:.6f}", "💰", "INFO")
    
    log_message("=" * 100, "⏱️", "INFO")
    log_message(f"Total Execution Time: {execution_time:.2f} seconds ({execution_time/60:.2f} minutes)", "⏱️", "INFO")
    log_message("=" * 100, "⏱️", "INFO")
    
    # Detailed results breakdown
    log_message("=" * 100, "📋", "INFO")
    log_message("DETAILED RESULTS BREAKDOWN", "📋", "INFO")
    log_message("=" * 100, "📋", "INFO")
    
    log_message("\n" + "🔄 SIMILAR SECTIONS:", "📋", "INFO")
    for result in similar_results:
        log_message(f"  • {result['section_name']}", "📌", "INFO")
        log_message(f"    Pages: {', '.join(result['pages'])}", "📄", "INFO")
        log_message(f"    Gemini Decision: {result['decision']}", "🤖", "INFO")
        log_message(f"    Method: {result['method']}", "⚙️", "INFO")
        log_message(f"    Status: {result['status']}", "✅" if result['status'] == 'Success' else "❌", "INFO")
        log_message("", "", "INFO")
    
    log_message("\n" + "📝 UNIQUE SECTIONS:", "📋", "INFO")
    for result in unique_results:
        log_message(f"  • {result['section_name']}", "📌", "INFO")
        log_message(f"    Page: {result['page']}", "📄", "INFO")
        log_message(f"    Method: {result['method']}", "⚙️", "INFO")
        log_message(f"    Status: {result['status']}", "✅" if result['status'] == 'Success' else "❌", "INFO")
        log_message("", "", "INFO")
    
    # Write log file
    log_message("=" * 100, "💾", "INFO")
    log_message("Writing log file", "💾", "INFO")
    write_log_file()
    
    log_message("=" * 100, "🎉", "INFO")
    log_message("SCRIPT EXECUTION COMPLETED SUCCESSFULLY", "🎉", "INFO")
    log_message("=" * 100, "🎉", "INFO")
    
    # Close MongoDB connection
    client.close()
    log_message("MongoDB connection closed", "🔌", "INFO")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_message("\nScript interrupted by user", "⚠️", "WARNING")
        write_log_file()
    except Exception as e:
        log_message(f"Unexpected error: {str(e)}", "❌", "ERROR")
        import traceback
        log_message(traceback.format_exc(), "❌", "ERROR")
        write_log_file()