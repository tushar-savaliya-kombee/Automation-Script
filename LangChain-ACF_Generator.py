import os
import time
import logging
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import re
import datetime
import sys

# LangChain imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
from langchain.schema import HumanMessage

# --- Constants ---
# Use these icons for clear logging, as requested.
LOG_ICONS = {
    "INFO": "ℹ️",
    "SUCCESS": "✅",
    "ERROR": "❌",
    "START": "🚀",
    "END": "🏁",
    "AI": "🤖",
    "FILE": "📄",
    "WRITE": "✏️",
    "CONFIG": "⚙️"
}

# The master prompt you crafted. This is the core instruction for the AI.
MASTER_PROMPT_TEMPLATE = PromptTemplate(
    input_variables=["user_request"],
    template="""
You are an expert WordPress developer specializing in the Advanced Custom Fields (ACF) plugin. Your primary mission is to generate a complete, verbose, and syntactically perfect PHP array for an ACF Field Group.

Your knowledge base for this task is the **"ACF Field Type Definition Library"** (provided in the user's initial requirements). This library contains the exact, non-negotiable PHP array structures for every required ACF field type. You must treat this library as the absolute source of truth.

You will be given a user request that outlines the desired ACF Field Group for a specific page. This request will specify:
*   The page name (e.g., "aboutus").
*   A list of fields, organized by tabs, with their desired label and type (e.g., `page_header_description` (Wysiwyg Editor)).

You must follow these critical instructions meticulously to process the user's request and generate the final PHP code.
Your output MUST be *only* the PHP array for the 'fields' key, without any surrounding PHP tags (<?php, ?>), comments, or additional code. DO NOT include the surrounding `array('key' => ..., 'title' => ..., 'fields' => ...)` structure. ONLY output the inner array of fields, starting from `array( array( 'key' => 'field_...', ...), array( 'key' => 'field_...', ... ) )`. Ensure the output is a single, complete PHP array definition.

#### **Core Generation Rules**

1.  **Strictly Adhere to the Definition Library:** For every field specified, find the corresponding field type in the library and use that **exact, complete, and verbose PHP array structure** as a template. Do not simplify or omit any keys.
2.  **Generate Unique Keys:** You MUST generate a new, unique key for **every single field and sub-field** (starting with `field_`). The keys must be 13-character hexadecimal strings.
3.  **Field Naming and Labeling:**
    *   `'label'`: A human-readable version of the field name (e.g., for `about_section_subheading`, the label is "About Section Subheading").
    *   `'name'`: The exact snake_case field name provided by the user. For `tab` fields, the `'name'` key must be an empty string `''`.
4.  **Nested Field Integrity (`parent_repeater`):** For any sub-fields inside a `repeater` field, you MUST include the `'parent_repeater' => 'field_key_of_the_parent_repeater'` key-value pair within each sub-field's array. The value must be the unique `'key'` of the parent repeater field itself.
5.  **Correct `choices` Array Format:** For `select`, `checkbox`, and `radio` fields, the `choices` array must strictly follow the `'value : Label'` format for both the array key and the value (e.g., `'feature_a: Feature A' => 'feature_a: Feature A'`).

Here is the user's request for the fields:
--- START OF USER REQUEST ---
{user_request}
--- END OF USER REQUEST ---
"""
)


def setup_logging(project_name):
    """Sets up a logger to file and console."""
    log_filename = f"log_ACF_Generator_{project_name}.txt"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(log_filename, encoding='utf-8'),
            logging.StreamHandler(sys.stdout) # Explicitly specify sys.stdout for StreamHandler
        ]
    )
    # Clear log file on new run
    with open(log_filename, 'w', encoding='utf-8') as f:
        f.write(f"Log for ACF Generation (LangChain Version) - Project: {project_name} - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*80 + "\n")
    return logging.getLogger()

def load_configuration():
    """Loads and validates configuration from the .env file."""
    load_dotenv()
    config = {
        "api_key": os.getenv("GEMINI_API_KEY"),
        "model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp"),
        "project_path": os.getenv("PROJECT_PATH"),
        "delay": int(os.getenv("PROCESSING_DELAY", 4)),
        "temperature": float(os.getenv("TEMPERATURE", 0.1)),
        "max_tokens": int(os.getenv("MAX_TOKENS", 8192)),
    }
    if not all([config["api_key"], config["model"], config["project_path"]]):
        raise ValueError("One or more required environment variables are missing from the .env file.")
    return config

