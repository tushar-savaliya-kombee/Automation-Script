import os
import json
import time
import threading
import logging
from datetime import timedelta
from dotenv import load_dotenv
import requests
import google.generativeai as genai
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Load Environment Variables ---
load_dotenv()

# --- Constants ---
FIGMA_API_TOKEN = os.getenv("FIGMA_API_TOKEN")
FIGMA_FILE_URL = os.getenv("FIGMA_FILE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")
PROJECT_THEME_PATH = os.getenv("PROJECT_THEME_PATH")

OUTPUT_DIR = os.path.join(PROJECT_THEME_PATH, "Figma-analysis-data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "Figma-analysis-data.txt")
LOG_FILE = os.path.join(PROJECT_THEME_PATH, "Log-for-figmy-analysis.txt")

# Threading configuration
MAX_WORKERS = 3  # Number of concurrent API calls

# Token tracking
total_prompt_tokens = 0
total_completion_tokens = 0
total_tokens = 0
token_lock = threading.Lock()

# --- Configure Gemini AI ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- Beautiful Logging with Icons ---
class ColoredFormatter(logging.Formatter):
    """Custom formatter with beautiful icons and structure."""
    
    ICONS = {
        'DEBUG': '🔍',
        'INFO': '💡',
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '🚨'
    }
    
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'
    }
    
    def format(self, record):
        icon = self.ICONS.get(record.levelname, '📌')
        
        # Add icon to the message
        original_msg = record.msg
        record.msg = f"{icon} {original_msg}"
        
        # Format the record
        formatted = super().format(record)
        
        # Restore original message
        record.msg = original_msg
        
        return formatted

