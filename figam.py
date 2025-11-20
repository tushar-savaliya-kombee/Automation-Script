import os
import re
import subprocess
import shutil
import time
import logging
import requests
from dotenv import load_dotenv
import google.generativeai as genai
from PIL import Image
from io import BytesIO
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import json

# --- Configuration & Setup ---

# Load environment variables from .env file
load_dotenv()

# Get configuration from environment
FIGMA_ACCESS_TOKEN = os.getenv("FIGMA_ACCESS_TOKEN")
FIGMA_FILE_URL = os.getenv("FIGMA_FILE_URL")
# print(f" - FIGMA_FILE_URL: {FIGMA_FILE_URL}") # Commented out for cleaner test output
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
MULTI_PAGE_MODE = os.getenv("MULTI_PAGE_MODE", "true").lower() == "true"
PAGE_PROCESSING_DELAY = int(os.getenv("PAGE_PROCESSING_DELAY", 3))
USE_FIGMA_PAGE_NAMES = os.getenv("USE_FIGMA_PAGE_NAMES", "true").lower() == "true"
IS_COMMAN_HEADER_FOOTER = os.getenv("IS_COMMAN_HEADER_FOOTER", "false").lower() == "true"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 3))  # Maximum number of concurrent threads
PROCESSING_DELAY = 3  # Fixed delay as requested


# Configure Google Gemini AI
genai.configure(api_key=GEMINI_API_KEY)

# --- Thread-Safe Rate Limiter ---
class ThreadSafeRateLimiter:
    """Thread-safe rate limiter to control API request frequency."""
    
    def __init__(self, max_requests_per_second=1):
        self.max_requests_per_second = max_requests_per_second
        self.min_interval = 1.0 / max_requests_per_second
        self.last_request_time = 0
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        """Wait if necessary to respect rate limits."""
        with self.lock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            
            if time_since_last < self.min_interval:
                sleep_time = self.min_interval - time_since_last
                time.sleep(sleep_time)
            
            self.last_request_time = time.time()

# Global rate limiter instances
figma_rate_limiter = ThreadSafeRateLimiter(max_requests_per_second=0.5)
gemini_rate_limiter = ThreadSafeRateLimiter(max_requests_per_second=0.33)

# --- Thread-Safe Logging Setup (Will be initialized later) ---
logger = None
log_lock = threading.Lock()
components_lock = threading.Lock()
file_write_lock = threading.Lock()

# --- Token Usage Tracking ---
class TokenUsageTracker:
    """Thread-safe tracker for Gemini API token usage."""
    
    def __init__(self):
        self.lock = threading.Lock()
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.api_calls = 0
        self.files_generated = 0
    
    def add_usage(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        """Add token usage from an API call."""
        with self.lock:
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_tokens += total_tokens
            self.api_calls += 1
    
    def increment_files(self, count=1):
        """Increment the count of generated files."""
        with self.lock:
            self.files_generated += count
    
    def get_summary(self):
        """Get a summary of token usage."""
        with self.lock:
            return {
                'prompt_tokens': self.total_prompt_tokens,
                'completion_tokens': self.total_completion_tokens,
                'total_tokens': self.total_tokens,
                'api_calls': self.api_calls,
                'files_generated': self.files_generated
            }

# Global token tracker
token_tracker = TokenUsageTracker()

class SafeConsoleFormatter(logging.Formatter):
    """Custom formatter that removes emojis for Windows console compatibility."""
    
    def format(self, record):
        # Format the message normally
        formatted = super().format(record)
        # Remove emojis and other non-ASCII characters for console
        try:
            # Try to encode as ASCII, if it fails, strip non-ASCII
            formatted.encode('cp1252')
            return formatted
        except UnicodeEncodeError:
            # Remove characters that can't be encoded in cp1252
            return formatted.encode('cp1252', errors='ignore').decode('cp1252')

def setup_logging(project_dir, project_name):
    """Initialize logging to file and console inside the project directory."""
    global logger
    
    log_formatter = logging.Formatter('%(asctime)s [%(threadName)s] %(message)s')
    console_formatter = SafeConsoleFormatter('%(asctime)s [%(threadName)s] %(message)s')
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Clear any existing handlers
    logger.handlers = []
    
    # Determine the log file name dynamically inside project directory
    log_file_name = f"generation_log_{project_name}.txt"
    log_file_path = os.path.join(project_dir, log_file_name)
    
    # If file exists, add numbering
    if os.path.exists(log_file_path):
        i = 1
        while True:
            log_file_name = f"generation_log_{project_name}_{i}.txt"
            log_file_path = os.path.join(project_dir, log_file_name)
            if not os.path.exists(log_file_path):
                break
            i += 1
    
    # Thread-safe file handler with UTF-8 encoding for emojis
    file_handler = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
    
    # Thread-safe console handler with safe formatter
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return log_file_path

# Thread-safe logging wrapper
def thread_safe_log(level, message):
    """Thread-safe logging function."""
    if logger is None:
        # Fallback to print if logger not initialized yet
        try:
            print(f"[{level.upper()}] {message}")
        except UnicodeEncodeError:
            # Remove emojis if console can't handle them
            clean_message = message.encode('cp1252', errors='ignore').decode('cp1252')
            print(f"[{level.upper()}] {clean_message}")
        return
        
    with log_lock:
        if level == 'info':
            logger.info(message)
        elif level == 'warning':
            logger.warning(message)
        elif level == 'error':
            logger.error(message)
        elif level == 'debug':
            logger.debug(message)
        elif level == 'critical':
            logger.critical(message)

# --- Provided Boilerplate & Command Functions ---

def run_command(command, cwd):
    """Runs a command in a specified directory."""
    thread_safe_log('info', f"Running command: '{' '.join(command)}' in '{cwd}'")
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True, shell=True)
        thread_safe_log('info', result.stdout)
        if result.stderr:
            thread_safe_log('warning', result.stderr)
        return result
    except subprocess.CalledProcessError as e:
        thread_safe_log('error', f"Error running command: {' '.join(command)}")
        thread_safe_log('error', e.stdout)
        thread_safe_log('error', e.stderr)
        raise RuntimeError(f"Command failed: {' '.join(command)}") from e

def setup_vite_tailwind_project(project_dir):
    """Sets up a new Vite project with Tailwind CSS by cloning from a boilerplate repository."""
    print(f"Step 0: Setting up Vite + Tailwind CSS project in '{project_dir}'...")
    repo_url = "https://github.com/kombee-technologies/figma2html-tailwind-boilerplate.git"
    
    if os.path.exists(project_dir):
        print(f"   -> Removing existing directory '{project_dir}'...")
        shutil.rmtree(project_dir)
        
    print(f"   -> Cloning repository from '{repo_url}'...")
    run_command(['git', 'clone', repo_url, project_dir], cwd=os.getcwd())
    
    public_img_dir = os.path.join(project_dir, "src", "public", "img")
    os.makedirs(public_img_dir, exist_ok=True)
    print(f"   -> Ensured directory '{public_img_dir}' exists.")
    
    print(f"   -> Project structure cloned successfully to '{project_dir}'")
    
    print("   -> Installing dependencies with pnpm...")
    run_command(['pnpm', 'install'], cwd=project_dir)
    print("   -> Dependencies installed.")

# --- Figma API Interaction ---

def get_figma_file_key_from_url(url):
    """Extracts the file key from a Figma URL."""
    thread_safe_log('debug', f"Attempting to extract file key from URL: {url}")
    match = re.search(r'(?:file|design)/([a-zA-Z0-9]+)', url)
    if not match:
        raise ValueError("Invalid Figma file URL. Could not extract file key.")
    return match.group(1)

