import os
import re
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from threading import Thread, Lock
from queue import Queue
from pymongo import MongoClient
from urllib.parse import urlparse

# Global variables
log_file_path = None
log_lock = Lock()
start_time = None

# ============================================================================
# EXCLUSION CONFIGURATION
# ============================================================================
# Add keywords here to exclude sections containing these terms (case-insensitive)
# Example: 'blog' will exclude: blog, blogs, Blog Section, blog-container, etc.
EXCLUDE_KEYWORDS = [
    'blog',      # Excludes all blog-related sections
    # Add more keywords below as needed:
    # 'test',
    # 'demo',
    # 'sample',
]
# ============================================================================

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
        project_path = os.getenv("PROJECT_THEME_PATH")
        mongo_uri = os.getenv("MONGO_URI")
        
        if not project_path:
            log_message("PROJECT_THEME_PATH not found in .env file", "❌", "ERROR")
            return None, None
        
        if not mongo_uri:
            log_message("MONGO_URI not found in .env file", "❌", "ERROR")
            return None, None
        
        if not os.path.exists(project_path):
            log_message(f"Project path does not exist: {project_path}", "❌", "ERROR")
            return None, None
        
        log_message(f"Project path loaded: {project_path}", "✅", "SUCCESS")
        log_message(f"MongoDB URI loaded: {mongo_uri}", "✅", "SUCCESS")
        return project_path, mongo_uri
    
    except Exception as e:
        log_message(f"Error loading environment variables: {str(e)}", "❌", "ERROR")
        return None, None

def connect_to_mongodb(mongo_uri):
    """Connect to MongoDB and return database instance"""
    log_message("Connecting to MongoDB", "🔌", "INFO")
    
    try:
        # Parse the MongoDB URI to extract database name
        parsed_uri = urlparse(mongo_uri)
        db_name = parsed_uri.path.lstrip('/')
        
        if not db_name:
            log_message("Database name not found in MONGO_URI", "❌", "ERROR")
            return None, None
        
        # Connect to MongoDB
        client = MongoClient(mongo_uri)
        
        # Test connection
        client.admin.command('ping')
        
        db = client[db_name]
        log_message(f"Successfully connected to MongoDB database: {db_name}", "✅", "SUCCESS")
        
        return client, db
    
    except Exception as e:
        log_message(f"Error connecting to MongoDB: {str(e)}", "❌", "ERROR")
        return None, None

def should_exclude_section(section_name):
    """
    Check if section should be excluded based on keyword matching.
    Uses the EXCLUDE_KEYWORDS list defined at the top of the file.
    
    Matching is case-insensitive and checks if keyword appears anywhere in section name.
    
    Examples with 'blog' keyword:
    - blog, Blog, BLOG ✓ excluded
    - blogs, Blogs, BLOGS ✓ excluded
    - blog-section, Blog Section, BlogSection ✓ excluded
    - blog-container, Blog Container ✓ excluded
    - my-blog, new-blogs, blogpost ✓ excluded
    """
    # Convert to lowercase for case-insensitive matching
    section_lower = section_name.lower()
    
    # Check if any exclude keyword is present in the section name
    for keyword in EXCLUDE_KEYWORDS:
        if keyword.lower() in section_lower:
            log_message(f"Excluding section '{section_name}' (contains keyword: '{keyword}')", "🚫", "INFO")
            return True
    
    return False

def clean_section_name(section_name):
    """Clean section name by removing emojis and special characters"""
    # Remove emojis and special characters, keep only alphanumeric, spaces, and hyphens
    cleaned = re.sub(r'[^\w\s-]', '', section_name, flags=re.UNICODE)
    # Replace multiple spaces with single space
    cleaned = re.sub(r'\s+', ' ', cleaned)
    # Trim whitespace
    cleaned = cleaned.strip()
    return cleaned

def get_latest_document_from_collection(collection):
    """Get the latest document from collection based on _id (ObjectId timestamp)"""
    try:
        # Find the latest document by sorting _id in descending order
        latest_doc = collection.find_one(sort=[("_id", -1)])
        
        if latest_doc:
            doc_id = latest_doc.get('_id')
            log_message(f"Found latest document with _id: {doc_id}", "📄", "INFO")
            return latest_doc
        else:
            log_message("No documents found in collection", "⚠️", "WARNING")
            return None
    
    except Exception as e:
        log_message(f"Error fetching latest document: {str(e)}", "❌", "ERROR")
        return None