def setup_logging():
    """Sets up beautiful logging with icons and proper structure."""
    if not os.path.exists(PROJECT_THEME_PATH):
        os.makedirs(PROJECT_THEME_PATH)
    
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    if logger.hasHandlers():
        logger.handlers.clear()

    # File handler with detailed format
    file_formatter = ColoredFormatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # Console handler with simplified format
    console_formatter = ColoredFormatter(
        "%(message)s"
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(console_formatter)
    stream_handler.setLevel(logging.INFO)
    logger.addHandler(stream_handler)

def log_section_header(title):
    """Logs a beautiful section header."""
    separator = "═" * 70
    logging.info("")
    logging.info(f"╔{separator}╗")
    logging.info(f"║ {title:^68} ║")
    logging.info(f"╚{separator}╝")
    logging.info("")

def log_subsection(title):
    """Logs a subsection header."""
    logging.info(f"\n┌─ {title} " + "─" * (65 - len(title)))

def log_item(icon, label, value):
    """Logs an item with icon, label, and value."""
    logging.info(f"│ {icon}  {label}: {value}")

def log_progress(current, total, item_name=""):
    """Logs progress with a progress bar."""
    percentage = (current / total) * 100
    bar_length = 40
    filled = int(bar_length * current / total)
    bar = "█" * filled + "░" * (bar_length - filled)
    logging.info(f"│ 📊  Progress: [{bar}] {percentage:.1f}% ({current}/{total}) {item_name}")

# --- Figma API Interaction ---
def get_figma_file_data():
    """Fetches Figma file data from the API."""
    log_subsection("Fetching Figma Data")
    
    if not all([FIGMA_API_TOKEN, FIGMA_FILE_URL, GEMINI_API_KEY, GEMINI_MODEL]):
        logging.error("Required environment variables are missing in .env file")
        log_item("❌", "Status", "FAILED - Missing credentials")
        return None

    try:
        file_id = FIGMA_FILE_URL.split("/")[4]
        api_url = f"https://api.figma.com/v1/files/{file_id}"
        headers = {"X-Figma-Token": FIGMA_API_TOKEN}
        
        log_item("🔑", "File ID", file_id)
        log_item("🌐", "API URL", api_url)
        
        start_time = time.time()
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        elapsed = time.time() - start_time
        
        log_item("✅", "Status", f"SUCCESS (took {elapsed:.2f}s)")
        log_item("📦", "Response Size", f"{len(response.content) / 1024:.2f} KB")
        
        return response.json()
        
    except requests.exceptions.RequestException as e:
        logging.error(f"API request failed: {e}")
        log_item("❌", "Status", "FAILED")
        return None
    except IndexError:
        logging.error(f"Invalid Figma URL: {FIGMA_FILE_URL}")
        log_item("❌", "Status", "FAILED - Invalid URL")
        return None

# --- AI-Based Batch Analysis ---
def get_batch_ai_decision(sections_dict):
    """
    Uses Gemini AI to classify multiple sections in a single API call.
    sections_dict: {page_name: [section_names]}
    Returns: {section_name: decision}
    """
    global total_prompt_tokens, total_completion_tokens, total_tokens
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        # Build the batch prompt
        sections_list = []
        for page_name, section_names in sections_dict.items():
            for section_name in section_names:
                sections_list.append(f"- {section_name}")
        
        all_sections_text = "\n".join(sections_list)
        
        prompt = (
            "You are an expert WordPress developer. Analyze the following Figma section names and classify each "
            "into one of these categories: 'Repeater', 'CPT', or 'Normal'.\n\n"
            "**Categories:**\n"
            "- **Repeater**: Sections with repeating items (team lists, features, pricing, logos, values, why choose us)\n"
            "- **CPT**: Custom Post Types - distinct content entries (testimonials, services, case studies, blog posts, portfolio)\n"
            "- **Normal**: Everything else (hero, CTA, about text, contact forms, headers, footers)\n\n"
            "**Sections to classify:**\n"
            f"{all_sections_text}\n\n"
            "**Output format:** Return ONLY a JSON object where keys are section names and values are classifications.\n"
            "Example: {\"Hero section\": \"Normal\", \"Team Section\": \"Repeater\", \"Blog Section\": \"CPT\"}\n\n"
            "IMPORTANT: Return ONLY the JSON object, no other text or markdown formatting."
        )
        
        logging.debug(f"Sending batch request with {len(sections_list)} sections")
        
        response = model.generate_content(prompt)
        
        # Track token usage
        if hasattr(response, 'usage_metadata'):
            with token_lock:
                prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                completion_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens
                total_tokens += (prompt_tokens + completion_tokens)
                logging.debug(f"Tokens used - Prompt: {prompt_tokens}, Completion: {completion_tokens}")
        
        response_text = response.text.strip()
        
        # Clean up response - remove markdown code blocks if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1]) if len(lines) > 2 else response_text
        response_text = response_text.replace("```json", "").replace("```", "").strip()
        
        # Parse JSON response
        decisions = json.loads(response_text)
        
        # Validate and normalize decisions
        valid_decisions = {"Repeater", "CPT", "Normal"}
        normalized_decisions = {}
        
        for section_name, decision in decisions.items():
            # Normalize decision
            decision_clean = decision.strip().replace("(Custom post type)", "").strip()
            
            if decision_clean not in valid_decisions:
                logging.warning(f"Unexpected decision '{decision}' for '{section_name}'. Using 'Normal'")
                normalized_decisions[section_name] = "Normal"
            else:
                # Convert back to full format if needed
                if decision_clean == "CPT":
                    normalized_decisions[section_name] = "CPT (Custom post type)"
                else:
                    normalized_decisions[section_name] = decision_clean + " section"
        
        return normalized_decisions
        
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse AI response as JSON: {e}")
        logging.debug(f"Raw response: {response_text}")
        return {}
    except Exception as e:
        logging.error(f"Batch AI analysis failed: {e}")
        return {}

def process_page_batch(page_name, child_nodes):
    """Processes a single page's sections with batch AI analysis."""
    log_subsection(f"Processing Page: {page_name}")
    
    section_names = [node.get("name", "Unnamed Section") for node in child_nodes]
    
    log_item("📄", "Page Name", page_name)
    log_item("🔢", "Sections Count", len(section_names))
    
    if not section_names:
        logging.warning(f"No sections found in page '{page_name}'")
        return page_name, {}
    
    # Get batch decisions from AI
    start_time = time.time()
    decisions = get_batch_ai_decision({page_name: section_names})
    elapsed = time.time() - start_time
    
    log_item("⚡", "AI Analysis Time", f"{elapsed:.2f}s")
    log_item("✅", "Sections Analyzed", f"{len(decisions)}/{len(section_names)}")
    
    # Fill in missing decisions with default
    for section_name in section_names:
        if section_name not in decisions:
            logging.warning(f"No AI decision for '{section_name}', using default")
            decisions[section_name] = "Normal section"
    
    # Log decisions
    logging.debug("Decisions:")
    for section_name, decision in decisions.items():
        logging.debug(f"  • {section_name} → {decision}")
    
    return page_name, decisions

# --- Tree Extraction and Processing ---
def get_node_icon(node_name):
    """Determines the correct icon for a node based on its name."""
    name_lower = node_name.lower()
    
    icon_map = {
        'hero': '🦸',
        'header': '📌',
        'footer': '📎',
        'blog': '📝',
        'testimonial': '💬',
        'team': '👥',
        'about': 'ℹ️',
        'contact': '📞',
        'cta': '🎯',
        'service': '⚙️',
        'portfolio': '🎨',
        'case stud': '📊',
        'faq': '❓',
        'pricing': '💰',
    }
    
    for keyword, icon in icon_map.items():
        if keyword in name_lower:
            return icon
    
    # Default icons
    if any(kw in name_lower for kw in ['section', 'container']):
        return '📦'
    
    return '🗂️'

def process_figma_data(data):
    """Processes Figma JSON data with parallel batch AI analysis."""
    if not data:
        logging.error("No data received from Figma API")
        return
    
    log_section_header("PROCESSING FIGMA DATA")

    try:
        canvas = data["document"]["children"][0]
        all_top_level_frames = canvas["children"]
        
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)
        
        # Collect all pages with their sections
        pages_data = []
        for page_node in all_top_level_frames:
            page_name = page_node.get("name", "Unnamed Page")
            child_nodes = page_node.get("children", [])
            pages_data.append((page_name, child_nodes))
        
        total_sections = sum(len(children) for _, children in pages_data)
        
        log_item("📊", "Total Pages", len(pages_data))
        log_item("📊", "Total Sections", total_sections)
        log_item("🔧", "Thread Workers", MAX_WORKERS)
        
        # Process pages in parallel using thread pool
        log_subsection("Starting Batch AI Analysis")
        
        all_decisions = {}
        processed_pages = 0
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all page processing tasks
            future_to_page = {
                executor.submit(process_page_batch, page_name, children): page_name 
                for page_name, children in pages_data
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_page):
                page_name = future_to_page[future]
                try:
                    result_page_name, decisions = future.result()
                    all_decisions[result_page_name] = decisions
                    processed_pages += 1
                    
                    log_progress(processed_pages, len(pages_data), f"- {page_name}")
                    
                except Exception as e:
                    logging.error(f"Failed to process page '{page_name}': {e}")
                    all_decisions[page_name] = {}
        
        # Write output file
        log_subsection("Writing Output File")
        
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            for page_name, child_nodes in pages_data:
                page_icon = get_node_icon(page_name)
                f.write(f"v {page_icon} {page_name}\n")
                
                decisions = all_decisions.get(page_name, {})
                
                for child_node in child_nodes:
                    child_name = child_node.get("name", "Unnamed Section")
                    section_icon = get_node_icon(child_name)
                    decision = decisions.get(child_name, "Normal section")
                    
                    f.write(f"  > {section_icon} {child_name} :- {decision}\n")
                
                f.write("\n---\n\n")
        
        log_item("✅", "Output File", OUTPUT_FILE)
        log_item("✅", "Status", "SUCCESS")

    except (KeyError, IndexError) as e:
        logging.error(f"Failed to parse Figma JSON structure: {e}")
        log_item("❌", "Status", "FAILED")