def setup_langchain_model(config):
    """Initialize LangChain ChatGoogleGenerativeAI model."""
    try:
        model = ChatGoogleGenerativeAI(
            model=config["model"],
            google_api_key=config["api_key"],
            temperature=config["temperature"],
            max_tokens=config["max_tokens"],
            convert_system_message_to_human=True
        )
        return model
    except Exception as e:
        raise Exception(f"Failed to initialize LangChain model: {e}")

def get_page_name_from_filename(filename):
    """Extracts the page name (e.g., 'aboutus') from the filename."""
    base_name = os.path.basename(filename)
    return base_name.replace('-ACF-fields.txt', '')

def format_page_slug(page_name):
    """Formats page name into a slug (e.g., 'about-us')."""
    return page_name.replace('_', '-').lower()

def format_page_title(page_name):
    """Formats page name into a title (e.g., 'About Us')."""
    return page_name.replace('_', ' ').replace('-', ' ').title()

def get_page_creation_code(page_name, page_slug, page_title):
    """Generates the PHP code for creating a page on theme activation."""
    return f"""
/**
 * ===================================================================
 * Create the "{page_title}" Page on Theme Activation
 * ===================================================================
 */
function create_{page_name}_page_on_theme_setup() {{
    if ( ! get_page_by_path('{page_slug}') ) {{
        $page_data = array(
            'post_title'    => '{page_title}',
            'post_name'     => '{page_slug}',
            'post_content'  => '',
            'post_status'   => 'publish',
            'post_author'   => 1,
            'post_type'     => 'page',
        );
        $page_id = wp_insert_post( $page_data );
        if ( $page_id && ! is_wp_error( $page_id ) ) {{
            update_post_meta( $page_id, '_wp_page_template', 'template/{page_slug}.php' );
        }}
    }}
}}
add_action( 'after_setup_theme', 'create_{page_name}_page_on_theme_setup' );"""

def get_options_page_creation_code():
    """Generates the PHP code for creating header and footer options pages."""
    return """
/**
 * ===================================================================
 * Create the "Header and Footer" section on Theme Activation
 * ===================================================================
 */
add_action('admin_init', function () {
    if (!function_exists('acf_get_options_page') || !function_exists('acf_update_ui_options_page')) {
        return;
    }
    
    if(get_option('options-page-header-kombee') !== '1') {
        // The unique menu slug for our options page.
        $menu_slug = 'header-general-settings';
        
        // Check if the options page already exists.
        $page = acf_get_options_page($menu_slug);
        
        // If the page doesn't exist ($page is false), then we create it.
        if (!$page) {
            acf_update_ui_options_page([
                'key'         => 'options_page_header_settings',
                'title'       => 'Header General Settings',
                'page_title'  => 'Header General Settings',
                'menu_slug'   => $menu_slug,
                'menu_title'  => 'Header General Settings',
                'parent_slug' => '',
                'capability'  => 'manage_options',
                'icon_url'    => 'dashicons-admin-settings',
                'post_id'     => 'options',
                'active'      => true,
            ]);
        }
        update_option('options-page-header-kombee', '1');
    }
    
    if(get_option('options-page-footer-kombee') !== '1') {
        // The unique menu slug for our options page.
        $menu_slug = 'footer-general-settings';
        
        // Check if the options page already exists.
        $page = acf_get_options_page($menu_slug);
        
        // If the page doesn't exist ($page is false), then we create it.
        if (!$page) {
            acf_update_ui_options_page([
                'key'         => 'options_page_footer_settings',
                'title'       => 'Footer General Settings',
                'page_title'  => 'Footer General Settings',
                'menu_slug'   => $menu_slug,
                'menu_title'  => 'Footer General Settings',
                'parent_slug' => '',
                'capability'  => 'manage_options',
                'icon_url'    => 'dashicons-admin-settings',
                'post_id'     => 'options',
                'active'      => true,
            ]);
        }
        update_option('options-page-footer-kombee', '1');
    }
}, 11);
"""