def figma_api_get(endpoint):
    """Makes a thread-safe GET request to the Figma API with rate limiting."""
    figma_rate_limiter.wait_if_needed()
    
    headers = {"X-Figma-Token": FIGMA_ACCESS_TOKEN}
    url = f"https://api.figma.com/v1/{endpoint}"
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            thread_safe_log('warning', f"Figma API request failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                raise

def find_nodes_by_name(node, names_to_find):
    """Recursively searches the Figma node tree for nodes with specific names."""
    found_nodes = {}
    if 'name' in node and node['name'] in names_to_find:
        found_nodes[node['name']] = node
        
    if 'children' in node:
        for child in node['children']:
            found_nodes.update(find_nodes_by_name(child, names_to_find))
            
    return found_nodes

def find_asset_nodes(node, image_nodes, icon_nodes):
    """
    Recursively traverses the Figma node tree to find images and icons.
    This uses heuristics to differentiate between images and icons.
    """
    node_name_lower = node.get('name', '').lower()
    node_id = node.get('id')

    is_logo = 'logo' in node_name_lower
    if is_logo:
        if node_id and node_id not in image_nodes:
            box = node.get('absoluteBoundingBox', {})
            width = box.get('width', 0)
            height = box.get('height', 0)
            clean_name = sanitize_filename(node.get('name', ''))
            image_nodes[node_id] = {
                'name': clean_name or 'logo',
                'width': round(width),
                'height': round(height)
            }
        # Do not return; a logo component might have other assets inside.

    # Heuristic to identify images: a node that has an image fill.
    has_image_fill = False
    if 'fills' in node and isinstance(node['fills'], list):
        for fill in node['fills']:
            if isinstance(fill, dict) and fill.get('type') == 'IMAGE':
                has_image_fill = True
                break

    # If it has an image fill, treat it as a distinct image asset.
    if has_image_fill:
        node_name = node.get('name', '')
        clean_name = sanitize_filename(node_name)
        if not clean_name:
            clean_name = f"image_{node.get('id', 'unknown').replace(':', '-')}"
        
        box = node.get('absoluteBoundingBox', {})
        width = box.get('width', 0)
        height = box.get('height', 0)

        if node_id and node_id not in image_nodes:
            image_nodes[node_id] = {
                'name': clean_name,
                'width': round(width),
                'height': round(height)
            }

    # Heuristic to identify icons: node is a vector, or its name suggests it's an icon.
    node_name = node.get('name', '').lower()
    is_vector = node.get('type') == 'VECTOR'
    is_boolean_op = node.get('type') == 'BOOLEAN_OPERATION'
    is_icon_by_name = 'icon' in node_name or 'ic_' in node_name or 'icn' in node_name

    if is_vector or is_boolean_op or is_icon_by_name:
        icon_name = node.get('name', '').strip()
        if icon_name:
            icon_nodes.add(icon_name)  # Use a set to avoid duplicates
        return

    # Recurse through children if they exist.
    if 'children' in node and isinstance(node['children'], list):
        for child in node['children']:
            if isinstance(child, dict):
                find_asset_nodes(child, image_nodes, icon_nodes)

def download_node_image(file_key, node_id, output_path):
    """Requests an image export from Figma and downloads it with thread-safe rate limiting."""
    thread_safe_log('info', f"   -> Requesting image export for node '{node_id}'...")
    
    # Apply rate limiting for Figma API calls
    figma_rate_limiter.wait_if_needed()
    
    img_endpoint = f"images/{file_key}?ids={node_id}&format=png&scale=2"
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            img_data = figma_api_get(img_endpoint)
            
            if 'err' in img_data and img_data['err']:
                thread_safe_log('error', f"Figma API error when requesting image: {img_data['err']}")
                return None, None

            img_url = img_data['images'][node_id]
            
            if not img_url:
                thread_safe_log('warning', f"No image URL returned for node '{node_id}'")
                return None, None
            
            thread_safe_log('info', f"   -> Downloading image from URL...")
            response = requests.get(img_url, stream=True, timeout=30)
            response.raise_for_status()
            
            # Thread-safe directory creation
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            image = Image.open(BytesIO(response.content))
            image.save(output_path, "PNG")
            
            width, height = image.size
            thread_safe_log('info', f"   -> Saved image to '{output_path}' (Size: {width}x{height}px)")
            
            relative_path = os.path.join('img', os.path.basename(output_path))
            return f"/{relative_path.replace(os.sep, '/')}", {"width": width, "height": height}
            
        except Exception as e:
            thread_safe_log('warning', f"Download attempt {attempt + 1}/{max_retries} failed for node '{node_id}': {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                thread_safe_log('error', f"Failed to download image for node '{node_id}' after {max_retries} attempts")
                return None, None


def download_single_image(node_id, image_url, node_details, assets_dir):
    """Downloads a single image in a thread-safe manner."""
    if not image_url or not node_details:
        return None
    
    node_name = node_details.get('name', f"image_{node_id.replace(':', '-')}")
    filename = f"{node_name}_{node_id.replace(':', '-')}.png"
    filepath = os.path.join(assets_dir, filename)

    try:
        thread_safe_log('info', f"   -> Downloading {filename}...")
        image_response = requests.get(image_url, timeout=30)
        image_response.raise_for_status()

        # Thread-safe file writing
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(image_response.content)

        relative_path = os.path.join("img", filename).replace("\\", "/")
        return {
            'path': relative_path,
            'details': {
                'width': node_details['width'],
                'height': node_details['height']
            }
        }
    except requests.exceptions.RequestException as e:
        thread_safe_log('warning', f"   -> WARNING: Could not download image for node {node_id}. Reason: {e}")
        return None

def download_figma_images(file_key, image_nodes, token, project_dir):
    """Fetches multiple images from Figma using multithreading and saves them to the 'src/public/img' folder."""
    if not image_nodes:
        thread_safe_log('info', "Step 2b: No image assets to download.")
        return {}
        
    thread_safe_log('info', f"Step 2b: Fetching {len(image_nodes)} image assets from Figma...")
    assets_dir = os.path.join(project_dir, "src", "public", "img")
    os.makedirs(assets_dir, exist_ok=True)

    # Get image URLs from Figma API with rate limiting
    figma_rate_limiter.wait_if_needed()
    headers = {"X-Figma-Token": token}
    ids_param = ",".join(image_nodes.keys())
    url = f"https://api.figma.com/v1/images/{file_key}?ids={ids_param}&format=png&scale=1"

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        thread_safe_log('error', f"Failed to get image URLs from Figma: {e}")
        return {}

    image_data = response.json()
    if 'images' not in image_data or not image_data['images']:
        thread_safe_log('warning', "Could not retrieve image URLs from Figma for assets.")
        return {}

    image_urls = image_data.get('images', {})
    downloaded_files = {}

    # Download images concurrently
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_node = {
            executor.submit(download_single_image, node_id, image_url, image_nodes.get(node_id), assets_dir): node_id
            for node_id, image_url in image_urls.items() if image_url
        }
        
        for future in as_completed(future_to_node):
            result = future.result()
            if result:
                downloaded_files[result['path']] = result['details']

    thread_safe_log('info', f"   -> {len(downloaded_files)} images saved to '{assets_dir}'")
    return downloaded_files

# --- AI Prompt Generation ---

def extract_design_properties(node, properties):
    """Recursively traverses the node tree to extract design properties like colors, fonts, and button styles."""
    # Extract colors from fills and strokes
    for prop in ['fills', 'strokes']:
        if prop in node and isinstance(node[prop], list):
            for paint in node[prop]:
                if paint.get('type') == 'SOLID' and paint.get('visible', True):
                    color_data = paint.get('color')
                    if color_data:
                        # Convert RGBA (0-1 floats) to a CSS hex string
                        r = int(color_data['r'] * 255)
                        g = int(color_data['g'] * 255)
                        b = int(color_data['b'] * 255)
                        properties['colors'].add(f"#{r:02x}{g:02x}{b:02x}")

                elif paint.get('type') == 'GRADIENT_LINEAR' and paint.get('visible', True):
                    gradient_stops = paint.get('gradientStops')
                    if gradient_stops:
                        gradient_info = {
                            'type': 'linear-gradient',
                            'stops': []
                        }
                        for stop in gradient_stops:
                            color_data = stop.get('color')
                            position = stop.get('position')
                            if color_data and position is not None:
                                r = int(color_data['r'] * 255)
                                g = int(color_data['g'] * 255)
                                b = int(color_data['b'] * 255)
                                a = round(color_data.get('a', 1) * 100) # Alpha as percentage
                                # Format as rgba(r, g, b, a%) or rgb(r, g, b) if alpha is 100%
                                color_str = f"rgba({r}, {g}, {b}, {a}%)" if a < 100 else f"rgb({r}, {g}, {b})"
                                gradient_info['stops'].append({
                                    'color': color_str,
                                    'position': round(position * 100) # Position as percentage
                                })
                        if gradient_info['stops']:
                            # Convert to a hashable tuple of tuples for the set
                            properties['gradients'].add(tuple(sorted(tuple(s.items()) for s in gradient_info['stops'])))

    # Extract font styles from text nodes
    if node.get('type') == 'TEXT':
        style = node.get('style', {})
        font_info = {
            'Family': style.get('fontFamily'),
            'Size': f"{style.get('fontSize')}px",
            'Weight': style.get('fontWeight'),
            'Line-Height': f"{style.get('lineHeightPx')}px"
        }
        # Get text color from its fills
        if 'fills' in node and isinstance(node['fills'], list):
            for paint in node.get('fills', []):
                if paint.get('type') == 'SOLID':
                    color_data = paint.get('color')
                    if color_data:
                        r, g, b = int(color_data['r'] * 255), int(color_data['g'] * 255), int(color_data['b'] * 255)
                        font_info['Color'] = f"#{r:02x}{g:02x}{b:02x}"
                        break
        # Use a tuple of items to make it hashable for the set
        properties['fonts'].add(tuple(sorted(font_info.items())))

    # Heuristic for buttons: A frame with a corner radius, a background color, and a text child.
    is_frame_or_component = node.get('type') in ['FRAME', 'COMPONENT', 'COMPONENT_SET']
    has_children = 'children' in node and isinstance(node['children'], list)

    if is_frame_or_component and has_children:
        has_text_child = any(child.get('type') == 'TEXT' for child in node['children'])
        has_bg_color = any(
            p.get('type') == 'SOLID' for p in node.get('fills', []) if isinstance(p, dict)
        )

        if has_text_child and has_bg_color and node.get('name', '').lower() not in ['icon', 'logo']:
            button_style = {}
            # Background color
            for fill in node.get('fills', []):
                if fill.get('type') == 'SOLID':
                    color_data = fill.get('color')
                    r, g, b = int(color_data['r'] * 255), int(color_data['g'] * 255), int(color_data['b'] * 255)
                    button_style['background-color'] = f"#{r:02x}{g:02x}{b:02x}"
                    break
            # Corner radius
            if 'cornerRadius' in node and node['cornerRadius'] > 0:
                button_style['border-radius'] = f"{node['cornerRadius']}px"
            
            # Padding (approximated from child positions)
            if len(node['children']) > 0:
                child_box = node['children'][0].get('absoluteBoundingBox', {})
                node_box = node.get('absoluteBoundingBox', {})
                if child_box and node_box:
                    padding_y = (node_box['height'] - child_box['height']) / 2
                    if padding_y > 2:
                         button_style['padding-vertical'] = f"{round(padding_y)}px"

            # Use tuple of items to make it hashable for the set
            properties['buttons'].add(tuple(sorted(button_style.items())))

    # Recurse through children
    if has_children:
        for child in node['children']:
            if isinstance(child, dict):
                extract_design_properties(child, properties)

def create_component_prompt(component_name, image_path, image_details, design_summary=None):
    """Creates a focused prompt for generating a single component (e.g., Header, Footer)."""
    image_list = f"- `{image_path}` (Size: {image_details['width']}x{image_details['height']}px)"

    design_summary_section = ""
    if design_summary:
        colors_str = ", ".join(sorted(design_summary.get('colors', [])))
        
        fonts_list = [dict(font_tuple) for font_tuple in design_summary.get('fonts', [])]
        fonts_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(f.items())])}" for f in sorted(fonts_list, key=lambda x: x.get('Size', '0'))]
        )
        
        buttons_list = [dict(button_tuple) for button_tuple in design_summary.get('buttons', [])]
        buttons_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(b.items())])}" for b in sorted(buttons_list, key=lambda x: x.get('background-color', ''))]
        )

        gradients_list = [dict(gradient_tuple) for gradient_tuple in design_summary.get('gradients', [])]
        gradients_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(g.items())])}" for g in sorted(gradients_list, key=lambda x: x.get('type', ''))]
        )

        if colors_str or fonts_str or buttons_str or gradients_str:
            design_summary_section = f"""
**Figma Design System Summary:**
To help you build an accurate layout, I have analyzed the Figma file and extracted the following design properties.
You MUST use these values when deciding on your Tailwind CSS classes.

*   **Color Palette:**
    {colors_str or "No solid colors found."}

*   **Typography (Font Styles Used):**
{fonts_str or "No text styles found."}

*   **Button Styles (Detected):**
{buttons_str or "No common button styles detected."}

*   **Gradient Styles (Detected):**
{gradients_str or "No linear gradients found."}
            """

    prompt = f"""
**Role:** You are an expert front-end developer specializing in creating clean, responsive, and production-ready HTML components using Tailwind CSS.

**Task:** Your goal is to convert the provided Figma design of a single component into a self-contained HTML file.

**Component Name:** {component_name}

{design_summary_section}

**Instructions & Requirements:**
1.  **Analyze the Image:** Use the provided image of the component for layout, spacing, colors, and typography.
2.  **HTML Structure:**
    *   **CRITICAL: Figma Design Fidelity (All Sections & Parts):** You MUST ensure that *every single section and part* of the generated HTML, CSS, and JavaScript is created *exactly as shown in the Figma design image*. This includes, but is not limited to, overall section layout, component arrangement, spacing (margins and padding), font sizes, colors, and interactive elements. All section alignments and visual properties MUST match the Figma design perfectly.
    *   **CRITICAL: Proper HTML Structure & Semantic Nesting:** Follow proper HTML structure with clear parent-child relationships:
        - Every section must have a clear parent container that wraps all child elements
        - Child elements must be properly nested within their parent containers
        - Use separate `<div>` elements for different visual layers (background, overlay, content)
        - When using absolute positioning, ensure the parent has `relative` positioning
        - Group related content within wrapper divs for better structure
        - Avoid flat HTML structure; create proper hierarchical nesting
    *   **CRITICAL: Sticky Header (for Header Component):** If this is the Header component, the outer `<nav>` element within `src/components/header.html` should *not* directly apply `sticky`, `top-0`, `z-50`, or `shadow-md` classes. These classes will be applied to the `<header id="header-placeholder">` in the main `src/index.html` file, which wraps this component. Ensure the `<nav>` is designed to fit within a sticky container.
    *   **CRITICAL: Modern Hamburger Menu:** If a navigation menu is present, implement a modern, fully functional and working, and visually appealing mobile "hamburger menu" with smooth open and close animations. This MUST include a clear close button (e.g., an 'X' icon) within the menu and a semi-transparent overlay that closes the menu when clicked, ensuring responsiveness and a unique UI/UX experience. The menu should open from the right side of the screen.
    *   **CRITICAL: Interactive Elements Implementation:** If the Figma design includes interactive elements such as pagination, accordion sections, toggle buttons, dropdowns, modals, tabs, carousels, or any other user-interactive components, you MUST implement them with fully functional JavaScript code. These interactive elements must work properly and smoothly without interfering with the hamburger menu functionality. Ensure all event listeners are properly scoped and do not conflict with the mobile menu's event handling. The hamburger menu must continue to work flawlessly alongside all other interactive elements.
    *   **CRITICAL: Responsive Table Implementation:** If the Figma design includes table sections, you MUST implement them as fully responsive tables that work perfectly on any screen resolution. Use Tailwind CSS responsive utilities (`overflow-x-auto`, `min-w-full`, `table-auto`, `block md:table`, `hidden md:table-cell`, etc.) to ensure tables are scrollable horizontally on mobile devices and display properly on all screen sizes. Implement proper table structure with semantic HTML (`<table>`, `<thead>`, `<tbody>`, `<tr>`, `<th>`, `<td>`) and ensure all table content remains readable and accessible across all devices.
    *   **CRITICAL: Header and Footer Navigation Structure:** For header and footer components, you MUST use the following specific structure:
        - In Header Use `<nav class="navbar navbar-expand-lg">` for the navigation container
        - Use `<div class="container">` for proper content wrapping
        - Navigation menu MUST use `<ul>` with proper classes like `navbar-nav mr-auto` and `<li>` with classes like `nav-item` for each menu item
        - Each menu item MUST use `<a>` tags with proper classes like `nav-link`
        - Include proper Tailwind CSS classes for responsive behavior (`flex`, `items-center`, `justify-between`, `gap-4`)
        - Footer components should follow similar structure with proper `<footer>`, `<ul>`, and `<li>` tags for navigation menus
    *   Generate ONLY the HTML code for the `{component_name}` component.
    *   Do NOT include `<!DOCTYPE>`, `<html>`, `<head>`, or `<body>` tags. The output should be a snippet ready for injection.
    *   Use semantic HTML5 tags (`<header>`, `<footer>`, `<nav>`, `<ul>`, `<li>`, `<a>`, `<button>`).
    *   **Icons:** Use Font Awesome for all icons. The main page will include the necessary CDN link. Use the correct syntax, for example: `<i class="fa-solid fa-star"></i>` or `<i class="fa-brands fa-twitter"></i>`.
    *   **Font Awesome:** You MUST use Font Awesome for all icons. The main page will include the following CDN link:
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css" integrity="sha512-DTOQO9RWCH3ppGqcWaEA1BIZOC6xxalwEsw9c2QQeAIftl+Vegovlnee1c9QX4TctnWMn13TZye+giMm8e2LwA==" crossorigin="anonymous" referrerpolicy="no-referrer" />
    *   Use the correct syntax, for example: `<i class="fa-solid fa-star"></i>` or `<i class="fab fa-twitter"></i>`. 
    *   Link to the stylesheet with `<link href="/style.css" rel="stylesheet">`. 
    *   Link to the JavaScript file with `<script type="module" src="/main.js" defer></script>` at the end of `<body>`. 
    *   Use descriptive `alt` tags for all images.
3.  **Styling:**
    *   You MUST use Tailwind CSS utility classes for all styling.
    *   Do NOT generate any `<style>` blocks or separate CSS files.
    *   Ensure the component is responsive using Tailwind's breakpoints (e.g., `md:`, `lg:`).
4.  `src/main.js`:
    *   Implement functional mobile "hamburger menu" if one is visible in the design.
5.  **Image Asset:**
    *   The image for this component has been provided. Use the following path:
    {image_list}
    *   Note: All images, including this one, will be available in the `src/public/img/` directory of the generated project.
6.  **Output Format (Strictly Enforced):**
    *   Provide the entire code output in a single response block.
    *   The file path MUST be `src/components/{component_name.lower()}.html`.

**Example Output Structure:**

FILEPATH: src/components/{component_name.lower()}.html
```html
<header class="bg-white shadow-md sticky top-0 z-50">
  <!-- Component HTML code here -->
</header>

---
Now, based on the provided image, generate the HTML for the `{component_name}` component.
"""
    thread_safe_log('info', f"Generated prompt for component: {component_name}\nFull Prompt:\n{prompt}")
    return prompt

