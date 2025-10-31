import os
import json
import time
import threading
import logging
from dotenv import load_dotenv
import requests

# --- Load Environment Variables ---
load_dotenv()

# --- Constants ---
FIGMA_API_TOKEN = os.getenv("FIGMA_API_TOKEN")
FIGMA_FILE_URL = os.getenv("FIGMA_FILE_URL")
OUTPUT_DIR = "Figma-analysis-data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "Figma-analysis-data.txt")
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "Log-for-figmy-analysis.txt")

# --- Setup Logging (FIXED) ---
def setup_logging():
    """Sets up logging with UTF-8 encoding to prevent UnicodeEncodeError."""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Prevent duplicate handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create a formatter
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # --- KEY CHANGE: Create a file handler with explicit UTF-8 encoding ---
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Create a stream handler for console output
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

# --- Figma API Interaction ---
def get_figma_file_data():
    """Fetches Figma file data from the API."""
    if not all([FIGMA_API_TOKEN, FIGMA_FILE_URL]):
        logging.error("❌ Figma API token or file URL not found in .env file.")
        return None

    try:
        # Extract file ID from URL
        file_id = FIGMA_FILE_URL.split("/")[4]
        api_url = f"https://api.figma.com/v1/files/{file_id}"
        headers = {"X-Figma-Token": FIGMA_API_TOKEN}
        
        logging.info(f"🚀 Fetching Figma data for file ID: {file_id}")
        response = requests.get(api_url, headers=headers)
        response.raise_for_status() # Raises an exception for bad status codes (4xx or 5xx)
        logging.info("✅ Successfully fetched Figma data.")
        return response.json()
        
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ API request failed: {e}")
        return None
    except IndexError:
        logging.error(f"❌ Invalid Figma URL. Could not extract file ID from: {FIGMA_FILE_URL}")
        return None

# --- AI-Based Analysis ---
def get_ai_decision(section_name):
    """
    Simulates an AI decision for section type classification.
    In a real application, you would replace this with a call to an AI model like Gemini.
    """
    # Simulate a network delay to respect potential API rate limits
    time.sleep(10)

    # This is a placeholder logic. A real AI would analyze the image/structure.
    name_lower = section_name.lower()
    if any(keyword in name_lower for keyword in ["list", "trainers", "choose us", "team"]):
        return "Repeater"
    if any(keyword in name_lower for keyword in ["testimonials", "services", "service details", "stories", "news"]):
        return "CPT (Custom post type)"
    return "Normal section"

# --- Tree Extraction and Processing ---
def get_node_icon(node_name):
    """Determines the correct icon for a node based on its name."""
    name_lower = node_name.lower()
    hash_keywords = [
        'hero', 'section', 'container', 'blogs', 'solutions', 'join',
        'case stud', 'industries', 'experts', 'about', 'contact',
        'cta', 'promo', 'update', 'services', 'openings'
    ]
    if any(keyword in name_lower for keyword in hash_keywords):
        return "#"
    return "🗂️"

def process_figma_data(data):
    """Processes Figma JSON data and generates markdown content with AI decisions."""
    if not data:
        logging.error("❌ No data received from Figma API to process.")
        return

    try:
        canvas = data["document"]["children"][0]
        all_top_level_frames = canvas["children"]
        
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

        # Open the output file with UTF-8 encoding
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            for page_node in all_top_level_frames:
                page_name = page_node.get("name", "Unnamed Page")
                page_icon = get_node_icon(page_name)
                f.write(f"v {page_icon} {page_name}\n")
                
                threads = []
                child_nodes = page_node.get("children", [])
                
                for child_node in child_nodes:
                    # The file_handle 'f' is passed to each thread
                    thread = threading.Thread(target=process_child_node, args=(child_node, f))
                    threads.append(thread)
                    thread.start()

                # Wait for all threads for the current page to complete
                for thread in threads:
                    thread.join()
                
                f.write("\n---\n\n")
        
        logging.info(f"✅ Markdown analysis complete. File saved at: {OUTPUT_FILE}")

    except (KeyError, IndexError) as e:
        logging.error(f"❌ Failed to parse Figma JSON structure. Error: {e}")

def process_child_node(child_node, file_handle):
    """Analyzes a child node (section) and writes the formatted result to the file."""
    child_name = child_node.get("name", "Unnamed Section")
    section_icon = get_node_icon(child_name)
    
    logging.info(f"🤖 Analyzing section: '{child_name}'...")
    ai_decision = get_ai_decision(child_name)
    logging.info(f"✅ AI decision for '{child_name}': {ai_decision}")

    # Create the formatted string
    output_line = f"  > {section_icon} {child_name} :- {ai_decision}\n"
    
    # Write the result directly to the file handle
    file_handle.write(output_line)

# --- Main Execution ---
if __name__ == "__main__":
    setup_logging()
    logging.info("🌟 --- Starting Figma Analysis Script --- 🌟")
    
    figma_data = get_figma_file_data()
    if figma_data:
        process_figma_data(figma_data)
        
    logging.info("🏁 --- Script Finished --- 🏁")