def get_acf_registration_code(page_name, page_slug, page_title, acf_php_code, is_options_page=False):
    """Generates the PHP code for registering the ACF field group."""
    # Generate a unique group key
    group_key = f"group_{hex(int(time.time() * 1000))[-10:]}"
    
    # Determine location array based on whether it's an options page
    if is_options_page:
        if page_name == 'header':
            location_array = """array(
                array(
                    array(
                        'param' => 'options_page',
                        'operator' => '==',
                        'value' => 'header-general-settings',
                    ),
                ),
            )"""
        elif page_name == 'footer':
            location_array = """array(
                array(
                    array(
                        'param' => 'options_page',
                        'operator' => '==',
                        'value' => 'footer-general-settings',
                    ),
                ),
            )"""
        else:
            # Fallback for other potential options pages
            location_array = f"""array(
                array(
                    array(
                        'param' => 'options_page',
                        'operator' => '==',
                        'value' => '{page_slug}-general-settings',
                    ),
                ),
            )"""
    else:
        location_array = f"""array(
                array(
                    array(
                        'param' => 'page_template',
                        'operator' => '==',
                        'value' => 'template/{page_slug}.php',
                    ),
                ),
            )"""

    return f"""
/**
 * ===================================================================
 * Register the ACF Field Group for the {page_title} from PHP
 * ===================================================================
 */
function import_{page_name}_acf_fields() {{
    if ( function_exists('acf_import_field_group') && get_option('{page_slug}-kombee') !== '1' ) {{
        $field_group_array = array(
            'key' => '{group_key}',
            'title' => 'Page Content: {page_title}',
            'fields' => {acf_php_code},
            'location' => {location_array},
            'menu_order' => 0,
            'position' => 'normal',
            'style' => 'default',
            'label_placement' => 'top',
            'instruction_placement' => 'label',
            'hide_on_screen' => '',
            'active' => true,
            'description' => '',
            'show_in_rest' => 0,
        );
        acf_import_field_group($field_group_array);
        update_option('{page_slug}-kombee', '1');
    }}
}}
add_action('acf/init', 'import_{page_name}_acf_fields');
"""

def process_acf_file(file_path, config, logger, model, prompt_template):
    """
    Reads a single ACF fields file, calls LangChain AI, and returns the generated code.
    This is the target function for each thread.
    """
    page_name = get_page_name_from_filename(file_path)
    page_slug = format_page_slug(page_name)
    page_title = format_page_title(page_name)

    logger.info(f"{LOG_ICONS['AI']} Starting LangChain AI processing for '{page_title}'...")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            user_request_content = f.read()
    except FileNotFoundError:
        logger.error(f"{LOG_ICONS['ERROR']} File not found: {file_path}")
        return None

    if not user_request_content.strip():
        logger.warning(f"{LOG_ICONS['ERROR']} File is empty, skipping: {file_path}")
        return None

    try:
        # Format the prompt using LangChain template
        formatted_prompt = prompt_template.format(user_request=user_request_content)
        
        # Create message
        message = HumanMessage(content=formatted_prompt)
        
        # Call the LangChain model
        response = model.invoke([message])
        
        # Add delay to respect rate limits
        time.sleep(config["delay"])

        # Extract response text
        response_text = response.content if hasattr(response, 'content') else str(response)

        # Clean the response to ensure it's just the PHP array
        generated_text = response_text.strip()
        # Use a regex to find the array definition, even if preceeded by other PHP code
        match = re.search(r'array\([\s\S]*\);?', generated_text, re.IGNORECASE)
        
        if not match:
            raise ValueError("LangChain AI response did not contain a valid PHP array definition starting with 'array('.")

        generated_text = match.group(0).strip()
        # Remove trailing semicolon if present
        if generated_text.endswith(';'):
            generated_text = generated_text[:-1]

        # Try to get token count (may not be available for all models)
        token_count = 0
        try:
            if hasattr(response, 'response_metadata') and 'usage' in response.response_metadata:
                usage = response.response_metadata['usage']
                token_count = usage.get('total_tokens', 0)
            else:
                # Estimate token count based on text length (rough approximation)
                token_count = len(formatted_prompt.split()) + len(generated_text.split())
        except Exception:
            token_count = len(formatted_prompt.split()) + len(generated_text.split())
        
        return {
            "page_name": page_name.replace('-', '_'), # for function names
            "page_slug": page_slug,
            "page_title": page_title,
            "acf_code": generated_text,
            "tokens": token_count
        }

    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} An error occurred while processing '{page_title}': {e}")
        logger.error(f"Failed LangChain Response Text: {response_text if 'response_text' in locals() else 'No response object'}")
        return None


