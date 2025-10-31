import os
import re
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from threading import Thread, Lock
from queue import Queue

# Global variables
log_file_path = None
log_lock = Lock()
start_time = None

def log_message(message, icon="ℹ️", level="INFO"):
    """Write formatted log messages with icons and timestamps"""
    with log_lock:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {icon} [{level}] {message}\n"
        
        if log_file_path:
            with open(log_file_path, 'a', encoding='utf-8') as log_file:
                log_file.write(log_entry)
        
        print(log_entry.strip())

def initialize_log_file(project_path):
    """Initialize the log file with header"""
    global log_file_path
    log_file_path = os.path.join(project_path, "Log-for-CPT-Registration.txt")
    
    with open(log_file_path, 'w', encoding='utf-8') as log_file:
        log_file.write("=" * 100 + "\n")
        log_file.write("🎯 WORDPRESS CUSTOM POST TYPE (CPT) REGISTRATION LOG\n")
        log_file.write("=" * 100 + "\n")
        log_file.write(f"📅 Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"📁 Project Path: {project_path}\n")
        log_file.write("=" * 100 + "\n\n")
    
    log_message("Log file initialized successfully", "✅", "SUCCESS")

def load_environment_variables():
    """Load environment variables from .env file"""
    log_message("Loading environment variables from .env file", "🔧", "INFO")
    
    try:
        load_dotenv()
        project_path = os.getenv("PROJECT_PATH_FOR_CPT_GENERATION")
        
        if not project_path:
            log_message("PROJECT_PATH_FOR_CPT_GENERATION not found in .env file", "❌", "ERROR")
            return None
        
        if not os.path.exists(project_path):
            log_message(f"Project path does not exist: {project_path}", "❌", "ERROR")
            return None
        
        log_message(f"Project path loaded: {project_path}", "✅", "SUCCESS")
        return project_path
    
    except Exception as e:
        log_message(f"Error loading environment variables: {str(e)}", "❌", "ERROR")
        return None

def read_cpt_sections_data(project_path):
    """Read and parse CPT sections data from file"""
    log_message("Reading CPT sections data file", "📖", "INFO")
    
    cpt_file_path = os.path.join(project_path, "Figma-analysis-data", "CPT-Sections-Data.txt")
    
    if not os.path.exists(cpt_file_path):
        log_message(f"CPT sections data file not found: {cpt_file_path}", "❌", "ERROR")
        return []
    
    try:
        with open(cpt_file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        
        log_message("CPT sections data file read successfully", "✅", "SUCCESS")
        return content
    
    except Exception as e:
        log_message(f"Error reading CPT sections data: {str(e)}", "❌", "ERROR")
        return ""

def parse_cpt_sections(content):
    """Parse CPT sections from content and extract section names with page names"""
    log_message("Parsing CPT sections from data", "🔍", "INFO")
    
    cpt_sections = []
    
    # Remove icons and clean the content
    lines = content.split('\n')
    current_page = None
    
    for line in lines:
        line = line.strip()
        
        # Skip empty lines and separator lines
        if not line or line == '---':
            continue
        
        # Check if it's a page header (contains 🗂️ and doesn't have ":-")
        if '🗂️' in line and ':-' not in line:
            # Extract page name
            page_match = re.search(r'🗂️\s*(.+?)(?:\s*$)', line)
            if page_match:
                current_page = page_match.group(1).strip()
                log_message(f"Found page: {current_page}", "📄", "INFO")
        
        # Check if it's a CPT section (contains ":- CPT")
        elif 'CPT' in line and ':-' in line:
            # Extract section name
            section_match = re.search(r'[>#]\s*(.+?)\s*:-\s*CPT', line)
            if section_match and current_page:
                section_name = section_match.group(1).strip()
                # Remove "Section" word if present
                section_name = re.sub(r'\s+Section\s*$', '', section_name, flags=re.IGNORECASE)
                # Remove any non-alphanumeric characters, spaces, or hyphens
                section_name = re.sub(r'[^a-zA-Z0-9\s-]', '', section_name).strip()
                
                cpt_sections.append({
                    'page': current_page,
                    'section': section_name
                })
                log_message(f"Found CPT section: '{section_name}' on page '{current_page}'", "🎯", "INFO")
    
    log_message(f"Total CPT sections found: {len(cpt_sections)}", "📊", "INFO")
    return cpt_sections

def generate_cpt_code(section_name, page_name, project_folder_name):
    """Generate CPT registration code for a section"""
    # Convert section name to slug format (lowercase, replace spaces with hyphens)
    section_slug = section_name.lower().replace(' ', '-')
    function_slug = section_name.lower().replace(' ', '_')
    function_name = f"kombee_{function_slug}_cpt_init"
    
    cpt_code = f'''
/*
=================================================================================
Don't Remove this code :- This CPT Post Creation Code for {section_name} with {page_name}
=================================================================================
*/
if(!function_exists('{function_name}')){{
function {function_name}() {{
    $labels = array(
        'name'                  => _x( '{section_name}', 'Post type general name', '{project_folder_name}' ),
        'singular_name'         => _x( '{section_name}', 'Post type singular name', '{project_folder_name}' ),
        'menu_name'             => _x( '{section_name}', 'Admin Menu text', '{project_folder_name}' ),
        'name_admin_bar'        => _x( '{section_name}', 'Add New on Toolbar', '{project_folder_name}' ),
        'add_new'               => __( 'Add New', '{project_folder_name}' ),
        'add_new_item'          => __( 'Add New {section_name}', '{project_folder_name}' ),
        'new_item'              => __( 'New {section_name}', '{project_folder_name}' ),
        'edit_item'             => __( 'Edit {section_name}', '{project_folder_name}' ),
        'view_item'             => __( 'View {section_name}', '{project_folder_name}' ),
        'all_items'             => __( 'All {section_name}', '{project_folder_name}' ),
        'search_items'          => __( 'Search {section_name}', '{project_folder_name}' ),
        'parent_item_colon'     => __( 'Parent {section_name}:', '{project_folder_name}' ),
        'not_found'             => __( 'No {section_name} found.', '{project_folder_name}' ),
        'not_found_in_trash'    => __( 'No {section_name} found in Trash.', '{project_folder_name}' ),
        'featured_image'        => _x( '{section_name} Cover Image', 'Overrides the "Featured Image" phrase for this post type. Added in 4.3', '{project_folder_name}' ),
        'set_featured_image'    => _x( 'Set cover image', 'Overrides the "Set featured image" phrase for this post type. Added in 4.3', '{project_folder_name}' ),
        'remove_featured_image' => _x( 'Remove cover image', 'Overrides the "Remove featured image" phrase for this post type. Added in 4.3', '{project_folder_name}' ),
        'use_featured_image'    => _x( 'Use as cover image', 'Overrides the "Use as featured image" phrase for this post type. Added in 4.3', '{project_folder_name}' ),
        'archives'              => _x( '{section_name} archives', 'The post type archive label used in nav menus. Default "Post Archives". Added in 4.4', '{project_folder_name}' ),
        'insert_into_item'      => _x( 'Insert into {section_name}', 'Overrides the "Insert into post"/"Insert into page" phrase (used when inserting media into a post). Added in 4.4', '{project_folder_name}' ),
        'uploaded_to_this_item' => _x( 'Uploaded to this {section_name}', 'Overrides the "Uploaded to this post"/"Uploaded to this page" phrase (used when viewing media attached to a post). Added in 4.4', '{project_folder_name}' ),
        'filter_items_list'     => _x( 'Filter {section_name} list', 'Screen reader text for the filter links heading on the post type listing screen. Default "Filter posts list"/"Filter pages list". Added in 4.4', '{project_folder_name}' ),
        'items_list_navigation' => _x( '{section_name} list navigation', 'Screen reader text for the pagination heading on the post type listing screen. Default "Posts list navigation"/"Pages list navigation". Added in 4.4', '{project_folder_name}' ),
        'items_list'            => _x( '{section_name} list', 'Screen reader text for the items list heading on the post type listing screen. Default "Posts list"/"Pages list". Added in 4.4', '{project_folder_name}' ),
    );
    $args = array(
        'labels'             => $labels,
        'description'        => '{section_name} custom post type.',
        'public'             => true,
        'publicly_queryable' => true,
        'show_ui'            => true,
        'show_in_menu'       => true,
        'query_var'          => true,
        'rewrite'            => array( 'slug' => '{section_slug}' ),
        'capability_type'    => 'post',
        'has_archive'        => true,
        'hierarchical'       => false,
        'menu_position'      => 20,
        'supports'           => array( 'title', 'editor', 'author', 'thumbnail' ),
        'taxonomies'         => array( 'category', 'post_tag' ),
        'show_in_rest'       => true
    );

    register_post_type( '{section_slug}', $args );
}}
add_action( 'init', '{function_name}' );
}}

'''
    return cpt_code

def register_cpt_worker(cpt_queue, project_path, project_folder_name, results_queue):
    """Worker thread to register CPTs"""
    while True:
        try:
            cpt_data = cpt_queue.get(timeout=1)
            if cpt_data is None:
                break
            
            section_name = cpt_data['section']
            page_name = cpt_data['page']
            
            log_message(f"Registering CPT for section: '{section_name}' on page '{page_name}'", "⚙️", "PROCESSING")
            
            # Generate CPT code
            cpt_code = generate_cpt_code(section_name, page_name, project_folder_name)
            
            # Append to functions.php
            functions_path = os.path.join(project_path, "functions.php")
            
            with log_lock:
                with open(functions_path, 'a', encoding='utf-8') as functions_file:
                    functions_file.write(cpt_code)
            
            log_message(f"CPT registered successfully: '{section_name}'", "✅", "SUCCESS")
            results_queue.put({'status': 'success', 'section': section_name, 'page': page_name})
            
        except Exception as e:
            log_message(f"Error registering CPT: {str(e)}", "❌", "ERROR")
            results_queue.put({'status': 'error', 'section': section_name, 'error': str(e)})
        
        finally:
            cpt_queue.task_done()

def register_all_cpts(cpt_sections, project_path, num_threads=4):
    """Register all CPTs using multithreading"""
    log_message(f"Starting CPT registration with {num_threads} threads", "🚀", "INFO")
    
    # Get project folder name
    project_folder_name = os.path.basename(project_path)
    
    # Create queues
    cpt_queue = Queue()
    results_queue = Queue()
    
    # Add all CPT sections to queue
    for cpt_data in cpt_sections:
        cpt_queue.put(cpt_data)
    
    # Create and start worker threads
    threads = []
    for i in range(num_threads):
        thread = Thread(target=register_cpt_worker, args=(cpt_queue, project_path, project_folder_name, results_queue))
        thread.daemon = True
        thread.start()
        threads.append(thread)
        log_message(f"Thread {i+1} started", "🔄", "INFO")
    
    # Wait for all tasks to complete
    cpt_queue.join()
    
    # Stop worker threads
    for _ in range(num_threads):
        cpt_queue.put(None)
    
    for thread in threads:
        thread.join()
    
    # Collect results
    results = {'success': [], 'error': []}
    while not results_queue.empty():
        result = results_queue.get()
        if result['status'] == 'success':
            results['success'].append(result)
        else:
            results['error'].append(result)
    
    return results

def generate_summary_report(results, execution_time):
    """Generate summary report in log file"""
    log_message("", "📊", "INFO")
    log_message("=" * 80, "", "INFO")
    log_message("REGISTRATION SUMMARY REPORT", "📊", "INFO")
    log_message("=" * 80, "", "INFO")
    
    total_cpts = len(results['success']) + len(results['error'])
    success_count = len(results['success'])
    error_count = len(results['error'])
    
    log_message(f"Total CPTs Processed: {total_cpts}", "📈", "INFO")
    log_message(f"Successfully Registered: {success_count}", "✅", "SUCCESS")
    log_message(f"Failed: {error_count}", "❌", "ERROR")
    log_message("", "", "INFO")
    
    if results['success']:
        log_message("Successfully Registered CPTs:", "✅", "SUCCESS")
        for result in results['success']:
            log_message(f"  ✓ {result['section']} (Page: {result['page']})", "  ", "INFO")
    
    if results['error']:
        log_message("", "", "INFO")
        log_message("Failed CPT Registrations:", "❌", "ERROR")
        for result in results['error']:
            log_message(f"  ✗ {result['section']} - Error: {result.get('error', 'Unknown error')}", "  ", "ERROR")
    
    log_message("", "", "INFO")
    log_message("=" * 80, "", "INFO")
    log_message(f"Total Execution Time: {execution_time:.2f} seconds", "⏱️", "INFO")
    log_message("=" * 80, "", "INFO")
    log_message("CPT Registration Process Completed Successfully!", "🎉", "SUCCESS")

def main():
    """Main function to orchestrate CPT registration"""
    global start_time
    start_time = time.time()
    
    print("🚀 Starting WordPress CPT Registration Script...\n")
    
    # Load environment variables
    project_path = load_environment_variables()
    if not project_path:
        print("❌ Failed to load project path. Exiting...")
        return
    
    # Initialize log file
    initialize_log_file(project_path)
    
    # Read CPT sections data
    content = read_cpt_sections_data(project_path)
    if not content:
        log_message("No CPT sections data found. Exiting...", "❌", "ERROR")
        return
    
    # Parse CPT sections
    cpt_sections = parse_cpt_sections(content)
    if not cpt_sections:
        log_message("No CPT sections found to register. Exiting...", "⚠️", "WARNING")
        return
    
    # Register all CPTs with multithreading
    results = register_all_cpts(cpt_sections, project_path, num_threads=4)
    
    # Calculate execution time
    execution_time = time.time() - start_time
    
    # Generate summary report
    generate_summary_report(results, execution_time)
    
    print(f"\n✅ Script completed successfully!")
    print(f"⏱️  Total execution time: {execution_time:.2f} seconds")
    print(f"📄 Check the log file for details: {log_file_path}")

if __name__ == "__main__":
    main()