def create_master_prompt(project_name, page_name, image_path, image_details, generated_components, downloaded_assets=None, is_header_footer_common=False, is_first_page=True, design_summary=None, page_sections=None):
    """Creates the master prompt for a full page, aware of existing components and assets."""
    image_list = f"- `{image_path}` (Size: {image_details['width']}x{image_details['height']}px)"

    # Initialize dynamic instruction sections
    component_injection_instructions = ""
    image_prompt_section = ""
    design_summary_section = ""
    section_marker_instructions = ""

    # --- Component Injection Instructions ---
    if is_header_footer_common and is_first_page:
        component_injection_instructions = """
**AI Task Refinement (CRITICAL for Common Components - First Page):**
*   For this first page, you MUST analyze the *entire* provided Figma page image.
*   You are responsible for intelligently identifying the distinct Header, Footer, and Main Content sections of the page.
*   You MUST then generate code for **FIVE** files in a single response. Each file block MUST be preceded by the exact literal string `FILEPATH: ` followed by the file path, and the code enclosed in triple backticks (e.g., ```html`).
    *   **STRICTLY ENFORCED:** Do NOT include any conversational text, explanations, or remarks outside of these code blocks. ONLY the `FILEPATH:` and code blocks are allowed.
    *   **Example Output Block (CRITICAL for format):**
    ```
    FILEPATH: src/components/header.html
    ```html
    <header>...</header>
    ```
    FILEPATH: src/components/footer.html
    ```html
    <footer>...</footer>
    ```

    ```
*   Here are the five files you MUST generate:
    1.  `src/components/header.html`:
        *   Contains ONLY the HTML for the identified Header component. Do NOT include `<!DOCTYPE>`, `<html>`, `<head>`, or `<body>` tags. The output must be a self-contained HTML snippet.
        *   Use semantic HTML5 tags and **exclusively Tailwind CSS utility classes** for all styling. Use Font Awesome for icons.
    2.  `src/components/footer.html`:
        *   Contains ONLY the HTML for the identified Footer component. Do NOT include `<!DOCTYPE>`, `<html>`, `<head>`, or `<body>` tags. The output must be a self-contained HTML snippet.
        *   Use semantic HTML5 tags and **exclusively Tailwind CSS utility classes** for all styling. Use Font Awesome for icons.
    3.  `src/{page_name}.html`:
        *   Contains the **full HTML structure** for the main page (including `<!DOCTYPE>`, `<html>`, `<head>`, `<body>`).
        *   MUST include Google Fonts links and Font Awesome CDN in the `<head>`.
        *   MUST link to stylesheet: `<link href="/style.css" rel="stylesheet">`.
        *   MUST link to JavaScript: `<script type="module" src="/main.js" defer></script>` at the end of `<body>`.
        *   MUST include empty placeholder elements where Header and Footer should be loaded:
            *   For the Header: `<header id="header-placeholder"></header>`
            *   For the Footer: `<footer id="footer-placeholder"></footer>`
    4.  `src/style.css`:
        *   Contains ONLY the line: `@import 'tailwindcss';` (no other CSS).
    5.  `src/main.js`:
        *   Contains minimal JavaScript, primarily for loading Header/Footer components into their placeholders. Do NOT include other unnecessary JS.

"""
    elif is_header_footer_common and not is_first_page:
        component_injection_instructions = """
**AI Task Refinement (CRITICAL for Common Components - Subsequent Pages):**
*   This website uses shared components for the Header and Footer, which have already been generated in `src/components/header.html` and `src/components/footer.html`.
*   You MUST NOT generate HTML for the Header or Footer directly in this file. Instead, their content will be loaded dynamically.
*   You MUST NOT generate `src/style.css` or `src/main.js`. These files are common and already exist. **STRICTLY ENFORCED:** Only generate the HTML for the main page.
*   Analyze the *entire* provided Figma page image to understand the full page layout.
*   Generate ONLY the HTML for the main page content (`src/{page_name}.html`).
    *   **STRICTLY ENFORCED:** Do NOT include any conversational text, explanations, or remarks outside of the code block. ONLY the `FILEPATH:` and code block are allowed.
*   The output should be a single HTML file with the full structure (including `<!DOCTYPE>`, `<html>`, `<head>`, `<body>`).
*   MUST include Google Fonts links and Font Awesome CDN in the `<head>`.
*   MUST link to stylesheet: `<link href="/style.css" rel="stylesheet">`.
*   MUST link to JavaScript: `<script type="module" src="/main.js" defer></script>` at the end of `<body>`.
*   MUST include empty placeholder elements where these common components should appear:
    *   For the Header: `<header id="header-placeholder"></header>`
    *   For the Footer: `<footer id="footer-placeholder"></footer>`
"""
    else:
        # Original logic if not using common header/footer
        if generated_components:
            placeholders = []
            for component, path in generated_components.items():
                placeholder_id = f"{component.lower()}-placeholder"
                tag = 'header' if 'header' in component.lower() else ('footer' if 'footer' in component.lower() else 'div')
                placeholders.append(f"<{tag} id=\"{placeholder_id}\"></{tag}>")
            
            if placeholders:
                component_injection_instructions = f"""
**Shared Components Integration (CRITICAL):**
*   This website uses shared components for the Header and Footer.
*   You MUST NOT generate the HTML for the Header or Footer directly in this file. Instead, their content will be loaded dynamically.
*   Instead, you MUST add empty placeholder elements where these components should appear.
*   Use the following placeholders:
    *   For the Header: `{placeholders[0]}`
    *   For the Footer: `{placeholders[1]}`
*   I have a JavaScript function that will automatically fetch the content from `src/components/header.html` and `src/components/footer.html` and inject it into these placeholders.
    *   **STRICTLY ENFORCED:** Do NOT include any conversational text, explanations, or remarks outside of the code block. ONLY the `FILEPATH:` and code block are allowed.
"""

    # --- Image Assets Section ---
    if downloaded_assets:
        asset_list = "\n".join(
            [f"- `{path}` (Size: {details['width']}x{details['height']}px)"
             for path, details in downloaded_assets.items()]
        )
        image_prompt_section = f"""
**Image Assets (Pre-downloaded):**
*   I have already downloaded the necessary image assets from the design.
*   They are located in the `src/public/img/` directory.
*   You MUST use these exact relative paths (prefixed with a forward slash) in your HTML `<img>` tags.
*   Here is the list of available images:
{asset_list}
"""

    # --- Design System Summary Section ---
    if design_summary:
        colors_str = ", ".join(sorted(design_summary.get('colors', [])))
        
        fonts_list = [dict(font_tuple) for font_tuple in design_summary.get('fonts', [])]
        fonts_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(f.items())])}" for f in sorted(fonts_list, key=lambda x: x.get('Size', '0'))]
        )
        
        buttons_list = [dict(button_tuple) for button_tuple in design_summary.get('buttons', [])]
        buttons_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(b.items())])}" for b in sorted(buttons_list, key=lambda x: x.get('background-color', ''))]
        )

        gradients_list = [dict(gradient_tuple) for gradient_tuple in design_summary.get('gradients', [])]
        gradients_str = "\n".join(
            [f"- {', '.join([f'{k}: {v}' for k, v in sorted(g.items())])}" for g in sorted(gradients_list, key=lambda x: x.get('type', ''))]
        )

        if colors_str or fonts_str or buttons_str or gradients_str:
            design_summary_section = f"""
**Figma Design System Summary:**
To help you build an accurate layout, I have analyzed the Figma file and extracted the following design properties.
You MUST use these values when deciding on your Tailwind CSS classes.

*   **Color Palette:**
    {colors_str or "No solid colors found."}

*   **Typography (Font Styles Used):**
{fonts_str or "No text styles found."}

*   **Button Styles (Detected):**
{buttons_str or "No common button styles detected."}

*   **Gradient Styles (Detected):**
{gradients_str or "No linear gradients found."}
"""

    # --- Section Markers Instructions ---
    if page_sections:
        section_list_formatted = "\n".join([f"        *   `{section_name}`" for section_name in page_sections])
        section_marker_instructions = f"""
        *   **CRITICAL: Section Markers:** You MUST add HTML comments to delineate each major section. For example, `<!-- START: Section Name -->` before a `<section>` tag and `<!-- END: Section Name -->` after its closing `</section>` tag.
            *   **STRICTLY ENFORCED:** You MUST use the following section names for your markers, in the exact order provided:
{section_list_formatted}
            *   Do NOT infer section names; use these precisely.
"""

    master_prompt = f"""
**Role:** You are an expert front-end developer specializing in creating clean, responsive, and production-ready code from design mockups using Tailwind CSS.

**Task:** Your goal is to convert the provided Figma design for the '{page_name}' page into a responsive website using Vite, Tailwind CSS, and JavaScript.

**Project Name:** {project_name}

{component_injection_instructions}

{image_prompt_section}

{design_summary_section}

    **Instructions & Requirements:**
    1.  **CRITICAL: Figma Design Fidelity (All Sections & Parts):** You MUST ensure that *every single section and part* of the generated HTML, CSS, and JavaScript is created *exactly as shown in the Figma design image*. This includes, but is not limited to, overall section layout, component arrangement, spacing (margins and padding), font sizes, colors, and interactive elements. All section alignments and visual properties MUST match the Figma design perfectly.
    2.  **CRITICAL: Proper HTML Structure & Semantic Nesting:** You MUST follow proper HTML structure with clear parent-child relationships:
        *   **Parent-Child Hierarchy:** Every section must have a clear parent container `<div>` or semantic tag (`<section>` etc.) that wraps all child elements.
        *   **Logical Nesting:** Child elements must be properly nested within their parent containers.
        *   **Separation of Concerns:** Use separate `<div>` elements for different visual layers (background, overlay, content) rather than combining everything in one element.
        *   **Proper Positioning Context:** When using absolute positioning, ensure the parent has `relative` positioning to create the proper positioning context.
        *   **Content Grouping:** Group related content (heading + paragraph + button) within a wrapper div for better structure and maintainability.
        *   **Avoid Flat Structure:** Do NOT create flat HTML where all elements are siblings. Instead, create proper hierarchical structure that reflects the visual design layers.
    3.  **Analyze the Image:** Use the main design image for visual layout, spacing, colors, and typography. You will have to infer text content and other details directly from the image.
    4.  **Layout and Alignment (CRITICAL):**
        *   Carefully analyze the spacing, alignment, and distribution of elements in the Figma design.
        *   **Always use Tailwind CSS flexbox (`flex`, `items-*`, `justify-*`, `gap-*`) and grid (`grid`, `grid-cols-*`, `gap-*`) utilities for structuring sections and arranging elements.** Do not rely on margin/padding alone for primary layout.
        *   For sections requiring content on one side and related elements on another (like the CTA and Footer main sections), use a flex container with `justify-between` and assign appropriate widths (`md:w-1/2`, `md:w-1/3`, etc.) to child elements.
        *   Apply text alignment classes (`text-left`, `text-center`, `text-right`) contextually based on the visual design, ensuring they are consistent across breakpoints or explicitly overridden when needed (e.g., `md:text-left`). Avoid conflicting alignment classes on the same element.
        *   Pay close attention to vertical and horizontal centering using `items-center` (flex), `justify-center` (flex), or `mx-auto` (block elements).
    5.  **HTML Structure (`src/{page_name}.html`):**
        *   Generate a single `{page_name}.html` file and ensure it is placed in the `src` directory.
        *   It must be well-structured with semantic HTML5 tags (`<main>`, `<section>`, etc.).
        *   **Google Fonts:** You MUST use Google Fonts. Infer the font families from the design (e.g., Urbanist, Poppins, etc.) and include the correct `<link>` tags for them in the `<head>`.
        *   **Font Awesome:** You MUST use Font Awesome for all icons. The main page will include the following CDN link:
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css" integrity="sha512-DTOQO9RWCH3ppGqcWaEA1BIZOC6xxalwEsw9c2QQeAIftl+Vegovlnee1c9QX4TctnWMn13TZye+giMm8e2LwA==" crossorigin="anonymous" referrerpolicy="no-referrer" />
        *   Use the correct syntax, for example: `<i class="fa-solid fa-star"></i>` or `<i class="fab fa-twitter"></i>`. 
        *   Link to the stylesheet with `<link href="/style.css" rel="stylesheet">`. 
        *   Link to the JavaScript file with `<script type="module" src="/main.js" defer></script>` at the end of `<body>`. 
        *   Use descriptive `alt` tags for all images.
{section_marker_instructions}
    6.  **Image Asset:**
        *   Here is the image for the entire page:
{image_list}
    7.  **CSS Styling (`src/style.css`):**
        *   Generate a `src/style.css` file that contains only the following line: `@import 'tailwindcss';`
        *   Implement a responsive design using Tailwind's utility classes (e.g., `md:`, `lg:`).
        *   Use Tailwind's default color palette and spacing system where possible, but use the exact colors from the design summary for text, backgrounds, etc.
        *   **You MUST NOT write any other custom CSS in `style.css`.** All styling MUST be done by applying Tailwind CSS classes directly in the HTML.
    8.  **JavaScript Functionality (`src/main.js`):**
        *   **CRITICAL AND STRICTLY ENFORCED: Single `DOMContentLoaded` Block & No Duplicate Loaders:** The entire `main.js` file MUST be contained within a single `document.addEventListener('DOMContentLoaded', () => { ... });` block. There MUST be ABSOLUTELY NO other `fetch()` calls, `DOMContentLoaded` listeners, or any other executable code outside of this single block (only function definitions that are called from within it are allowed). This rule is paramount. The AI is solely responsible for generating all necessary JavaScript, including component loading and mobile menu initialization, within this single block.
        *   **CRITICAL: Interactive Elements Implementation:** If the Figma design includes interactive elements such as pagination, accordion sections, toggle buttons, dropdowns, modals, tabs, carousels, or any other user-interactive components, you MUST implement them with fully functional JavaScript code within the single `DOMContentLoaded` block. These interactive elements must work properly and smoothly without interfering with the hamburger menu functionality. Ensure all event listeners are properly scoped and do not conflict with the mobile menu's event handling. The hamburger menu must continue to work flawlessly alongside all other interactive elements.
        *   **CRITICAL: Responsive Table Implementation:** If the Figma design includes table sections, you MUST implement them as fully responsive tables that work perfectly on any screen resolution. Use Tailwind CSS responsive utilities (`overflow-x-auto`, `min-w-full`, `table-auto`, `block md:table`, `hidden md:table-cell`, etc.) to ensure tables are scrollable horizontally on mobile devices and display properly on all screen sizes. Implement proper table structure with semantic HTML (`<table>`, `<thead>`, `<tbody>`, `<tr>`, `<th>`, `<td>`) and ensure all table content remains readable and accessible across all devices.
        *   **CRITICAL: Accordion Implementation:** If the design includes FAQ sections with accordion functionality, you MUST implement a complete `initializeAccordions()` function that:
            *   Targets all elements with class `faq-toggle` (the clickable buttons)
            *   Implements proper accordion behavior where only one item can be open at a time
            *   Toggles the `hidden` class on elements with class `faq-content`
            *   **DYNAMIC ICON HANDLING:** Analyze the Figma design to determine the exact icon classes used for expanded/collapsed states. The icon classes MUST match exactly what's shown in the design (e.g., `fa-plus`, `fa-minus`, `fa-chevron-down`, `fa-chevron-up`, `fa-plus-circle`, `fa-minus-circle`, etc.). Do NOT assume specific icon classes - extract them from the actual design.
            *   **ICON STATE MANAGEMENT:** Implement proper icon state toggling based on the detected icon classes from the design. Use `classList.remove()` and `classList.add()` to switch between the collapsed and expanded icon states.
            *   Ensures smooth transitions and proper state management
        *   Inside this single `DOMContentLoaded` listener, define the following functions and call them in the specified sequence:
            *   Define an `async function loadComponent(placeholderId, filePath)` that fetches and injects HTML content.
            *   Define the `initializeMobileMenu()` function, which contains all the logic for the mobile hamburger menu:
                *   Target elements with IDs: `hamburger-button` (to open), `close-mobile-menu` (to close), `mobile-menu` (the menu sidebar), and `mobile-menu-overlay` (the background overlay).
                *   When `hamburger-button` is clicked: remove `translate-x-full` and add `translate-x-0` to `mobile-menu`, remove `hidden` from `mobile-menu-overlay`, and add `overflow-hidden` to the `body`.
                *   When `close-mobile-menu` or `mobile-menu-overlay` are clicked: remove `translate-x-0` and add `translate-x-full` to `mobile-menu`, add `hidden` to `mobile-menu-overlay`, and remove `overflow-hidden` from the `body`.
            *   Define additional functions for any interactive elements found in the design (e.g., `initializeAccordions()`, `initializePagination()`, `initializeToggles()`, etc.).
            *   After defining these functions, execute them in this order:
                *   Call `loadComponent('header-placeholder', '/components/header.html')` and, upon its successful completion, call `initializeMobileMenu()` after a small delay (e.g., 100ms) to ensure all elements are rendered.
                *   Then, call `loadComponent('footer-placeholder', '/components/footer.html')`.
                *   Finally, call all other interactive element initialization functions (e.g., `initializeAccordions()`, `initializePagination()`, etc.).
    9.  **Output Format (Strictly Enforced):**
        *   You MUST provide the entire code output in a single response.
        *   Before each file's code block, you MUST include a header in the format `FILEPATH: path/to/your/file.ext`.
        *   Ensure the file paths are correct for a Vite project (e.g., `src/index.html`, `src/style.css`, `src/main.js`).

    **CRITICAL: Sticky Header Setup:** The `<header id="header-placeholder">` in `src/{page_name}.html` MUST include the classes `sticky top-0 z-50 shadow-sm bg-white w-full` to ensure the header is sticky and styled correctly.

    ---
    Now, based on the provided image, generate the complete HTML, CSS, and JavaScript files for the '{page_name}' page.
"""
    thread_safe_log('info', f"Generated master prompt for page: {page_name}\nFull Prompt:\n{master_prompt}")
    return master_prompt

