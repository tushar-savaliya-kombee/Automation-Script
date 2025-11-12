import os
import re
import json
import time
import threading
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient
import google.generativeai as genai

# Load environment variables
load_dotenv()

PROJECT_THEME_PATH = os.getenv('PROJECT_THEME_PATH')
MONGO_URI = os.getenv('MONGO_URI')
PORT = os.getenv('PORT', '5000')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-pro')

# Global variables for logging and tracking
log_file_path = None
log_lock = threading.Lock()
total_tokens_used = 0
tokens_lock = threading.Lock()

# Beautiful icons for logging
ICONS = {
    'start': '🚀',
    'success': '✅',
    'error': '❌',
    'warning': '⚠️',
    'info': 'ℹ️',
    'process': '⚙️',
    'file': '📄',
    'folder': '📁',
    'database': '💾',
    'ai': '🤖',
    'thread': '🧵',
    'time': '⏱️',
    'check': '✓',
    'arrow': '➜',
    'bullet': '•',
    'search': '🔍',
    'delete': '🗑️',
    'save': '💿',
    'complete': '🎉',
    'debug': '🐛'
}

def log_message(message, icon='info', level='INFO'):
    """Thread-safe logging with beautiful formatting"""
    with log_lock:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        icon_symbol = ICONS.get(icon, ICONS['info'])
        
        separator = '=' * 80
        log_entry = f"[{timestamp}] {icon_symbol} [{level}] {message}\n"
        
        print(log_entry.strip())
        
        if log_file_path:
            with open(log_file_path, 'a', encoding='utf-8') as f:
                f.write(log_entry)

def log_section(title):
    """Log a section header"""
    with log_lock:
        separator = '=' * 80
        header = f"\n{separator}\n{ICONS['arrow']} {title}\n{separator}\n"
        print(header)
        if log_file_path:
            with open(log_file_path, 'a', encoding='utf-8') as f:
                f.write(header)

def update_token_count(tokens):
    """Thread-safe token counting"""
    global total_tokens_used
    with tokens_lock:
        total_tokens_used += tokens

def initialize_log_file():
    """Initialize the log file"""
    global log_file_path
    log_file_path = os.path.join(PROJECT_THEME_PATH, 'Log-for-FindingCPT.txt')
    
    with open(log_file_path, 'w', encoding='utf-8') as f:
        header = f"""
{'=' * 80}
{ICONS['start']} ACF FIELD REPEATER REMOVER - EXECUTION LOG
{'=' * 80}
Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Project Path: {PROJECT_THEME_PATH}
{'=' * 80}

"""
        f.write(header)
    log_message(f"Log file initialized at: {log_file_path}", 'success')

def get_project_name():
    """Extract project name from PROJECT_THEME_PATH"""
    project_name = os.path.basename(PROJECT_THEME_PATH)
    log_message(f"Project name extracted: {project_name}", 'info')
    return project_name

