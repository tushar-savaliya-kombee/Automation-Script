import os
import re
import time
import threading
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from pymongo import MongoClient
from bson import ObjectId

class Logger:
    def __init__(self, log_file):
        self.log_file = log_file
        self.lock = threading.Lock()
        self.log_queue = Queue()

        # Configure sys.stdout for UTF-8
        sys.stdout.reconfigure(encoding='utf-8')
        
        # Clear log file
        with open(self.log_file, 'w', encoding='utf-8') as f:
            f.write("")
    
    def log(self, message, icon="ℹ️", level="INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {icon} [{level}] {message}\n"
        
        with self.lock:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)
            print(log_entry.strip())
    
    def log_section(self, title):
        separator = "=" * 80
        with self.lock:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(f"\n{separator}\n")
                f.write(f"🔷 {title}\n")
                f.write(f"{separator}\n\n")
            print(f"\n{'=' * 80}")
            print(f"🔷 {title}")
            print(f"{'=' * 80}\n")
    
    def log_subsection(self, title):
        with self.lock:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(f"\n{'─' * 60}\n")
                f.write(f"📌 {title}\n")
                f.write(f"{'─' * 60}\n\n")
            print(f"\n{'─' * 60}")
            print(f"📌 {title}")
            print(f"{'─' * 60}\n")
    
    def log_success(self, message):
        self.log(message, "✅", "SUCCESS")
    
    def log_error(self, message):
        self.log(message, "❌", "ERROR")
    
    def log_warning(self, message):
        self.log(message, "⚠️", "WARNING")
    
    def log_info(self, message):
        self.log(message, "ℹ️", "INFO")
    
    def log_process(self, message):
        self.log(message, "⚙️", "PROCESS")