def fetch_cpt_sections_from_mongodb(db):
    """Fetch CPT sections data from MongoDB using aggregation query"""
    log_message("Fetching CPT sections from MongoDB", "📊", "INFO")
    
    try:
        # Get all collections in the database
        collection_names = db.list_collection_names()
        
        if not collection_names:
            log_message("No collections found in database", "❌", "ERROR")
            return None
        
        log_message(f"Found {len(collection_names)} collection(s): {', '.join(collection_names)}", "📁", "INFO")
        
        # Use the first collection (or you can add logic to select specific one)
        collection_name = collection_names[0]
        collection = db[collection_name]
        log_message(f"Using collection: {collection_name}", "📁", "INFO")
        
        # Get the latest document from the collection
        latest_document = get_latest_document_from_collection(collection)
        
        if not latest_document:
            log_message("No document found in collection", "❌", "ERROR")
            return None
        
        # Get document identifier for logging
        doc_identifier = latest_document.get('_id', 'Unknown')
        log_message(f"Processing latest document: {doc_identifier}", "📋", "INFO")
        
        # MongoDB Aggregation Pipeline - process only the latest document
        # Note: Using simplified pipeline for compatibility with older MongoDB versions
        pipeline = [
            # Match only the latest document by _id
            {"$match": {"_id": latest_document['_id']}},
            {"$unwind": "$pages"},
            {"$unwind": "$pages.sections"},
            # Only CPT type sections
            {"$match": {"pages.sections.type": "CPT (Custom post type)"}},
            # Use section name directly (we'll clean it in Python)
            {
                "$addFields": {
                    "cleanSectionName": "$pages.sections.name"
                }
            },
            # Group by section name -> find which pages it appears in
            {
                "$group": {
                    "_id": "$cleanSectionName",
                    "pages": {"$addToSet": "$pages.page"}
                }
            },
            # Split into similar and unique sections
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
        
        # Execute aggregation
        result = list(collection.aggregate(pipeline))
        
        if not result:
            log_message("No CPT sections found in MongoDB", "⚠️", "WARNING")
            return None
        
        cpt_data = result[0]
        
        # Clean section names and filter out excluded sections
        log_message("Cleaning section names and applying exclusion filters", "🔍", "INFO")
        
        # Filter similar sections
        filtered_similar = []
        for section in cpt_data.get('similarSections', []):
            cleaned_name = clean_section_name(section['sectionName'])
            if not should_exclude_section(cleaned_name):
                section['sectionName'] = cleaned_name
                filtered_similar.append(section)
        
        cpt_data['similarSections'] = filtered_similar
        
        # Filter unique sections
        filtered_unique = []
        for page_data in cpt_data.get('uniqueSections', []):
            cleaned_names = []
            for name in page_data['sectionNames']:
                cleaned_name = clean_section_name(name)
                if not should_exclude_section(cleaned_name):
                    cleaned_names.append(cleaned_name)
            
            # Only include page if it has at least one non-excluded section
            if cleaned_names:
                page_data['sectionNames'] = cleaned_names
                filtered_unique.append(page_data)
        
        cpt_data['uniqueSections'] = filtered_unique
        
        similar_count = len(cpt_data.get('similarSections', []))
        unique_count = len(cpt_data.get('uniqueSections', []))
        
        log_message(f"After filtering: {similar_count} similar sections (appearing on multiple pages)", "📊", "INFO")
        log_message(f"After filtering: {unique_count} pages with unique sections", "📊", "INFO")
        
        # Log details
        for section in cpt_data.get('similarSections', []):
            log_message(f"Similar Section: '{section['sectionName']}' appears on pages: {', '.join(section['pages'])}", "🔄", "INFO")
        
        for page_data in cpt_data.get('uniqueSections', []):
            log_message(f"Unique Sections on '{page_data['page']}': {', '.join(page_data['sectionNames'])}", "📄", "INFO")
        
        return cpt_data
    
    except Exception as e:
        log_message(f"Error fetching CPT sections from MongoDB: {str(e)}", "❌", "ERROR")
        return None

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
            cpt_type = cpt_data.get('type', 'unique')  # 'similar' or 'unique'
            
            log_message(f"Registering {cpt_type} CPT for section: '{section_name}' on page '{page_name}'", "⚙️", "PROCESSING")
            
            # Generate CPT code
            cpt_code = generate_cpt_code(section_name, page_name, project_folder_name)
            
            # Append to functions.php
            functions_path = os.path.join(project_path, "functions.php")
            
            with log_lock:
                with open(functions_path, 'a', encoding='utf-8') as functions_file:
                    functions_file.write(cpt_code)
            
            log_message(f"CPT registered successfully: '{section_name}' ({cpt_type})", "✅", "SUCCESS")
            results_queue.put({'status': 'success', 'section': section_name, 'page': page_name, 'type': cpt_type})
            
        except Exception as e:
            log_message(f"Error registering CPT: {str(e)}", "❌", "ERROR")
            results_queue.put({'status': 'error', 'section': section_name, 'error': str(e)})
        
        finally:
            cpt_queue.task_done()

def prepare_cpt_registration_queue(cpt_data):
    """Prepare CPT registration queue from MongoDB data"""
    log_message("Preparing CPT registration queue", "📋", "INFO")
    
    cpt_queue_data = []
    
    # Process similar sections (only register once per section)
    similar_sections = cpt_data.get('similarSections', [])
    for section in similar_sections:
        section_name = section['sectionName']
        pages = section['pages']
        
        # Register only once with all pages listed
        cpt_queue_data.append({
            'section': section_name,
            'page': ', '.join(pages),  # Combine all pages
            'type': 'similar'
        })
        log_message(f"Queued similar section: '{section_name}' (appears on: {', '.join(pages)})", "📌", "INFO")
    
    # Process unique sections (register separately for each page)
    unique_sections = cpt_data.get('uniqueSections', [])
    for page_data in unique_sections:
        page_name = page_data['page']
        section_names = page_data['sectionNames']
        
        for section_name in section_names:
            cpt_queue_data.append({
                'section': section_name,
                'page': page_name,
                'type': 'unique'
            })
            log_message(f"Queued unique section: '{section_name}' on page '{page_name}'", "📌", "INFO")
    
    total_registrations = len(cpt_queue_data)
    log_message(f"Total CPT registrations to process: {total_registrations}", "📊", "INFO")
    
    return cpt_queue_data

def register_all_cpts(cpt_queue_data, project_path, num_threads=4):
    """Register all CPTs using multithreading"""
    log_message(f"Starting CPT registration with {num_threads} threads", "🚀", "INFO")
    
    # Get project folder name
    project_folder_name = os.path.basename(project_path)
    
    # Create queues
    cpt_queue = Queue()
    results_queue = Queue()
    
    # Add all CPT sections to queue
    for cpt_data in cpt_queue_data:
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
    results = {'success': [], 'error': [], 'similar': [], 'unique': []}
    while not results_queue.empty():
        result = results_queue.get()
        if result['status'] == 'success':
            results['success'].append(result)
            if result.get('type') == 'similar':
                results['similar'].append(result)
            else:
                results['unique'].append(result)
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
    similar_count = len(results['similar'])
    unique_count = len(results['unique'])
    
    log_message(f"Total CPTs Processed: {total_cpts}", "📈", "INFO")
    log_message(f"Successfully Registered: {success_count}", "✅", "SUCCESS")
    log_message(f"  - Similar Sections (Multi-page): {similar_count}", "🔄", "INFO")
    log_message(f"  - Unique Sections (Single-page): {unique_count}", "📄", "INFO")
    log_message(f"Failed: {error_count}", "❌", "ERROR")
    log_message("", "", "INFO")
    
    if results['similar']:
        log_message("Similar Sections Registered (appear on multiple pages):", "🔄", "SUCCESS")
        for result in results['similar']:
            log_message(f"  ✓ {result['section']} (Pages: {result['page']})", "  ", "INFO")
    
    if results['unique']:
        log_message("", "", "INFO")
        log_message("Unique Sections Registered (page-specific):", "📄", "SUCCESS")
        for result in results['unique']:
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
    
    print("🚀 Starting WordPress CPT Registration Script (MongoDB Edition)...\n")
    
    # Load environment variables
    project_path, mongo_uri = load_environment_variables()
    if not project_path or not mongo_uri:
        print("❌ Failed to load environment variables. Exiting...")
        return
    
    # Initialize log file
    initialize_log_file(project_path)
    
    # Connect to MongoDB
    mongo_client, db = connect_to_mongodb(mongo_uri)
    if db is None:
        log_message("Failed to connect to MongoDB. Exiting...", "❌", "ERROR")
        return
    
    try:
        # Fetch CPT sections data from MongoDB
        cpt_data = fetch_cpt_sections_from_mongodb(db)
        if not cpt_data:
            log_message("No CPT sections data found in MongoDB. Exiting...", "❌", "ERROR")
            return
        
        # Prepare CPT registration queue
        cpt_queue_data = prepare_cpt_registration_queue(cpt_data)
        if not cpt_queue_data:
            log_message("No CPT sections to register. Exiting...", "⚠️", "WARNING")
            return
        
        # Register all CPTs with multithreading
        results = register_all_cpts(cpt_queue_data, project_path, num_threads=4)
        
        # Calculate execution time
        execution_time = time.time() - start_time
        
        # Generate summary report
        generate_summary_report(results, execution_time)
        
        print(f"\n✅ Script completed successfully!")
        print(f"⏱️  Total execution time: {execution_time:.2f} seconds")
        print(f"📄 Check the log file for details: {log_file_path}")
    
    finally:
        # Close MongoDB connection
        if mongo_client:
            mongo_client.close()
            log_message("MongoDB connection closed", "🔌", "INFO")

if __name__ == "__main__":
    main()