# --- AI Interaction & File Processing ---

def generate_code_with_gemini(prompt, image_path):
    """Sends the prompt and image to the Gemini AI model with thread-safe rate limiting."""
    gemini_rate_limiter.wait_if_needed()
    
    thread_safe_log('info', "   -> Sending request to Gemini AI...")
    model = genai.GenerativeModel(GEMINI_MODEL)
    
    if not os.path.exists(image_path):
        thread_safe_log('error', f"Image path does not exist: {image_path}")
        raise FileNotFoundError(f"Cannot find image at {image_path}")

    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            # Load image using PIL instead of uploading
            thread_safe_log('info', f"   -> Loading image file: {image_path}")
            image = Image.open(image_path)
            
            # Generate content with the image directly
            thread_safe_log('info', "   -> Generating content with Gemini AI...")
            response = model.generate_content([prompt, image])
            thread_safe_log('info', "   -> Received response from Gemini AI.")
            thread_safe_log('debug', f"GEMINI RAW RESPONSE:\n---\n{response.text}\n---")
            
            # Track token usage
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                completion_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
                total_tokens = getattr(response.usage_metadata, 'total_token_count', 0)
                
                token_tracker.add_usage(prompt_tokens, completion_tokens, total_tokens)
                thread_safe_log('info', f"   -> Token usage - Prompt: {prompt_tokens}, Completion: {completion_tokens}, Total: {total_tokens}")
            
            return response.text
            
        except Exception as e:
            thread_safe_log('warning', f"Gemini API request failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                thread_safe_log('error', f"An error occurred with the Gemini API after {max_retries} attempts: {e}")
                return None

def parse_ai_response(response_text):
    """Parses the multi-file AI response using regex."""
    files = {}
    
    # Split the response by FILEPATH: to get individual file blocks
    # re.split will include the delimiter, so we need to handle that
    # Use a regex that captures the delimiter so it's included in the split list
    file_blocks = re.split(r'(FILEPATH:\s*[^\n]+)', response_text, flags=re.IGNORECASE)

    # The first element will be everything before the first FILEPATH, which we ignore
    # Then it alternates: delimiter, content, delimiter, content...
    current_filepath = None
    for i, block in enumerate(file_blocks):
        if not block.strip():
            continue # Skip empty blocks

        if block.lower().startswith('filepath:'):
            # This is a filepath declaration
            # Extract the actual path
            match = re.search(r'FILEPATH:\s*([^\n]+)', block, re.IGNORECASE)
            if match:
                current_filepath = match.group(1).strip()
            else:
                current_filepath = None
        elif current_filepath:
            # This block is the content for the previously found filepath
            content = block.strip()
            
            content = re.sub(r'^```(?:\w+)?\s*\n?', '', content, flags=re.IGNORECASE)
            content = re.sub(r'\n?\s*```\s*', '', content)

            cleaned_path = current_filepath
            if cleaned_path.lower().endswith('.html.html'):
                cleaned_path = cleaned_path[:-5]
            
            cleaned_path = re.sub(r'[^a-zA-Z0-9_/.:-]', '', cleaned_path)
            
            files[cleaned_path] = content.strip()
            current_filepath = None

    if not files:
        thread_safe_log('warning', "Could not parse AI response. No file blocks found.")
        thread_safe_log('warning', f"Response Text was: {response_text}")
        
    thread_safe_log('info', f"   -> Parsed {len(files)} file(s) from AI response.")
    return files

def save_files_from_response(project_dir, files, figma_frame_name=None):
    """Thread-safe function to save parsed files to the project directory."""
    files_saved = 0
    for file_path, content in files.items():
        if os.path.isabs(file_path):
            thread_safe_log('warning', f"File path '{file_path}' is absolute, skipping.")
            continue
        
        full_path = os.path.join(project_dir, file_path)
        
        with file_write_lock:
            try:
                os.makedirs(os.path.dirname(full_path), exist_ok=True)

                if figma_frame_name and file_path.endswith('.html'):
                    comment = f"<!-- Figma Frame Name: {figma_frame_name} -->\n"
                    content = comment + content
                
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                thread_safe_log('info', f"   -> Wrote file: {full_path}")
                files_saved += 1
            except IOError as e:
                thread_safe_log('error', f"Failed to write file {full_path}: {e}")
    
    # Track generated files
    if files_saved > 0:
        token_tracker.increment_files(files_saved)

def inject_component_loader_js(project_dir, generated_components):
    """Appends JavaScript to main.js to load HTML components."""
    if not generated_components:
        thread_safe_log('info', "No components to inject. Skipping JS injection.")
        return

    main_js_path = os.path.join(project_dir, 'src', 'main.js')

    if not os.path.exists(main_js_path):
        with open(main_js_path, 'w') as f:
            f.write("// Main JavaScript file\n")
        thread_safe_log('info', f"Created missing file: {main_js_path}")

    loader_script = "\n\n// --- Component Loader ---\n"
    loader_script += "document.addEventListener('DOMContentLoaded', () => {\n"
    
    for component_name, component_path in generated_components.items():
        placeholder_id = f"{component_name.lower()}-placeholder"
        relative_path = os.path.join('components', os.path.basename(component_path)).replace(os.sep, '/')
        
        loader_script += f"""
    const {component_name.lower()}El = document.getElementById('{placeholder_id}');
    if ({component_name.lower()}El) {{
        fetch('/{relative_path}')
            .then(response => response.text())
            .then(data => {{
                {component_name.lower()}El.innerHTML = data;
            }})
            .catch(error => console.error('Error loading {component_name}:', error));
    }}
"""
    loader_script += "});\n"

    with open(main_js_path, 'a', encoding='utf-8') as f:
        f.write(loader_script)
    thread_safe_log('info', f"Appended component loader logic to '{main_js_path}'.")

def sanitize_filename(name):
    """Sanitizes a string to be a valid filename."""
    return re.sub(r'[^a-zA-Z0-9_. -]', '', name).replace(' ', '_')

def convert_to_filename(page_name):
    """
    Converts a Figma page name to a clean, URL-friendly filename.
    Examples:
        "Who We Are" -> "who-we-are"
        "About Us!" -> "about-us"
        "Contact  &  Support" -> "contact-support"
        "Home Page 2024" -> "home-page-2024"
    """
    # Convert to lowercase
    filename = page_name.lower()
    
    # Replace spaces and special characters with hyphens
    filename = re.sub(r'[^a-z0-9]+', '-', filename)
    
    # Remove leading/trailing hyphens
    filename = filename.strip('-')
    
    # If empty after cleaning, use a default
    if not filename:
        filename = "page"
    
    return filename

def extract_sections_from_page_node(page_node):
    """Extracts the names of top-level frames within a page node, which are treated as sections."""
    sections = []
    if 'children' in page_node and isinstance(page_node['children'], list):
        for child in page_node['children']:
            if child.get('type') == 'FRAME' or child.get('type') == 'COMPONENT' or child.get('type') == 'COMPONENT_SET':
                section_name = child.get('name', '').strip()
                if section_name:
                    section_name = re.sub(r'^\d+\s*', '', section_name).strip()
                    sections.append(section_name)
    return sections

def process_single_page(page_data):
    """Thread-safe function to process a single page."""
    try:
        page_node, page_index, total_pages, file_key, PROJECT_NAME, generated_components, downloaded_assets, design_summary = page_data
        
        page_name_raw = page_node['name']
        # Use the new convert_to_filename function for clean, URL-friendly names
        page_name = convert_to_filename(page_name_raw)
        output_filename = f"{page_name}.html"
        is_first_page = (page_index == 0)
        
        thread_safe_log('info', f"\n--- Processing Page {page_index+1}/{total_pages}: '{page_name_raw}' -> '{output_filename}' ---")
        
        if page_index > 0:
            thread_safe_log('info', f"   -> Applying {PROCESSING_DELAY}s delay before processing...")
            time.sleep(PROCESSING_DELAY)
        
        img_dir = os.path.join(PROJECT_NAME, "src", "public", "img")
        # Use consistent naming for page images
        img_filename = f"page-{page_name}.png"
        img_output_path = os.path.join(img_dir, img_filename)
        
        max_download_retries = 3
        img_path = None
        img_details = None
        
        for download_attempt in range(max_download_retries):
            try:
                img_path, img_details = download_node_image(file_key, page_node['id'], img_output_path)
                if img_path and img_details:
                    break
                else:
                    thread_safe_log('warning', f"Download attempt {download_attempt + 1} failed for page: {page_name_raw}")
                    if download_attempt < max_download_retries - 1:
                        time.sleep(2 ** download_attempt)       
            except Exception as e:
                thread_safe_log('warning', f"Download attempt {download_attempt + 1} failed for page {page_name_raw}: {e}")
                if download_attempt < max_download_retries - 1:
                    time.sleep(2 ** download_attempt)       
        
        if not img_path or not img_details:
            thread_safe_log('error', f"Failed to download image for page: {page_name_raw} after {max_download_retries} attempts")
            return None

        page_sections = extract_sections_from_page_node(page_node)
        thread_safe_log('info', f"   -> Extracted sections for '{page_name_raw}': {page_sections}")

        prompt = create_master_prompt(PROJECT_NAME, output_filename, img_path, img_details, generated_components, downloaded_assets, IS_COMMAN_HEADER_FOOTER, is_first_page, design_summary, page_sections)
        thread_safe_log('info', f"Generated prompt for page: {page_name}")
        
        full_img_path = os.path.join(PROJECT_NAME, "src", "public", img_path.lstrip('/'))
        ai_response = generate_code_with_gemini(prompt, full_img_path)
        
        if ai_response:
            files = parse_ai_response(ai_response)
            
            if not files:
                thread_safe_log('error', f"No files parsed from AI response for page: {page_name_raw}")
                return None
            
            result = {
                'page_index': page_index,
                'page_name': page_name_raw,
                'output_filename': output_filename,
                'files': files,
                'is_first_page': is_first_page
            }
            
            thread_safe_log('info', f"Successfully processed page: {page_name_raw}")
            return result
        else:
            thread_safe_log('error', f"Failed to get AI response for page: {page_name_raw}")
            return None
            
    except Exception as e:
        thread_safe_log('error', f"Error processing page {page_data[0]['name']}: {e}")
        import traceback
        thread_safe_log('error', f"Traceback: {traceback.format_exc()}")
        return None

# --- Utility Functions ---

def get_next_project_name(base_name):
    """
    Finds the next available project directory name by checking for existing numbered directories.
    E.g., if "my-project", "my-project-1" exist, it returns "my-project-2".
    """
    if not os.path.exists(base_name):
        return base_name

    i = 1
    while True:
        new_name = f"{base_name}-{i}"
        if not os.path.exists(new_name):
            return new_name
        i += 1

# --- Main Execution Logic ---

def main():
    """The main function to run the Figma to Website generation process."""
    start_time = time.time()
    
    print("==================================================")
    print("    FIGMA TO WEBSITE - GENERATION SCRIPT START    ")
    print("==================================================")
    
    try:
        # Determine project name first
        PROJECT_NAME_ENV = os.getenv("PROJECT_NAME")
        BASE_PROJECT_NAME = PROJECT_NAME_ENV if PROJECT_NAME_ENV else "figma-generated-website"
        PROJECT_NAME = get_next_project_name(BASE_PROJECT_NAME)
        
        # Setup project
        setup_vite_tailwind_project(PROJECT_NAME)
        
        # NOW setup logging inside the project directory
        log_file_path = setup_logging(PROJECT_NAME, PROJECT_NAME)
        thread_safe_log('info', "==================================================")
        thread_safe_log('info', "    FIGMA TO WEBSITE - GENERATION SCRIPT START    ")
        thread_safe_log('info', "==================================================")
        thread_safe_log('info', f"Log file created at: {log_file_path}")
        
        # Log configuration
        thread_safe_log('info', "Configuration:")
        thread_safe_log('info', f" - PROJECT_NAME: {PROJECT_NAME}")
        thread_safe_log('info', f" - GEMINI_MODEL: {GEMINI_MODEL}")
        thread_safe_log('info', f" - MULTI_PAGE_MODE: {MULTI_PAGE_MODE}")
        thread_safe_log('info', f" - PAGE_PROCESSING_DELAY: {PAGE_PROCESSING_DELAY}s")
        thread_safe_log('info', f" - PROCESSING_DELAY: {PROCESSING_DELAY}s")
        thread_safe_log('info', f" - MAX_WORKERS: {MAX_WORKERS}")
        thread_safe_log('info', f" - USE_FIGMA_PAGE_NAMES: {USE_FIGMA_PAGE_NAMES}")
        thread_safe_log('info', f" - IS_COMMAN_HEADER_FOOTER: {IS_COMMAN_HEADER_FOOTER}")
        thread_safe_log('info', f" - FIGMA_ACCESS_TOKEN loaded: {bool(FIGMA_ACCESS_TOKEN)}")
        thread_safe_log('info', f" - FIGMA_FILE_URL: {FIGMA_FILE_URL}")
        thread_safe_log('info', f" - GEMINI_API_KEY loaded: {bool(GEMINI_API_KEY)}")
        
        # Step 1: Get Figma file data
        thread_safe_log('info', "\nStep 1: Fetching Figma file data...")
        file_key = get_figma_file_key_from_url(FIGMA_FILE_URL)
        figma_data = figma_api_get(f"files/{file_key}")
        thread_safe_log('info', f"Successfully fetched Figma file: '{figma_data['name']}'")
        
        document = figma_data['document']
        main_canvas = document['children'][0]
        
        generated_components = {}
        downloaded_assets = {}
        found_icon_names = set()

        # Extract design properties
        thread_safe_log('info', "\nStep 2b: Parsing Figma JSON to extract design properties...")
        design_properties = {'colors': set(), 'fonts': set(), 'buttons': set(), 'gradients': set()}
        extract_design_properties(document, design_properties)
        thread_safe_log('info', f"   -> Found {len(design_properties['colors'])} colors, {len(design_properties['fonts'])} font styles, {len(design_properties['buttons'])} button styles, and {len(design_properties['gradients'])} gradients.")
        design_summary = {
            'colors': list(design_properties['colors']),
            'fonts': list(design_properties['fonts']),
            'buttons': list(design_properties['buttons']),
            'gradients': list(design_properties['gradients']),
        }

        # Step 2a: Find and download all image assets
        thread_safe_log('info', "\nStep 2a: Identifying and downloading all image assets...")
        all_image_nodes = {}
        find_asset_nodes(document, all_image_nodes, found_icon_names)
        downloaded_assets = download_figma_images(file_key, all_image_nodes, FIGMA_ACCESS_TOKEN, PROJECT_NAME)
        thread_safe_log('info', f"Found {len(downloaded_assets)} image assets.")

        # Step 3: Process individual pages with multithreading
        thread_safe_log('info', "\nStep 3: Processing individual pages with multithreading...")
        pages_to_process = [child for child in main_canvas['children'] if child['type'] == 'FRAME']
        
        if not pages_to_process:
            thread_safe_log('error', "No top-level frames found on the first canvas to process as pages.")
            return
            
        thread_safe_log('info', f"Found {len(pages_to_process)} pages (frames) to process.")
        thread_safe_log('info', f"Using {MAX_WORKERS} worker threads for processing.")
        
        page_data_list = []
        for i, page_node in enumerate(pages_to_process):
            page_data = (page_node, i, len(pages_to_process), file_key, PROJECT_NAME, generated_components, downloaded_assets, design_summary)
            page_data_list.append(page_data)
        
        if IS_COMMAN_HEADER_FOOTER and len(pages_to_process) > 0:
            thread_safe_log('info', "Processing first page separately to generate common components...")
            first_page_result = process_single_page(page_data_list[0])
            
            if first_page_result:
                files = first_page_result['files']
                page_name_raw = first_page_result['page_name']
                
                with components_lock:
                    for fp, content in files.items():
                        save_files_from_response(PROJECT_NAME, {fp: content}, page_name_raw)
                        if "header.html" in fp.lower():
                            generated_components["Header"] = fp
                        elif "footer.html" in fp.lower():
                            generated_components["Footer"] = fp
                
                thread_safe_log('info', f"First page response handled. Generated components: {generated_components}")
                
                updated_page_data_list = []
                for i in range(1, len(pages_to_process)):
                    page_node = pages_to_process[i]
                    page_data = (page_node, i, len(pages_to_process), file_key, PROJECT_NAME, generated_components.copy(), downloaded_assets, design_summary)
                    updated_page_data_list.append(page_data)
                
                if len(pages_to_process) > 1:
                    thread_safe_log('info', "Processing remaining pages concurrently...")
                    
                    successful_pages = 0
                    failed_pages = []
                    
                    try:
                        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                            future_to_page = {executor.submit(process_single_page, page_data): page_data for page_data in updated_page_data_list}
                            
                            for future in as_completed(future_to_page):
                                try:
                                    result = future.result(timeout=300)
                                    if result:
                                        files = result['files']
                                        page_name_raw = result['page_name']
                                        output_filename = result['output_filename']
                                        
                                        save_files_from_response(PROJECT_NAME, files, page_name_raw)
                                        thread_safe_log('info', f"Subsequent page handled: {output_filename}")
                                        successful_pages += 1
                                    else:
                                        page_data = future_to_page[future]
                                        failed_pages.append(page_data)
                                        thread_safe_log('warning', f"Page processing returned None: {page_data[0]['name']}")
                                except Exception as e:
                                    page_data = future_to_page[future]
                                    failed_pages.append(page_data)
                                    thread_safe_log('error', f"Error in concurrent processing for page {page_data[0]['name']}: {e}")
                    
                    except Exception as e:
                        thread_safe_log('error', f"Critical error in concurrent processing: {e}")
                        failed_pages.extend(updated_page_data_list)
                    
                    if failed_pages:
                        thread_safe_log('info', f"Processing {len(failed_pages)} failed pages sequentially as fallback...")
                        for page_data in failed_pages:
                            try:
                                result = process_single_page(page_data)
                                if result:
                                    files = result['files']
                                    page_name_raw = result['page_name']
                                    output_filename = result['output_filename']
                                    
                                    save_files_from_response(PROJECT_NAME, files, page_name_raw)
                                    thread_safe_log('info', f"Sequential fallback handled: {output_filename}")
                                    successful_pages += 1
                                else:
                                    thread_safe_log('error', f"Sequential fallback also failed for: {page_data[0]['name']}")
                            except Exception as e:
                                thread_safe_log('error', f"Sequential fallback error for {page_data[0]['name']}: {e}")
                    
                    thread_safe_log('info', f"Completed processing {successful_pages} out of {len(updated_page_data_list)} remaining pages")
        else:
            thread_safe_log('info', "Processing all pages concurrently...")
            
            successful_pages = 0
            failed_pages = []
            
            try:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    future_to_page = {executor.submit(process_single_page, page_data): page_data for page_data in page_data_list}
                    
                    for future in as_completed(future_to_page):
                        try:
                            result = future.result(timeout=300)
                            if result:
                                files = result['files']
                                page_name_raw = result['page_name']
                                
                                save_files_from_response(PROJECT_NAME, files, page_name_raw)
                                thread_safe_log('info', f"Page processed: {page_name_raw}")
                                successful_pages += 1
                            else:
                                page_data = future_to_page[future]
                                failed_pages.append(page_data)
                                thread_safe_log('warning', f"Page processing returned None: {page_data[0]['name']}")
                        except Exception as e:
                            page_data = future_to_page[future]
                            failed_pages.append(page_data)
                            thread_safe_log('error', f"Error in concurrent processing for page {page_data[0]['name']}: {e}")
            
            except Exception as e:
                thread_safe_log('error', f"Critical error in concurrent processing: {e}")
                failed_pages.extend(page_data_list)
            
            if failed_pages:
                thread_safe_log('info', f"Processing {len(failed_pages)} failed pages sequentially as fallback...")
                for page_data in failed_pages:
                    try:
                        result = process_single_page(page_data)
                        if result:
                            files = result['files']
                            page_name_raw = result['page_name']
                            
                            save_files_from_response(PROJECT_NAME, files, page_name_raw)
                            thread_safe_log('info', f"Sequential fallback processed: {page_name_raw}")
                            successful_pages += 1
                        else:
                            thread_safe_log('error', f"Sequential fallback also failed for: {page_data[0]['name']}")
                    except Exception as e:
                        thread_safe_log('error', f"Sequential fallback error for {page_data[0]['name']}: {e}")
            
            thread_safe_log('info', f"Completed processing {successful_pages} out of {len(page_data_list)} total pages")

        # Step 4: Post-processing
        thread_safe_log('info', "\nStep 4: Performing post-processing steps...")

    except Exception as e:
        thread_safe_log('critical', f"A critical error occurred: {e}")
        import traceback
        thread_safe_log('critical', traceback.format_exc())
    finally:
        end_time = time.time()
        execution_time = end_time - start_time
        
        # Get token usage summary
        usage_summary = token_tracker.get_summary()
        
        # Print generation summary
        thread_safe_log('info', "\n")
        thread_safe_log('info', "=" * 70)
        thread_safe_log('info', "GENERATION SUMMARY".center(70))
        thread_safe_log('info', "=" * 70)
        thread_safe_log('info', f"✅ Project: {PROJECT_NAME}")
        thread_safe_log('info', f"📄 Total Files Generated: {usage_summary['files_generated']}")
        thread_safe_log('info', f"🤖 Gemini API Calls: {usage_summary['api_calls']}")
        thread_safe_log('info', f"📊 Token Usage:")
        thread_safe_log('info', f"   • Prompt Tokens: {usage_summary['prompt_tokens']:,}")
        thread_safe_log('info', f"   • Completion Tokens: {usage_summary['completion_tokens']:,}")
        thread_safe_log('info', f"   • Total Tokens: {usage_summary['total_tokens']:,}")
        thread_safe_log('info', f"🏁 Total Execution Time: {execution_time:.2f} seconds")
        thread_safe_log('info', "=" * 70)

if __name__ == "__main__":     
    main()