class WordPressCPTGenerator:
    def __init__(self):
        self.start_time = time.time()
        self.project_path = None
        self.figma_analysis_path = None
        self.cpt_sections_path = None
        self.template_path = None
        self.blog_post_data_path = None
        self.logger = None
        self.gemini_model = None
        self.cpt_sections_data = []
        self.mongo_client = None
        self.mongo_db = None
        
    def initialize(self):
        """Initialize the generator"""
        # Load environment variables
        load_dotenv()
        
        # Get project path
        self.project_path = os.getenv('PROJECT_THEME_PATH')
        gemini_api_key = os.getenv('GEMINI_API_KEY')
        gemini_model_name = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
        mongo_uri = os.getenv('MONGO_URI', 'mongodb://127.0.0.1:27017/wordpress-automation')
        
        if not self.project_path:
            raise ValueError("PROJECT_THEME_PATH not found in .env file")
        
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY not found in .env file")
        
        # Setup paths
        self.project_path = Path(self.project_path)
        self.figma_analysis_path = self.project_path / "Figma-analysis-data"
        self.cpt_sections_path = self.figma_analysis_path / "CPT-Sections-Data.txt"
        self.template_path = self.project_path / "template"
        self.blog_post_data_path = self.project_path / "Blog-Post-Data"
        
        # Initialize logger
        log_file = self.project_path / "Log-for-Modification.txt"
        self.logger = Logger(log_file)
        
        # Configure Gemini AI
        genai.configure(api_key=gemini_api_key)
        self.gemini_model = genai.GenerativeModel(gemini_model_name)
        
        # Connect to MongoDB
        self.connect_to_mongodb(mongo_uri)
        
        self.logger.log_section("🚀 WORDPRESS CPT GENERATOR STARTED")
        self.logger.log_success(f"Project Path: {self.project_path}")
        self.logger.log_success(f"Gemini Model: {gemini_model_name}")
    
    def connect_to_mongodb(self, mongo_uri):
        """Connect to MongoDB database"""
        try:
            self.logger.log_info(f"Connecting to MongoDB: {mongo_uri}")
            self.mongo_client = MongoClient(mongo_uri)
            
            # Extract database name from URI
            db_name = mongo_uri.split('/')[-1].split('?')[0]
            self.mongo_db = self.mongo_client[db_name]
            
            # Test connection
            self.mongo_client.server_info()
            self.logger.log_success(f"Connected to MongoDB database: {db_name}")
        except Exception as e:
            self.logger.log_error(f"Failed to connect to MongoDB: {str(e)}")
            raise
    
    def get_latest_document_from_collection(self, collection):
        """Get the latest document from a MongoDB collection"""
        try:
            # Sort by _id in descending order to get the latest document
            latest_doc = collection.find_one(sort=[("_id", -1)])
            return latest_doc
        except Exception as e:
            self.logger.log_error(f"Error getting latest document: {str(e)}")
            return None
    
    def clean_section_name(self, section_name):
        """Clean section name by removing special characters and emojis"""
        if not section_name:
            return ""
        
        # Remove emojis and special prefixes
        cleaned = re.sub(r'^(v\s*[🗂️#]\s*|#\s*|[🗂️]\s*)', '', section_name)
        cleaned = re.sub(r'\s*:-\s*CPT.*$', '', cleaned)
        cleaned = cleaned.strip()
        
        return cleaned
    
    def should_exclude_section(self, section_name):
        """Check if section should be excluded based on exclusion rules"""
        # Add your exclusion logic here if needed
        # For now, we'll keep all sections
        return False
    
    def fetch_cpt_sections_from_mongodb(self):
        """Fetch CPT sections data from MongoDB using aggregation query"""
        self.logger.log_section("� FAETCHING CPT SECTIONS FROM MONGODB")
        
        try:
            # Get all collections in the database
            collection_names = self.mongo_db.list_collection_names()
            
            if not collection_names:
                self.logger.log_error("No collections found in database")
                return None
            
            self.logger.log_info(f"Found {len(collection_names)} collection(s): {', '.join(collection_names)}")
            
            # Use the first collection (or you can add logic to select specific one)
            collection_name = collection_names[0]
            collection = self.mongo_db[collection_name]
            self.logger.log_success(f"Using collection: {collection_name}")
            
            # Get the latest document from the collection
            latest_document = self.get_latest_document_from_collection(collection)
            
            if not latest_document:
                self.logger.log_error("No document found in collection")
                return None
            
            # Get document identifier for logging
            doc_identifier = latest_document.get('_id', 'Unknown')
            self.logger.log_success(f"Processing latest document: {doc_identifier}")
            self.logger.log_info("Applying blog-exclusion filter (all variants starting with 'blog' will be excluded)")
            
            # MongoDB Aggregation Pipeline - process only the latest document
            pipeline = [
                # Match only the latest document by _id
                {"$match": {"_id": latest_document['_id']}},
                # Flatten pages and sections
                {"$unwind": "$pages"},
                {"$unwind": "$pages.sections"},
                # Only CPT type sections
                {"$match": {"pages.sections.type": "CPT (Custom post type)"}},
                # Exclude ALL blog variants (case-insensitive)
                # This regex matches any section name that contains "blog" anywhere (with emojis, spaces, etc.)
                {"$match": {
                    "pages.sections.name": {
                        "$not": {"$regex": "blog", "$options": "i"}
                    }
                }},
                # Add cleaned section field
                {"$addFields": {"cleanSectionName": "$pages.sections.name"}},
                # Group by section name -> find which pages it appears in
                {"$group": {
                    "_id": "$cleanSectionName",
                    "pages": {"$addToSet": "$pages.page"}
                }},
                # Split into similar and unique sections
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
            self.logger.log_info("Executing MongoDB aggregation pipeline...")
            result = list(collection.aggregate(pipeline))
            
            if not result:
                self.logger.log_warning("No CPT sections found in MongoDB")
                return None
            
            cpt_data = result[0]
            
            # Log the raw MongoDB response
            self.logger.log_subsection("📋 MONGODB AGGREGATION RESPONSE")
            import json
            try:
                # Pretty print the MongoDB response
                formatted_response = json.dumps(cpt_data, indent=2, default=str)
                self.logger.log_info(f"Raw MongoDB Response:\n{formatted_response}")
            except Exception as e:
                self.logger.log_warning(f"Could not format MongoDB response: {str(e)}")
                self.logger.log_info(f"Raw MongoDB Response: {cpt_data}")
            
            # Clean section names and filter out excluded sections
            self.logger.log_info("Cleaning section names and applying exclusion filters")
            
            # Filter similar sections
            filtered_similar = []
            for section in cpt_data.get('similarSections', []):
                cleaned_name = self.clean_section_name(section['sectionName'])
                if not self.should_exclude_section(cleaned_name):
                    section['sectionName'] = cleaned_name
                    filtered_similar.append(section)
            
            cpt_data['similarSections'] = filtered_similar
            
            # Filter unique sections
            filtered_unique = []
            for page_data in cpt_data.get('uniqueSections', []):
                cleaned_names = []
                for name in page_data['sectionNames']:
                    cleaned_name = self.clean_section_name(name)
                    if not self.should_exclude_section(cleaned_name):
                        cleaned_names.append(cleaned_name)
                
                # Only include page if it has at least one non-excluded section
                if cleaned_names:
                    page_data['sectionNames'] = cleaned_names
                    filtered_unique.append(page_data)
            
            cpt_data['uniqueSections'] = filtered_unique
            
            similar_count = len(cpt_data.get('similarSections', []))
            unique_count = len(cpt_data.get('uniqueSections', []))
            
            self.logger.log_success(f"After filtering: {similar_count} similar sections (appearing on multiple pages)")
            self.logger.log_success(f"After filtering: {unique_count} pages with unique sections")
            
            # Log details
            for section in cpt_data.get('similarSections', []):
                self.logger.log_info(f"Similar Section: '{section['sectionName']}' appears on pages: {', '.join(section['pages'])}")
            
            for page_data in cpt_data.get('uniqueSections', []):
                self.logger.log_info(f"Unique Sections on '{page_data['page']}': {', '.join(page_data['sectionNames'])}")
            
            return cpt_data
            
        except Exception as e:
            self.logger.log_error(f"Error fetching CPT sections from MongoDB: {str(e)}")
            import traceback
            self.logger.log_error(traceback.format_exc())
            return None
    
    def convert_mongodb_data_to_cpt_sections(self, mongodb_data):
        """Convert MongoDB data format to internal cpt_sections_data format"""
        self.logger.log_section("🔄 CONVERTING MONGODB DATA TO INTERNAL FORMAT")
        
        if not mongodb_data:
            self.logger.log_warning("No MongoDB data to convert")
            return []
        
        cpt_sections_data = []
        
        # Process similar sections (sections appearing on multiple pages)
        for similar_section in mongodb_data.get('similarSections', []):
            section_name = similar_section['sectionName']
            pages = similar_section['pages']
            
            for page in pages:
                # Find if page already exists in cpt_sections_data
                page_entry = next((p for p in cpt_sections_data if p['page_name'] == page), None)
                
                if page_entry:
                    page_entry['sections'].append(section_name)
                else:
                    cpt_sections_data.append({
                        'page_name': page,
                        'sections': [section_name]
                    })
        
        # Process unique sections (sections appearing on single page)
        for unique_page in mongodb_data.get('uniqueSections', []):
            page_name = unique_page['page']
            section_names = unique_page['sectionNames']
            
            # Find if page already exists in cpt_sections_data
            page_entry = next((p for p in cpt_sections_data if p['page_name'] == page_name), None)
            
            if page_entry:
                page_entry['sections'].extend(section_names)
            else:
                cpt_sections_data.append({
                    'page_name': page_name,
                    'sections': section_names
                })
        
        total_sections = sum(len(p['sections']) for p in cpt_sections_data)
        self.logger.log_success(f"Converted {len(cpt_sections_data)} pages with {total_sections} total sections")
        
        return cpt_sections_data
    
    def generate_cpt_sections_file(self):
        """Generate CPT-Sections-Data.txt file"""
        self.logger.log_section("📝 GENERATING CPT SECTIONS DATA FILE")
        
        if not self.cpt_sections_data:
            self.logger.log_warning("No CPT sections data available")
            return
        
        output_content = []
        
        for page_data in self.cpt_sections_data:
            page_name = page_data['page_name']
            sections = page_data['sections']
            
            output_content.append(f"v 🗂️ {page_name}")
            for section in sections:
                # Format section name properly
                output_content.append(f"  > {section} :- CPT (Custom post type)")
            output_content.append("\n---\n")
        
        # Write to file
        self.cpt_sections_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.cpt_sections_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_content))
        
        self.logger.log_success(f"CPT sections file created: {self.cpt_sections_path}")
    
    def extract_section_code(self, php_file, section_name):
        """Extract section code from PHP file"""
        self.logger.log_info(f"Extracting section: {section_name} from {php_file.name}")
        
        if not php_file.exists():
            self.logger.log_error(f"PHP file not found: {php_file}")
            return None
        
        with open(php_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # Clean section_name for regex matching
        # Remove leading 'v 🗂️' or 'v #' and trailing ':- CPT' from section_name if present
        # And also remove '#' from the beginning if it exists for cases like "# Team Section"
        cleaned_section_name = re.sub(r'^(v\s*[🗂️#]\s*|#\s*)', '', section_name)
        cleaned_section_name = re.sub(r'\s*:-\s*CPT$', '', cleaned_section_name)
        cleaned_section_name = cleaned_section_name.strip()
        # Remove any leading/trailing whitespace or special characters that might remain
        cleaned_section_name = re.sub(r'[^a-zA-Z0-9\s]', '', cleaned_section_name).strip()

        self.logger.log_info(f"Cleaned section name for regex: '{cleaned_section_name}'")
        
        # Try different comment patterns
        patterns = [
            (r"//<!--\s*START:\s*{}\s*-->".format(re.escape(cleaned_section_name)), r"//<!--\s*END:\s*{}\s*-->".format(re.escape(cleaned_section_name))),
            (r"<!--\s*START:\s*{}\s*-->".format(re.escape(cleaned_section_name)), r"<!--\s*END:\s*{}\s*-->".format(re.escape(cleaned_section_name))),
            (r"<!--\s*START:\s*{}\s*Section\s*-->".format(re.escape(cleaned_section_name)), r"<!--\s*END:\s*{}\s*Section\s*-->".format(re.escape(cleaned_section_name))),
            (r"//\s*START:\s*{}".format(re.escape(cleaned_section_name)), r"//\s*END:\s*{}".format(re.escape(cleaned_section_name))),
        ]
        
        for start_pattern, end_pattern in patterns:
            start_match = re.search(start_pattern, content, re.IGNORECASE)
            if start_match:
                end_match = re.search(end_pattern, content[start_match.end():], re.IGNORECASE)
                if end_match:
                    section_code = content[start_match.start():start_match.end() + end_match.end()]
                    self.logger.log_success(f"Section code extracted ({len(section_code)} chars)")
                    return section_code
        
        self.logger.log_warning(f"Section markers not found for: {section_name}")
        return None
    
    def extract_cpt_slugs_from_functions_php(self):
        """Extract all registered CPT slugs from functions.php file"""
        self.logger.log_section("🔍 EXTRACTING CPT SLUGS FROM FUNCTIONS.PHP")
        
        functions_php_path = self.project_path / "functions.php"
        
        if not functions_php_path.exists():
            self.logger.log_error(f"functions.php not found at: {functions_php_path}")
            return {}
        
        self.logger.log_info(f"Reading functions.php: {functions_php_path}")
        
        try:
            with open(functions_php_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Dictionary to store CPT name -> slug mapping
            cpt_mapping = {}
            
            # Pattern to find register_post_type calls
            # Matches: register_post_type( 'slug', $args );
            register_pattern = r"register_post_type\s*\(\s*['\"]([^'\"]+)['\"]"
            
            # Pattern to find CPT function comments that contain the CPT name
            # Matches: This CPT Post Creation Code for [Name] with [Pages]
            comment_pattern = r"This CPT Post Creation Code for\s+([^with]+?)\s+with"
            
            # Find all register_post_type calls
            register_matches = re.finditer(register_pattern, content)
            comment_matches = re.finditer(comment_pattern, content)
            
            # Convert to lists for easier processing
            register_list = [(m.group(1), m.start()) for m in register_matches]
            comment_list = [(m.group(1).strip(), m.start()) for m in comment_matches]
            
            # Match comments with their corresponding register_post_type calls
            for cpt_name, comment_pos in comment_list:
                # Find the closest register_post_type call after this comment
                closest_slug = None
                min_distance = float('inf')
                
                for slug, register_pos in register_list:
                    if register_pos > comment_pos:
                        distance = register_pos - comment_pos
                        if distance < min_distance and distance < 2000:  # Within 2000 chars
                            min_distance = distance
                            closest_slug = slug
                
                if closest_slug:
                    cpt_mapping[cpt_name] = closest_slug
                    self.logger.log_success(f"Found CPT: '{cpt_name}' -> slug: '{closest_slug}'")
            
            if not cpt_mapping:
                self.logger.log_warning("No CPT mappings found in functions.php")
            else:
                self.logger.log_success(f"Total CPT mappings extracted: {len(cpt_mapping)}")
            
            return cpt_mapping
            
        except Exception as e:
            self.logger.log_error(f"Error reading functions.php: {str(e)}")
            import traceback
            self.logger.log_error(traceback.format_exc())
            return {}
    
    def get_cpt_slug_for_section(self, section_name):
        """Get the correct CPT slug for a given section name using extracted mappings"""
        if not hasattr(self, 'cpt_slug_mapping') or not self.cpt_slug_mapping:
            self.logger.log_warning("CPT slug mapping not initialized")
            # Fallback: generate slug from section name
            slug = re.sub(r'[^\w\s-]', '', section_name).strip().lower().replace(' ', '-')
            return slug
        
        # Try exact match first
        if section_name in self.cpt_slug_mapping:
            return self.cpt_slug_mapping[section_name]
        
        # Try case-insensitive match
        for cpt_name, slug in self.cpt_slug_mapping.items():
            if cpt_name.lower() == section_name.lower():
                return slug
        
        # Try partial match (if section name contains CPT name or vice versa)
        for cpt_name, slug in self.cpt_slug_mapping.items():
            # Normalize both strings for comparison
            normalized_section = section_name.lower().strip()
            normalized_cpt = cpt_name.lower().strip()
            
            # Check if one contains the other
            if normalized_cpt in normalized_section or normalized_section in normalized_cpt:
                self.logger.log_info(f"Partial match found: '{section_name}' matched with '{cpt_name}' -> '{slug}'")
                return slug
        
        # Fallback: generate slug from section name
        slug = re.sub(r'[^\w\s-]', '', section_name).strip().lower().replace(' ', '-')
        self.logger.log_warning(f"No CPT mapping found for '{section_name}', using generated slug: {slug}")
        return slug
    
    def modify_section_with_gemini(self, section_code, section_name, page_name, is_similar=False, all_pages=None, max_retries=5):
        """Use Gemini AI to modify section code for CPT support with retry logic"""
        self.logger.log_process(f"Modifying section with Gemini AI: {section_name}")
        
        # Get the correct CPT slug
        cpt_slug = self.get_cpt_slug_for_section(section_name)
        self.logger.log_info(f"Using CPT slug: {cpt_slug}")
        
        # Build additional requirements for similar sections
        if is_similar and all_pages:
            similar_section_note = f"""
**⚠️ CRITICAL - SIMILAR SECTION ALERT:**
This section appears on MULTIPLE pages: {', '.join(all_pages)}
The ACF field names MUST be CONSISTENT across all pages!

**MANDATORY ACF Field Naming Convention:**
- Use GENERIC field names: 'item_image', 'item_description', 'item_title'
- DO NOT use page-specific names like 'service_image', 'solution_image', 'service_description', 'solution_description'
- ALL pages using this CPT must use the SAME field names
- Example: get_field('item_image') NOT get_field('service_image') or get_field('solution_image')
"""
        else:
            similar_section_note = ""
        
        prompt = f"""You are a WordPress developer expert. I need you to modify the following PHP section code to use WordPress CPT (Custom Post Type) instead of hardcoded data.

**Page Name:** {page_name}
**Section Name:** {section_name}
**CPT Slug (MUST USE EXACTLY THIS):** {cpt_slug}
{similar_section_note}
**Current Code:**
```php
{section_code}
```

**CRITICAL REQUIREMENTS:**
1. Convert this section to fetch data from a WordPress Custom Post Type
2. **MUST USE EXACTLY THIS CPT SLUG:** '{cpt_slug}' (DO NOT change it, DO NOT use underscores, use EXACTLY as provided)
3. In WP_Query, use: 'post_type' => '{cpt_slug}'
4. Use get_field() for ACF custom fields with CONSISTENT naming
5. Maintain the existing HTML structure and CSS classes
6. Add proper WordPress loops and conditional checks
7. Keep the START and END comment markers exactly as they are
8. Make sure the code is production-ready and follows WordPress best practices

**IMPORTANT:** The CPT slug '{cpt_slug}' is already registered in functions.php. You MUST use this exact slug with hyphens, not underscores.

**Return ONLY the modified PHP code, nothing else. Do not include explanations or markdown formatting.**
"""
        
        for attempt in range(max_retries):
            try:
                response = self.gemini_model.generate_content(prompt)
                modified_code = response.text.strip()
                
                # Remove markdown code blocks if present
                modified_code = re.sub(r'^```php\n?', '', modified_code)
                modified_code = re.sub(r'\n?```$', '', modified_code)
                
                self.logger.log_success(f"Section modified successfully ({len(modified_code)} chars)")
                return modified_code
            
            except Exception as e:
                error_str = str(e)
                
                # Check if it's a rate limit error (429)
                if "429" in error_str or "quota" in error_str.lower():
                    # Extract retry delay from error message
                    retry_match = re.search(r'retry in (\d+(?:\.\d+)?)', error_str)
                    if retry_match:
                        retry_delay = float(retry_match.group(1)) + 2  # Add 2 seconds buffer
                    else:
                        retry_delay = 60  # Default to 60 seconds
                    
                    if attempt < max_retries - 1:
                        self.logger.log_warning(f"Rate limit hit. Waiting {retry_delay:.0f} seconds before retry (attempt {attempt + 1}/{max_retries})...")
                        time.sleep(retry_delay)
                    else:
                        self.logger.log_error(f"Max retries reached. Gemini API error: {error_str}")
                        return None
                else:
                    # Non-rate-limit error, don't retry
                    self.logger.log_error(f"Gemini API error: {error_str}")
                    return None
        
        return None
    
    def replace_section_in_file(self, php_file, old_code, new_code):
        """Replace section code in PHP file"""
        self.logger.log_process(f"Replacing section in file: {php_file.name}")
        
        with open(php_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if old_code not in content:
            self.logger.log_error("Original section code not found in file")
            return False
        
        new_content = content.replace(old_code, new_code)
        
        with open(php_file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        self.logger.log_success("Section replaced successfully")
        return True
    
    def extract_section_codes_from_all_pages(self, section_name, pages_list):
        """Extract section code from all pages where this section appears"""
        self.logger.log_info(f"Extracting section code from {len(pages_list)} pages")
        
        section_codes = {}
        
        for page in pages_list:
            # Get PHP filename
            safe_page_name = re.sub(r'[^\w\s-]', '', page).strip().replace(' ', '-').lower()
            php_file = self.template_path / f"{safe_page_name}.php"
            
            if not php_file.exists():
                # Try alternative naming
                php_file = self.template_path / f"{safe_page_name.replace('-', '')}.php"
                if not php_file.exists():
                    self.logger.log_warning(f"PHP template not found for page: {page}")
                    continue
            
            # Extract section code
            section_code = self.extract_section_code(php_file, section_name)
            if section_code:
                section_codes[page] = section_code
                self.logger.log_success(f"Extracted section code from {page} ({len(section_code)} chars)")
        
        return section_codes
    
    def generate_acf_documentation_with_gemini(self, section_name, page_name, pages_list=None, max_retries=5):
        """Generate ACF field documentation using Gemini AI with retry logic"""
        self.logger.log_process(f"Generating ACF documentation for: {section_name}")
        
        # Handle pages_list parameter
        if pages_list is None:
            pages_list = [page_name]
        
        # Check if this is a similar section (multiple pages)
        is_similar = len(pages_list) > 1
        
        # Extract actual section codes from all pages
        section_codes_dict = {}
        if is_similar:
            section_codes_dict = self.extract_section_codes_from_all_pages(section_name, pages_list)
        
        if is_similar and section_codes_dict:
            # Build section codes display for prompt
            section_codes_display = ""
            for page, code in section_codes_dict.items():
                section_codes_display += f"\n\n**=== CODE FROM PAGE: {page} ===**\n```php\n{code}\n```\n"
            
            pages_info = f"""**Pages where this section appears:** {', '.join(pages_list)}
**⚠️ CRITICAL:** This section appears on MULTIPLE pages. Analyze the ACTUAL code from each page below.

{section_codes_display}

**YOUR TASK:**
1. Analyze the get_field() calls in EACH page's code
2. Identify which fields are used INSIDE the WP_Query loop (these are CPT fields)
3. EXCLUDE fields used OUTSIDE the loop (like solutions_tagline, solutions_heading, solutions_description, solutions_cta_text, solutions_cta_link - these are page-level fields, NOT CPT fields)
4. Create a UNIFIED field structure using GENERIC names (item_image, item_description, item_title, etc.)
5. Document fields that exist in one page but not another
"""
            additional_req = """
8. **CRITICAL:** Only document fields that are used INSIDE the WP_Query while loop (CPT fields)
9. DO NOT include page-level fields like section headings, taglines, descriptions, or CTA buttons
10. Use GENERIC field names: 'item_image', 'item_description', 'item_title', 'item_button_text', 'item_page_link'
11. Format the output showing fields per page like:
    
    **{section_name}_Combined_ACF_Fields ({', '.join(pages_list)})**
    │
    ├── **Page: {pages_list[0]}**
    │   ├── item_image (image)
    │   ├── item_title (text)
    │   └── item_description (textarea)
    │
    └── **Page: {pages_list[1] if len(pages_list) > 1 else ''}**
        ├── item_image (image)
        ├── item_title (text)
        ├── item_description (textarea)
        ├── item_button_text (text)
        ├── item_page_link (link)
        └── challenge_questions (repeater)
            └── challenge_text (text)
"""
        else:
            pages_info = f"**Page Name:** {page_name}"
            additional_req = ""
        
        prompt = f"""You are a WordPress ACF (Advanced Custom Fields) expert. Generate comprehensive ACF field documentation for the following section.

{pages_info}
**Section Name:** {section_name}

**Requirements:**
1. Analyze the provided PHP code carefully
2. Identify ONLY the ACF fields used INSIDE the WP_Query loop (CPT fields)
3. EXCLUDE any fields used outside the loop (page-level fields)
4. Define a Custom Post Type with appropriate fields
5. Include WordPress default fields (Title, Featured Image, Content)
6. Use proper field types (Text, textarea, wysiwyg, group, repeater, image, gallery, select, checkbox, radio button, true/false, page link, link, Number, URL, Repeater, etc.)
7. Follow the format specified above
{additional_req}
**Generate ONLY the markdown content. No explanations, no extra text.**
"""
        
        for attempt in range(max_retries):
            try:
                response = self.gemini_model.generate_content(prompt)
                documentation = response.text.strip()
                
                self.logger.log_success(f"ACF documentation generated ({len(documentation)} chars)")
                return documentation
            
            except Exception as e:
                error_str = str(e)
                
                # Check if it's a rate limit error (429)
                if "429" in error_str or "quota" in error_str.lower():
                    # Extract retry delay from error message
                    retry_match = re.search(r'retry in (\d+(?:\.\d+)?)', error_str)
                    if retry_match:
                        retry_delay = float(retry_match.group(1)) + 2  # Add 2 seconds buffer
                    else:
                        retry_delay = 60  # Default to 60 seconds
                    
                    if attempt < max_retries - 1:
                        self.logger.log_warning(f"Rate limit hit. Waiting {retry_delay:.0f} seconds before retry (attempt {attempt + 1}/{max_retries})...")
                        time.sleep(retry_delay)
                    else:
                        self.logger.log_error(f"Max retries reached. Gemini API error: {error_str}")
                        return None
                else:
                    # Non-rate-limit error, don't retry
                    self.logger.log_error(f"Gemini API error: {error_str}")
                    return None
        
        return None
    
    def save_acf_documentation(self, documentation, section_name, page_name_or_pages):
        """Save ACF documentation to file"""
        safe_section_name = re.sub(r'[^\w\s-]', '', section_name).strip().replace(' ', '-').lower()
        
        # Check if this is a combined documentation (page_name_or_pages is "combined" or a list)
        if page_name_or_pages == "combined" or isinstance(page_name_or_pages, list):
            # Get all pages for this section
            all_pages = self.get_all_pages_for_section(section_name)
            if all_pages:
                # Create filename with all page names
                pages_str = '-'.join([re.sub(r'[^\w\s-]', '', p).strip().replace(' ', '-').lower() for p in all_pages])
                filename = f"ACF-{safe_section_name}-Combined-({pages_str}).txt"
            else:
                filename = f"ACF-{safe_section_name}-Combined.txt"
        else:
            # Single page documentation
            safe_page_name = re.sub(r'[^\w\s-]', '', page_name_or_pages).strip().replace(' ', '-').lower()
            filename = f"ACF-{safe_page_name}-{safe_section_name}.txt"
        
        filepath = self.blog_post_data_path / filename
        self.blog_post_data_path.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(documentation)
        
        self.logger.log_success(f"ACF documentation saved: {filename}")
        return filepath
    
    def is_similar_section(self, section_name):
        """Check if this section appears on multiple pages (similarSection)"""
        if not hasattr(self, 'similar_sections_cache'):
            self.similar_sections_cache = {}
            
            # Build cache from cpt_sections_data
            for page_data in self.cpt_sections_data:
                for section in page_data['sections']:
                    if section not in self.similar_sections_cache:
                        self.similar_sections_cache[section] = []
                    self.similar_sections_cache[section].append(page_data['page_name'])
        
        # Return True if section appears on more than one page
        return len(self.similar_sections_cache.get(section_name, [])) > 1
    
    def get_all_pages_for_section(self, section_name):
        """Get all pages where this section appears"""
        if not hasattr(self, 'similar_sections_cache'):
            self.is_similar_section(section_name)  # Build cache
        
        return self.similar_sections_cache.get(section_name, [])
    
    def process_section(self, page_name, section_name):
        """Process a single CPT section"""
        thread_id = threading.get_ident()
        self.logger.log_subsection(f"🔧 Processing Section (Thread: {thread_id})")
        
        # Section name is already cleaned from MongoDB
        self.logger.log_info(f"Section: {section_name}")
        self.logger.log_info(f"Page: {page_name}")
        
        # Check if this is a similar section (appears on multiple pages)
        is_similar = self.is_similar_section(section_name)
        all_pages = self.get_all_pages_for_section(section_name)
        
        if is_similar:
            self.logger.log_info(f"⚠️ Similar Section detected - appears on pages: {', '.join(all_pages)}")
            self.logger.log_warning("ACF fields should be consistent across all pages for this section!")

        # Define a mapping for known section name discrepancies
        section_name_map = {
            'Home Page': {
                '# Testimonial Section': 'Reviews Section',
            },
            'Membership': {
                '🗂️ Service Items': 'Service Items', # Match what's in the PHP file.
            }
        }

        # Determine the actual section name in the PHP file
        php_section_name = section_name
        if page_name in section_name_map and section_name in section_name_map[page_name]:
            php_section_name = section_name_map[page_name][section_name]
            self.logger.log_info(f"Mapping '{section_name}' to '{php_section_name}' for page '{page_name}'")

        # Get PHP filename
        safe_page_name = re.sub(r'[^\w\s-]', '', page_name).strip().replace(' ', '-').lower()
        php_file = self.template_path / f"{safe_page_name}.php"
        
        if not php_file.exists():
            # Try alternative naming
            php_file = self.template_path / f"{safe_page_name.replace('-', '')}.php"
            if not php_file.exists():
                self.logger.log_error(f"PHP template not found: {php_file}")
                return False
        
        self.logger.log_success(f"Found template: {php_file.name}")
        
        # Extract section code
        section_code = self.extract_section_code(php_file, php_section_name)
        if not section_code:
            return False
        
        # Modify with Gemini AI
        modified_code = self.modify_section_with_gemini(section_code, php_section_name, page_name, is_similar, all_pages)
        if not modified_code:
            return False
        
        # Replace in file
        success = self.replace_section_in_file(php_file, section_code, modified_code)
        if not success:
            return False
        
        # Generate ACF documentation
        # For similar sections, only generate once (check if already generated)
        if is_similar:
            # Check if documentation already exists for this section
            acf_doc_key = f"acf_doc_{php_section_name}"
            if not hasattr(self, 'generated_acf_docs'):
                self.generated_acf_docs = set()
            
            if acf_doc_key not in self.generated_acf_docs:
                # Generate combined ACF documentation for all pages
                acf_doc = self.generate_acf_documentation_with_gemini(php_section_name, page_name, all_pages)
                if acf_doc:
                    self.save_acf_documentation(acf_doc, php_section_name, "combined")
                    self.generated_acf_docs.add(acf_doc_key)
                    self.logger.log_info(f"📄 Combined ACF documentation created for similar section")
            else:
                self.logger.log_info(f"📄 ACF documentation already exists for this similar section")
        else:
            # Unique section - generate documentation normally
            acf_doc = self.generate_acf_documentation_with_gemini(php_section_name, page_name, [page_name])
            if acf_doc:
                self.save_acf_documentation(acf_doc, php_section_name, page_name)
        
        self.logger.log_success(f"✨ Section processing completed: {php_section_name}")
        return True
    
    def process_all_sections_multithreaded(self):
        """Process all CPT sections using multithreading"""
        self.logger.log_section("⚡ PROCESSING ALL CPT SECTIONS (MULTITHREADED)")
        
        if not self.cpt_sections_data:
            self.logger.log_warning("No CPT sections to process")
            return
        
        # Prepare tasks
        tasks = []
        for page_data in self.cpt_sections_data:
            page_name = page_data['page_name']
            for section_name in page_data['sections']:
                tasks.append((page_name, section_name))
        
        self.logger.log_info(f"Total tasks: {len(tasks)}")
        self.logger.log_info(f"Using ThreadPoolExecutor with max 3 workers")
        
        # Process with thread pool
        successful = 0
        failed = 0
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_task = {
                executor.submit(self.process_section, page_name, section_name): (page_name, section_name)
                for page_name, section_name in tasks
            }
            
            for future in as_completed(future_to_task):
                page_name, section_name = future_to_task[future]
                try:
                    result = future.result()
                    if result:
                        successful += 1
                    else:
                        failed += 1
                except Exception as e:
                    self.logger.log_error(f"Exception in thread: {str(e)}")
                    failed += 1
        
        self.logger.log_section("📊 PROCESSING SUMMARY")
        self.logger.log_success(f"Successful: {successful}")
        if failed > 0:
            self.logger.log_error(f"Failed: {failed}")
        else:
            self.logger.log_success(f"Failed: {failed}")
    
    def run(self):
        """Main execution method"""
        try:
            # Initialize
            self.initialize()
            
            # Extract CPT slugs from functions.php
            self.cpt_slug_mapping = self.extract_cpt_slugs_from_functions_php()
            
            # Fetch CPT sections from MongoDB
            mongodb_data = self.fetch_cpt_sections_from_mongodb()
            
            if not mongodb_data:
                self.logger.log_error("Failed to fetch data from MongoDB. Exiting.")
                return
            
            # Convert MongoDB data to internal format
            self.cpt_sections_data = self.convert_mongodb_data_to_cpt_sections(mongodb_data)
            
            # Generate CPT sections file
            self.generate_cpt_sections_file()
            
            # Process all sections with multithreading
            self.process_all_sections_multithreaded()
            
            # Calculate execution time
            end_time = time.time()
            execution_time = end_time - self.start_time
            
            self.logger.log_section("🎉 PROCESS COMPLETED SUCCESSFULLY")
            self.logger.log_success(f"Total Execution Time: {execution_time:.2f} seconds")
            self.logger.log_success(f"Total Execution Time: {execution_time/60:.2f} minutes")
            
        except Exception as e:
            self.logger.log_error(f"Fatal error: {str(e)}")
            import traceback
            self.logger.log_error(traceback.format_exc())
        finally:
            # Close MongoDB connection
            if self.mongo_client:
                self.mongo_client.close()
                self.logger.log_info("MongoDB connection closed")


if __name__ == "__main__":
    generator = WordPressCPTGenerator()
    generator.run()