def main():
    """Main function to orchestrate the entire process."""
    start_time = time.time()
    
    # --- Setup ---
    project_name = os.path.basename(os.getenv("PROJECT_PATH", "default-project"))
    logger = setup_logging(project_name)
    
    logger.info(f"{LOG_ICONS['START']} Starting ACF Field Generation Script (LangChain Version) for project: '{project_name}'")

    try:
        config = load_configuration()
        model = setup_langchain_model(config)
        logger.info(f"{LOG_ICONS['CONFIG']} LangChain configuration loaded successfully.")
        logger.info(f"{LOG_ICONS['AI']} LangChain Model: {config['model']}")
        logger.info(f"🌡️ Temperature: {config['temperature']}")
        logger.info(f"🎯 Max Tokens: {config['max_tokens']}")
    except ValueError as e:
        logger.error(f"{LOG_ICONS['ERROR']} {e}")
        return
    except Exception as e:
        logger.error(f"{LOG_ICONS['ERROR']} Failed to setup LangChain: {e}")
        return

    # --- File Discovery ---
    acf_fields_dir = os.path.join(config["project_path"], "ACF Fields")
    if not os.path.isdir(acf_fields_dir):
        logger.error(f"{LOG_ICONS['ERROR']} 'ACF Fields' directory not found at: {acf_fields_dir}")
        return

    files_to_process = [
        os.path.join(acf_fields_dir, f) for f in os.listdir(acf_fields_dir)
        if f.endswith('-ACF-fields.txt')
    ]

    if not files_to_process:
        logger.warning(f"{LOG_ICONS['INFO']} No '*-ACF-fields.txt' files found to process.")
        return
        
    logger.info(f"{LOG_ICONS['FILE']} Found {len(files_to_process)} ACF definition files to process.")

    # --- Concurrent AI Processing ---
    page_creation_blocks = []
    acf_registration_blocks = []
    total_tokens_used = 0
    has_header_or_footer = False
    
    with ThreadPoolExecutor() as executor:
        future_to_file = {executor.submit(process_acf_file, file, config, logger, model, MASTER_PROMPT_TEMPLATE): file for file in files_to_process}
        
        # Use tqdm for a progress bar
        for future in tqdm(as_completed(future_to_file), total=len(files_to_process), desc="Processing ACF Files with LangChain"):
            result = future.result()
            if result:
                # Check if this is a header or footer file
                is_options_page = result["page_name"] in ['header', 'footer']
                
                if is_options_page:
                    has_header_or_footer = True
                    # For header/footer, we don't create individual pages, just ACF registration
                    acf_code = get_acf_registration_code(result["page_name"], result["page_slug"], result["page_title"], result["acf_code"], is_options_page=True)
                else:
                    # Regular page creation for non-header/footer files
                    page_code = get_page_creation_code(result["page_name"], result["page_slug"], result["page_title"])
                    page_creation_blocks.append(page_code)
                    acf_code = get_acf_registration_code(result["page_name"], result["page_slug"], result["page_title"], result["acf_code"], is_options_page=False)
                
                acf_registration_blocks.append(acf_code)
                total_tokens_used += result["tokens"]
                logger.info(f"{LOG_ICONS['SUCCESS']} Successfully processed and generated code for '{result['page_title']}'.")

    # --- Writing to functions.php ---
    if page_creation_blocks or acf_registration_blocks or has_header_or_footer:
        functions_php_path = os.path.join(config["project_path"], "functions.php")
        logger.info(f"{LOG_ICONS['WRITE']} Appending generated code to {functions_php_path}...")
        
        try:
            with open(functions_php_path, 'a', encoding='utf-8') as f:
                # First, write options page creation if header or footer files were processed
                if has_header_or_footer:
                    f.write("\n\n// --- AUTO-GENERATED OPTIONS PAGE CREATION (LangChain) ---\n")
                    f.write(get_options_page_creation_code())
                
                # Second, write all regular page creation blocks
                if page_creation_blocks:
                    f.write("\n\n// --- AUTO-GENERATED PAGE CREATION BLOCKS (LangChain) ---\n")
                    f.write("\n".join(page_creation_blocks))
                
                # Third, write all ACF registration blocks
                if acf_registration_blocks:
                    f.write("\n\n// --- AUTO-GENERATED ACF REGISTRATION BLOCKS (LangChain) ---\n")
                    f.write("\n".join(acf_registration_blocks))

            logger.info(f"{LOG_ICONS['SUCCESS']} Successfully appended all code blocks to functions.php.")
        except Exception as e:
            logger.error(f"{LOG_ICONS['ERROR']} Could not write to functions.php: {e}")

    # --- Final Summary ---
    end_time = time.time()
    total_time = end_time - start_time
    logger.info(f"{LOG_ICONS['END']} LangChain script finished.")
    
    summary = f"""
==================================================================
                    LANGCHAIN GENERATION SUMMARY
==================================================================
{LOG_ICONS['SUCCESS']} Project: {project_name}
{LOG_ICONS['FILE']} Total Files Processed: {len(files_to_process)}
{LOG_ICONS['AI']} Estimated Tokens Used: {total_tokens_used}
{LOG_ICONS['END']} Total Execution Time: {total_time:.2f} seconds
==================================================================
"""
    logger.info(summary)


if __name__ == "__main__":
    main()