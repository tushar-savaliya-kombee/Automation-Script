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
        
    def initialize(self):
        """Initialize the generator"""
        # Load environment variables
        load_dotenv()
        
        # Get project path
        self.project_path = os.getenv('PROJECT_PATH_FOR_CPT_GENERATION')
        gemini_api_key = os.getenv('GEMINI_API_KEY')
        gemini_model_name = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
        
        if not self.project_path:
            raise ValueError("PROJECT_PATH_FOR_CPT_GENERATION not found in .env file")
        
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
        
        self.logger.log_section("🚀 WORDPRESS CPT GENERATOR STARTED")
        self.logger.log_success(f"Project Path: {self.project_path}")
        self.logger.log_success(f"Gemini Model: {gemini_model_name}")
    
    def parse_figma_analysis(self):
        """Parse Figma analysis data to detect CPT sections"""
        self.logger.log_section("📖 PARSING FIGMA ANALYSIS DATA")
        
        figma_file = self.figma_analysis_path / "Figma-analysis-data.txt"
        
        if not figma_file.exists():
            self.logger.log_error(f"Figma analysis file not found: {figma_file}")
            return
        
        self.logger.log_info(f"Reading file: {figma_file}")
        
        with open(figma_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse pages and sections
        pages = content.split('---')
        cpt_data = []
        
        for page in pages:
            page = page.strip()
            if not page:
                continue
            
            lines = page.split('\n')
            page_name = None
            page_cpt_sections = []
            
            for line in lines:
                line = line.strip()
                
                # Detect page name
                if line.startswith('v 🗂️') or line.startswith('v #'):
                    # Remove the prefix "v 🗂️" or "v #" from the page name
                    page_name = re.sub(r'^(v\s*[🗂️#]\s*)', '', line).strip()
                    # Further clean the page_name to remove any stray emojis or non-alphanumeric characters
                    page_name = re.sub(r'[^a-zA-Z0-9\s]', '', page_name).strip()
                
                # Detect CPT sections
                if 'CPT (Custom post type)' in line:
                    # Extract section name
                    section_match = re.search(r'[>#]\s*(.+?)\s*:-\s*CPT', line)
                    if section_match:
                        section_name = section_match.group(1).strip()
                        page_cpt_sections.append(line)
                        self.logger.log_success(f"Found CPT: {section_name} in page: {page_name}")
            
            if page_name and page_cpt_sections:
                cpt_data.append({
                    'page_name': page_name,
                    'sections': page_cpt_sections
                })
        
        self.cpt_sections_data = cpt_data
        self.logger.log_success(f"Total CPT sections detected: {sum(len(p['sections']) for p in cpt_data)}")
        
        return cpt_data
    
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
                output_content.append(f"  {section}")
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
    
    def modify_section_with_gemini(self, section_code, section_name, page_name):
        """Use Gemini AI to modify section code for CPT support"""
        self.logger.log_process(f"Modifying section with Gemini AI: {section_name}")
        
        prompt = f"""You are a WordPress developer expert. I need you to modify the following PHP section code to use WordPress CPT (Custom Post Type) instead of hardcoded data.

**Page Name:** {page_name}
**Section Name:** {section_name}

**Current Code:**
```php
{section_code}
```

**Requirements:**
1. Convert this section to fetch data from a WordPress Custom Post Type
2. Create appropriate CPT slug based on section name (e.g., "Team Section" -> "team_member")
3. Use WP_Query to fetch posts
4. Use get_field() for ACF custom fields
5. Maintain the existing HTML structure and CSS classes
6. Add proper WordPress loops and conditional checks
7. Keep the START and END comment markers exactly as they are
8. Make sure the code is production-ready and follows WordPress best practices

**Return ONLY the modified PHP code, nothing else. Do not include explanations or markdown formatting.**
"""
        
        try:
            response = self.gemini_model.generate_content(prompt)
            modified_code = response.text.strip()
            
            # Remove markdown code blocks if present
            modified_code = re.sub(r'^```php\n?', '', modified_code)
            modified_code = re.sub(r'\n?```$', '', modified_code)
            
            self.logger.log_success(f"Section modified successfully ({len(modified_code)} chars)")
            return modified_code
        
        except Exception as e:
            self.logger.log_error(f"Gemini API error: {str(e)}")
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
    
    def generate_acf_documentation_with_gemini(self, section_name, page_name):
        """Generate ACF field documentation using Gemini AI"""
        self.logger.log_process(f"Generating ACF documentation for: {section_name}")
        
        prompt = f"""You are a WordPress ACF (Advanced Custom Fields) expert. Generate comprehensive ACF field documentation for the following section.

**Page Name:** {page_name}
**Section Name:** {section_name}

**Requirements:**
1. Create a markdown document with proper structure
2. Define main ACF fields for the section (text, textarea, wysiwyg, etc.)
3. Define a Custom Post Type with appropriate fields
4. Include WordPress default fields (Title, Featured Image, Content)
5. Add ACF custom fields specific to this CPT
6. Use proper field types (Text, Number, URL, Repeater, etc.)
7. Follow this exact format:

*   **Tab: [Section Name]**
    ACF Fields :-
    *   `field_name` (Field Type) - Description.
    *   `field_name_2` (Field Type) - Description.

CPT: cpt_slug :-
│
├── Title (WP default) → Purpose
├── Featured Image (WP default) → Purpose
├── Content (WP default) → Purpose
├── ACF: field_name (Field Type)
├── ACF: field_name_2 (Field Type)
└── ACF: repeater_field_name (Repeater)
      ├── sub_field_1 (Field Type)
      └── sub_field_2 (Field Type)

**Generate ONLY the markdown content following the format above. No explanations, no extra text.**
"""
        
        try:
            response = self.gemini_model.generate_content(prompt)
            documentation = response.text.strip()
            
            self.logger.log_success(f"ACF documentation generated ({len(documentation)} chars)")
            return documentation
        
        except Exception as e:
            self.logger.log_error(f"Gemini API error: {str(e)}")
            return None
    
    def save_acf_documentation(self, documentation, section_name, page_name):
        """Save ACF documentation to file"""
        # Create filename
        safe_page_name = re.sub(r'[^\w\s-]', '', page_name).strip().replace(' ', '-').lower()
        safe_section_name = re.sub(r'[^\w\s-]', '', section_name).strip().replace(' ', '-').lower()
        filename = f"ACF-{safe_page_name}-{safe_section_name}.txt"
        
        filepath = self.blog_post_data_path / filename
        self.blog_post_data_path.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(documentation)
        
        self.logger.log_success(f"ACF documentation saved: {filename}")
        return filepath
    
    def process_section(self, page_name, section_line):
        """Process a single CPT section"""
        thread_id = threading.get_ident()
        self.logger.log_subsection(f"🔧 Processing Section (Thread: {thread_id})")
        
        # Extract section name
        section_match = re.search(r'[>#]\s*(.+?)\s*:-\s*CPT', section_line)
        if not section_match:
            self.logger.log_error(f"Could not extract section name from: {section_line}")
            return False
        
        section_name = section_match.group(1).strip()
        self.logger.log_info(f"Section: {section_name}")
        self.logger.log_info(f"Page: {page_name}")

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
        modified_code = self.modify_section_with_gemini(section_code, php_section_name, page_name)
        if not modified_code:
            return False
        
        # Replace in file
        success = self.replace_section_in_file(php_file, section_code, modified_code)
        if not success:
            return False
        
        # Generate ACF documentation
        acf_doc = self.generate_acf_documentation_with_gemini(php_section_name, page_name)
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
            for section_line in page_data['sections']:
                tasks.append((page_name, section_line))
        
        self.logger.log_info(f"Total tasks: {len(tasks)}")
        self.logger.log_info(f"Using ThreadPoolExecutor with max 3 workers")
        
        # Process with thread pool
        successful = 0
        failed = 0
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_task = {
                executor.submit(self.process_section, page_name, section_line): (page_name, section_line)
                for page_name, section_line in tasks
            }
            
            for future in as_completed(future_to_task):
                page_name, section_line = future_to_task[future]
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
            
            # Parse Figma analysis
            self.parse_figma_analysis()
            
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


if __name__ == "__main__":
    generator = WordPressCPTGenerator()
    generator.run()