def convert_markdown_to_json_with_gemini(markdown_content):
    """Convert markdown content to JSON using Gemini AI"""
    log_message("Converting markdown to JSON using Gemini AI", 'ai')
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        prompt = f"""
        Please convert the following markdown data into a valid JSON format.
        The JSON should be an array of objects, where each object represents a page
        and contains a 'page' key with the page name and a 'sections' key with an
        array of section objects. Each section object should have 'name' and 'type' keys.

Analyze the following Figma analysis data and convert it into a structured JSON array.

IMPORTANT: Create a JSON array where each object represents a page with the following structure:
{{
  "page": "Page Name",
  "sections": [
    {{
      "name": "Section Name",
      "type": "Section Type (e.g., 'CPT (Custom post type)', 'Static', 'Hero', etc.)"
    }}
  ]
}}

Rules:
1. Extract all page names from the markdown
2. For each page, identify all sections/components
3. The "type" field for each section MUST be copied EXACTLY as it appears in the markdown, without any interpretation or modification.
4. Return ONLY valid JSON, no explanations or markdown code blocks
5. Ensure all page names and section names are preserved exactly as they appear

Markdown Content:
{markdown_content}

Return ONLY the JSON array, starting with [ and ending with ].
"""
        
        response = model.generate_content(prompt)
        
        # Track tokens used
        if hasattr(response, 'usage_metadata'):
            tokens = getattr(response.usage_metadata, 'total_token_count', 0)
            update_token_count(tokens)
            log_message(f"Tokens used in this conversion: {tokens}", 'info')
        
        # Extract JSON from response
        json_text = response.text.strip()
        
        # Remove markdown code blocks if present
        json_text = re.sub(r'^```json\s*', '', json_text)
        json_text = re.sub(r'^```\s*', '', json_text)
        json_text = re.sub(r'\s*```$', '', json_text)
        json_text = json_text.strip()
        
        # Ensure it starts with [ or {
        if not json_text.startswith('[') and not json_text.startswith('{'):
            # Try to find JSON in the text
            json_match = re.search(r'(\[.*\]|\{.*\})', json_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
        
        json_data = json.loads(json_text)
        
        # Ensure it's an array
        if isinstance(json_data, dict):
            json_data = [json_data]
        
        log_message("Successfully converted markdown to JSON", 'success')
        log_message(f"Generated {len(json_data)} page entries", 'info')
        
        return json_data
        
    except Exception as e:
        log_message(f"Error converting markdown to JSON: {str(e)}", 'error', 'ERROR')
        raise

def read_figma_analysis_data():
    """Read the Figma analysis data file"""
    log_section("READING FIGMA ANALYSIS DATA")
    
    figma_folder = os.path.join(PROJECT_THEME_PATH, 'Figma-analysis-data')
    figma_file = os.path.join(figma_folder, 'Figma-analysis-data.txt')
    
    log_message(f"Looking for file: {figma_file}", 'search')
    
    if not os.path.exists(figma_file):
        log_message(f"Figma analysis file not found at {figma_file}", 'error', 'ERROR')
        raise FileNotFoundError(f"File not found: {figma_file}")
    
    with open(figma_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    log_message(f"Successfully read {len(content)} characters from Figma analysis file", 'success')
    return content

def get_next_document_number(collection):
    """Get the next document number for auto-increment"""
    existing_docs = list(collection.find({}, {'_id': 1}).sort('_id', -1).limit(1))
    
    if not existing_docs:
        return 1
    
    last_id = existing_docs[0]['_id']
    
    # Extract number from document ID (e.g., "document_1" -> 1)
    match = re.search(r'(\d+)$', str(last_id))
    if match:
        return int(match.group(1)) + 1
    return 1

def store_json_in_mongodb(json_data, project_name):
    """Store JSON data in MongoDB with auto-increment document naming"""
    log_section("STORING DATA IN MONGODB")
    
    try:
        client = MongoClient(MONGO_URI)
        db = client.get_database()
        
        # Use project name as collection name (sanitize it)
        collection_name = re.sub(r'[^a-zA-Z0-9_]', '_', project_name).lower()
        collection = db[collection_name]
        
        log_message(f"Using collection: {collection_name}", 'database')
        
        # Get next document number
        doc_number = get_next_document_number(collection)
        document_id = f"document_{doc_number}"
        
        log_message(f"Creating document: {document_id}", 'info')
        
        # Prepare document - store as pages array directly
        document = {
            '_id': document_id,
            'created_at': datetime.now(),
            'pages': json_data  # Store as 'pages' key for easier querying
        }
        
        # Insert document
        collection.insert_one(document)
        
        log_message(f"Successfully stored document '{document_id}' in collection '{collection_name}'", 'success')
        log_message(f"Document contains {len(json_data)} pages", 'info')
        
        return collection_name, document_id
        
    except Exception as e:
        log_message(f"Error storing data in MongoDB: {str(e)}", 'error', 'ERROR')
        raise

def fetch_cpt_sections_from_mongodb(collection_name):
    """Fetch CPT sections data from MongoDB"""
    log_section("FETCHING CPT SECTIONS FROM MONGODB")
    
    try:
        client = MongoClient(MONGO_URI)
        db = client.get_database()
        collection = db[collection_name]
        
        log_message("Fetching documents from MongoDB", 'search')
        
        # Get all documents
        documents = list(collection.find({}))
        
        if not documents:
            log_message("No documents found in collection", 'warning', 'WARNING')
            return []
        
        log_message(f"Found {len(documents)} document(s) in collection", 'info')
        
        # Use the latest document
        latest_doc = max(documents, key=lambda x: x.get('created_at', datetime.min))
        log_message(f"Using document: {latest_doc['_id']}", 'info')
        
        # Get pages data - check different possible structures
        pages_data = None
        if 'pages' in latest_doc:
            pages_data = latest_doc['pages']
        elif 'data' in latest_doc:
            pages_data = latest_doc['data']
        else:
            # The document itself might be the data structure
            pages_data = latest_doc
        
        log_message(f"Analyzing data structure...", 'debug')
        
        # Debug: Show structure
        if isinstance(pages_data, list):
            log_message(f"Data is a list with {len(pages_data)} items", 'debug')
            if pages_data:
                log_message(f"First item keys: {list(pages_data[0].keys()) if isinstance(pages_data[0], dict) else 'Not a dict'}", 'debug')
        elif isinstance(pages_data, dict):
            log_message(f"Data is a dict with keys: {list(pages_data.keys())}", 'debug')
        
        # Process data to find CPT sections
        cpt_results = []
        
        # Handle different data structures
        pages_list = []
        if isinstance(pages_data, list):
            pages_list = pages_data
        elif isinstance(pages_data, dict):
            # If it's a dict, it might have pages nested somewhere
            if 'pages' in pages_data:
                pages_list = pages_data['pages']
            elif 'data' in pages_data:
                pages_list = pages_data['data']
            else:
                # Try to extract from other possible keys
                for key in pages_data.keys():
                    if isinstance(pages_data[key], list):
                        pages_list = pages_data[key]
                        break
        
        log_message(f"Processing {len(pages_list)} pages", 'process')
        
        for idx, page_data in enumerate(pages_list):
            if not isinstance(page_data, dict):
                log_message(f"Skipping item {idx}: not a dictionary", 'debug')
                continue
            
            page_name = page_data.get('page', page_data.get('name', page_data.get('pageName', '')))
            sections = page_data.get('sections', page_data.get('components', []))
            
            if not page_name:
                log_message(f"Skipping item {idx}: no page name found", 'debug')
                continue
            
            log_message(f"Analyzing page: {page_name} ({len(sections)} sections)", 'debug')
            
            cpt_sections = []
            for section in sections:
                if isinstance(section, dict):
                    section_type = section.get('type', section.get('sectionType', ''))
                    section_name = section.get('name', section.get('sectionName', section.get('title', '')))
                    
                    log_message(f"  Section: {section_name} | Type: {section_type}", 'debug')
                    
                    # Check if it's a CPT section (case insensitive)
                    if 'cpt' in section_type.lower() or 'custom post type' in section_type.lower():
                        if section_name:
                            cpt_sections.append(section_name)
                            log_message(f"  ✓ Found CPT section: {section_name}", 'success')
            
            if cpt_sections:
                cpt_results.append({
                    'page': page_name,
                    'cpt_sections': cpt_sections
                })
                log_message(f"Page '{page_name}' has {len(cpt_sections)} CPT sections", 'success')
        
        log_message(f"Found {len(cpt_results)} pages with CPT sections", 'success')
        
        if cpt_results:
            log_message("Summary of CPT sections found:", 'info')
            for result in cpt_results:
                log_message(f"  {ICONS['bullet']} {result['page']}: {', '.join(result['cpt_sections'])}", 'info')
        else:
            log_message("No CPT sections found in any page", 'warning', 'WARNING')
            log_message("This might mean:", 'info')
            log_message("  1. No sections are marked as 'CPT (Custom post type)' type", 'info')
            log_message("  2. The JSON structure doesn't match expected format", 'info')
            log_message("  3. Check the generated JSON in MongoDB to verify", 'info')
        
        return cpt_results
        
    except Exception as e:
        log_message(f"Error fetching CPT sections: {str(e)}", 'error', 'ERROR')
        import traceback
        log_message(f"Traceback: {traceback.format_exc()}", 'error', 'ERROR')
        raise

def normalize_filename(page_name):
    """Normalize page name to match ACF field filenames"""
    # Remove leading 'v', emojis, and other non-alphanumeric characters first
    # This handles cases like 'v 🗂️ Home' -> 'Home'
    cleaned_name = re.sub(r'^[vV]\s*[^a-zA-Z0-9]*', '', page_name).strip()
    
    # Then remove spaces, special characters, convert to lowercase
    normalized = re.sub(r'[^a-zA-Z0-9]', '', cleaned_name).lower()
    return f"{normalized}-ACF-fields.txt"

def calculate_similarity(str1, str2):
    """Calculate similarity between two strings using character-level matching"""
    str1, str2 = str1.lower(), str2.lower()
    
    # Exact match
    if str1 == str2:
        return 1.0
    
    # Substring match
    if str1 in str2 or str2 in str1:
        return 0.9
    
    # Character overlap ratio
    set1, set2 = set(str1), set(str2)
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    
    if union == 0:
        return 0.0
    
    return intersection / union

def remove_repeater_blocks_from_file(file_path, section_names):
    """Remove repeater blocks for specific sections from ACF field file"""
    log_message(f"Processing file: {os.path.basename(file_path)}", 'process')
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        original_content = ''.join(lines)
        sections_processed = 0
        repeaters_removed = 0
        
        for section_name in section_names:
            # Normalize section name for matching (remove emojis and special chars)
            clean_section_name = re.sub(r'[^\w\s-]', '', section_name).strip()
            
            log_message(f"  {ICONS['search']} Looking for section: {section_name}", 'info')
            log_message(f"  {ICONS['debug']} Normalized search name: '{clean_section_name}'", 'debug')
            
            # Find the tab line with best match
            tab_line_idx = -1
            best_match_score = 0.0
            best_match_name = ""
            
            for i, line in enumerate(lines):
                # More robust check for a Tab line, capturing the tab content
                # Pattern matches: *   **Tab: Name** or *   **Tab: 1. Name**
                match = re.search(r'^\s*\*\s*\*\*?Tab:\s*(.+?)(\*\*)?$', line, re.IGNORECASE)
                if match:
                    # Extract the tab's name directly from the file line
                    tab_name_in_file = match.group(1).strip()
                    
                    # Remove numbering pattern like "1. ", "2. ", etc.
                    tab_name_cleaned = re.sub(r'^\d+\.\s*', '', tab_name_in_file)
                    
                    # Clean the extracted tab name for comparison (remove emojis, special chars, extra asterisks)
                    clean_tab_name_in_file = re.sub(r'[^\w\s-]', '', tab_name_cleaned).strip().lower()
                    clean_search_name = clean_section_name.lower()
                    
                    # Use multiple matching strategies
                    # Strategy 1: Word-based matching
                    search_words = set(clean_search_name.split())
                    tab_words = set(clean_tab_name_in_file.split())
                    
                    # Remove common words that don't help matching
                    common_words = {'section', 'the', 'a', 'an', 'and', 'or', 'of', 'for', 'in', 'on', 'at', 'with'}
                    search_words_filtered = search_words - common_words
                    tab_words_filtered = tab_words - common_words
                    
                    word_overlap_score = 0.0
                    if search_words_filtered and tab_words_filtered:
                        overlap = search_words_filtered & tab_words_filtered
                        word_overlap_score = len(overlap) / min(len(search_words_filtered), len(tab_words_filtered))
                    
                    # Strategy 2: Character similarity
                    char_similarity = calculate_similarity(clean_search_name, clean_tab_name_in_file)
                    
                    # Strategy 3: Substring matching
                    substring_score = 0.0
                    if clean_search_name in clean_tab_name_in_file or clean_tab_name_in_file in clean_search_name:
                        substring_score = 0.8
                    
                    # Strategy 4: Key word presence (for specific cases)
                    key_word_score = 0.0
                    # Extract key words from search name (longest words)
                    if search_words_filtered:
                        key_word = max(search_words_filtered, key=len)
                        if len(key_word) >= 4:  # Only consider words with 4+ chars
                            for tab_word in tab_words:
                                if key_word in tab_word or tab_word in key_word:
                                    key_word_score = 0.7
                                    break
                    
                    # Combined score (weighted average)
                    combined_score = max(
                        word_overlap_score * 1.0,
                        char_similarity * 0.8,
                        substring_score,
                        key_word_score
                    )
                    
                    log_message(f"  {ICONS['debug']} Tab '{tab_name_cleaned}': word={word_overlap_score:.2f}, char={char_similarity:.2f}, substr={substring_score:.2f}, key={key_word_score:.2f}, combined={combined_score:.2f}", 'debug')
                    
                    # Update best match if this is better
                    if combined_score > best_match_score and combined_score >= 0.5:
                        best_match_score = combined_score
                        tab_line_idx = i
                        best_match_name = tab_name_in_file
            
            if tab_line_idx == -1:
                log_message(f"  {ICONS['warning']} Section '{section_name}' not found in file (best score: {best_match_score:.2f})", 'warning', 'WARNING')
                continue
            
            log_message(f"  {ICONS['success']} Found section at line {tab_line_idx+1} with tab name '{best_match_name}' (score: {best_match_score:.2f})", 'success')
            
            # Now find and remove repeater blocks in this section
            # Section goes from current tab to next tab (or end of file)
            section_start = tab_line_idx + 1
            section_end = len(lines)
            
            # Find next tab
            for i in range(section_start, len(lines)):
                if re.search(r'\*\s*\*\*Tab:', lines[i], re.IGNORECASE):
                    section_end = i
                    break
            
            log_message(f"  {ICONS['debug']} Section spans lines {section_start+1} to {section_end}", 'debug')
            
            # Process lines in this section to find and mark repeater blocks for removal
            i = section_start
            lines_to_remove = []
            
            while i < section_end:
                line = lines[i]
                
                # Check if this line is a repeater field
                # Pattern: *   `field_name` (Repeater)
                if re.search(r'\*\s+`[^`]+`\s+\(Repeater\)', line):
                    log_message(f"  {ICONS['debug']} Found repeater at line {i+1}: {line.strip()[:50]}...", 'debug')
                    
                    # Mark this line for removal
                    lines_to_remove.append(i)
                    
                    # Get the indentation level of the repeater line
                    repeater_indent = len(line) - len(line.lstrip())
                    
                    # Now find all content that belongs to this repeater
                    # This includes: sub-fields, groups, and fields inside groups
                    j = i + 1
                    while j < section_end:
                        next_line = lines[j]
                        
                        # Skip empty lines
                        if next_line.strip() == '':
                            j += 1
                            continue
                        
                        next_indent = len(next_line) - len(next_line.lstrip())
                        
                        # If indentation is greater than repeater, it belongs to the repeater
                        if next_indent > repeater_indent:
                            # Check if it's a meaningful line (starts with * or has content)
                            if next_line.strip().startswith('*'):
                                lines_to_remove.append(j)
                                
                                # Determine what type of line this is
                                if re.search(r'\*\s+`[^`]+`', next_line):
                                    # It's a field
                                    log_message(f"  {ICONS['debug']}   Field at line {j+1}: {next_line.strip()[:60]}...", 'debug')
                                elif re.search(r'\*\s+\*\*Group:', next_line, re.IGNORECASE):
                                    # It's a group header
                                    log_message(f"  {ICONS['debug']}   Group at line {j+1}: {next_line.strip()[:60]}...", 'debug')
                                else:
                                    # Other content within repeater
                                    log_message(f"  {ICONS['debug']}   Content at line {j+1}: {next_line.strip()[:60]}...", 'debug')
                            j += 1
                        else:
                            # Indentation is same or less - end of repeater block
                            break
                    
                    repeaters_removed += 1
                    log_message(f"  {ICONS['debug']} Repeater block ends at line {j}", 'debug')
                    i = j  # Continue from after the repeater block
                else:
                    i += 1
            
            if lines_to_remove:
                sections_processed += 1
                log_message(f"  {ICONS['delete']} Removing {len(lines_to_remove)} lines for {repeaters_removed} repeater block(s)", 'success')
                
                # Remove lines in reverse order to maintain indices
                for idx in reversed(lines_to_remove):
                    del lines[idx]
        
        new_content = ''.join(lines)
        
        if new_content != original_content:
            # Write the updated content back to file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            log_message(f"  {ICONS['save']} File updated - {sections_processed} section(s) processed, {repeaters_removed} repeater(s) removed", 'success')
            return True
        else:
            log_message(f"  {ICONS['info']} No changes made to file", 'info')
            return False
            
    except Exception as e:
        log_message(f"  {ICONS['error']} Error processing file: {str(e)}", 'error', 'ERROR')
        import traceback
        log_message(f"  {ICONS['error']} Traceback: {traceback.format_exc()}", 'error', 'ERROR')
        return False

def process_page_thread(page_data, acf_folder):
    """Thread worker function to process a single page"""
    page_name = page_data['page']
    cpt_sections = page_data['cpt_sections']
    
    thread_name = threading.current_thread().name
    log_message(f"Thread {thread_name} processing page: {page_name}", 'thread')
    
    # Find the corresponding ACF field file
    acf_filename = normalize_filename(page_name)
    acf_file_path = os.path.join(acf_folder, acf_filename)
    
    log_message(f"  {ICONS['search']} Looking for file: {acf_filename}", 'info')
    
    if not os.path.exists(acf_file_path):
        log_message(f"  {ICONS['warning']} ACF field file not found: {acf_filename}", 'warning', 'WARNING')
        return False
    
    # Remove repeater blocks
    success = remove_repeater_blocks_from_file(acf_file_path, cpt_sections)
    
    log_message(f"Thread {thread_name} completed processing page: {page_name}", 'thread')
    return success

def process_acf_files_with_threading(cpt_data):
    """Process ACF field files using multithreading"""
    log_section("PROCESSING ACF FIELD FILES WITH MULTITHREADING")
    
    acf_folder = os.path.join(PROJECT_THEME_PATH, 'ACF Fields')
    
    log_message(f"ACF Fields folder: {acf_folder}", 'folder')
    
    if not os.path.exists(acf_folder):
        log_message(f"ACF Fields folder not found: {acf_folder}", 'error', 'ERROR')
        raise FileNotFoundError(f"Folder not found: {acf_folder}")
    
    # Create threads for each page
    threads = []
    max_threads = min(len(cpt_data), 10)  # Limit to 10 concurrent threads
    
    log_message(f"Creating {len(cpt_data)} worker threads", 'thread')
    
    for i, page_data in enumerate(cpt_data):
        thread = threading.Thread(
            target=process_page_thread,
            args=(page_data, acf_folder),
            name=f"Worker-{i+1}"
        )
        threads.append(thread)
    
    # Start all threads
    log_message(f"Starting {len(threads)} threads", 'thread')
    for thread in threads:
        thread.start()
    
    # Wait for all threads to complete
    log_message("Waiting for all threads to complete...", 'thread')
    for thread in threads:
        thread.join()
    
    log_message(f"All {len(threads)} threads completed successfully", 'complete')

def main():
    """Main execution function"""
    start_time = time.time()
    
    try:
        # Initialize log file
        initialize_log_file()
        
        log_section("SCRIPT EXECUTION STARTED")
        log_message(f"Project Theme Path: {PROJECT_THEME_PATH}", 'info')
        log_message(f"MongoDB URI: {MONGO_URI}", 'info')
        log_message(f"Gemini Model: {GEMINI_MODEL}", 'info')
        
        # Step 1: Get project name
        project_name = get_project_name()
        
        # Step 2: Read Figma analysis data
        markdown_content = read_figma_analysis_data()
        
        # Step 3: Convert markdown to JSON using Gemini AI
        json_data = convert_markdown_to_json_with_gemini(markdown_content)
        
        # Step 4: Store JSON in MongoDB
        collection_name, document_id = store_json_in_mongodb(json_data, project_name)
        
        # Step 5: Fetch CPT sections data
        cpt_data = fetch_cpt_sections_from_mongodb(collection_name)
        
        if not cpt_data:
            log_message("No CPT sections found. Exiting.", 'warning', 'WARNING')
            log_message("Please check the generated JSON in MongoDB to verify the structure", 'info')
            return
        
        # Step 6: Process ACF field files with multithreading
        process_acf_files_with_threading(cpt_data)
        
        # Calculate execution time
        end_time = time.time()
        execution_time = end_time - start_time
        
        # Final summary
        log_section("EXECUTION SUMMARY")
        log_message(f"Total execution time: {execution_time:.2f} seconds", 'time')
        log_message(f"Total Gemini AI tokens used: {total_tokens_used}", 'ai')
        log_message(f"Pages processed: {len(cpt_data)}", 'check')
        log_message("Script execution completed successfully!", 'complete')
        
        print(f"\n{ICONS['complete']} {'=' * 80}")
        print(f"{ICONS['time']} Total Execution Time: {execution_time:.2f} seconds")
        print(f"{ICONS['ai']} Total Gemini AI Tokens Used: {total_tokens_used}")
        print(f"{'=' * 80}\n")
        
    except Exception as e:
        log_message(f"Fatal error: {str(e)}", 'error', 'ERROR')
        import traceback
        log_message(f"Traceback: {traceback.format_exc()}", 'error', 'ERROR')
        log_message("Script execution failed!", 'error', 'ERROR')
        raise

if __name__ == "__main__":
    main()