# --- Main Execution ---
if __name__ == "__main__":
    # Record start time
    script_start_time = time.time()
    
    # Setup logging
    setup_logging()
    
    # Beautiful header
    log_section_header("FIGMA ANALYZER - BATCH AI PROCESSING")
    
    log_item("🕐", "Start Time", time.strftime("%Y-%m-%d %H:%M:%S"))
    log_item("🔧", "Model", GEMINI_MODEL or "Not Set")
    log_item("⚡", "Mode", f"Multi-threaded ({MAX_WORKERS} workers)")
    
    try:
        # Fetch Figma data
        figma_data = get_figma_file_data()
        
        # Process data if successful
        if figma_data:
            process_figma_data(figma_data)
        else:
            logging.error("Failed to fetch Figma data. Exiting.")
    
    except KeyboardInterrupt:
        logging.warning("Script interrupted by user")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
    
    # Calculate total execution time
    script_end_time = time.time()
    total_duration = script_end_time - script_start_time
    duration_formatted = str(timedelta(seconds=int(total_duration)))
    
    # Beautiful footer with execution time
    log_section_header("GENERATION SUMMARY")
    
    project_name = os.path.basename(PROJECT_THEME_PATH)
    
    # Count processed files
    total_files = 0
    if os.path.exists(OUTPUT_FILE):
        total_files = 1
    
    logging.info("")
    log_item("✅", "Project", project_name)
    log_item("📄", "Total Files Processed", total_files)
    log_item("🤖", "Gemini API Token Usage", "")
    log_item("  ", "  • Prompt Tokens", total_prompt_tokens)
    log_item("  ", "  • Completion Tokens", total_completion_tokens)
    log_item("  ", "  • Total Tokens", total_tokens)
    log_item("🏁", "Total Execution Time", f"{total_duration:.2f} seconds")
    logging.info("")
    
    # Performance indicator
    if total_duration < 60:
        log_item("🚀", "Speed", "Lightning Fast! ⚡")
    elif total_duration < 180:
        log_item("✅", "Speed", "Good Performance")
    else:
        log_item("🐌", "Speed", "Consider optimizing")
    
    logging.info("")
    logging.info("╔═══════════════════════════════════════════════════════════════════╗")
    logging.info("║                   🎉  SCRIPT FINISHED SUCCESSFULLY  🎉           ║")
    logging.info("╚═══════════════════════════════════════════════════════════════════╝")
    logging.info("")