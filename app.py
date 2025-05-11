import json
import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import sys
sys.path.insert(0, '/usr/lib/chromium-browser/chromedriver') # Specific to your environment
import os
os.environ['DISPLAY'] = ':0' # Specific to your environment
import chromadb
from chromadb.config import Settings
# import requests # Not used directly in app.py's core logic anymore for Gemini
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai

# --- New imports for Queueing System ---
import queue
import threading
import uuid
# --- End New imports ---

# Initialize Flask app after imports
app = Flask(__name__)
CORS(app)

# Initialize ChromaDB client
# Ensure Settings are appropriate if you persist ChromaDB
client = chromadb.Client(Settings())

# --- Global components for the Queueing System ---
request_queue = queue.Queue()  # Thread-safe queue to hold incoming tasks
results_store = {}             # Dictionary to store results: {request_id: (response_text, context_text)}
result_events = {}             # Dictionary to store threading.Event objects: {request_id: event_object}
shared_resource_lock = threading.Lock() # Lock for secure access to shared dictionaries

# --- Gemini API Key Configuration ---
# IMPORTANT: Replace these with your actual API keys, preferably loaded from environment variables
GEMINI_API_KEYS = [
    "AIzaSyCjPOOS0s8Uw3f6RzUXpMYUziTMINFtkMs",
    "AIzaSyCuy_YHMwDlQmG0_rfPKNIQxUTQ1HB_-pE",
    # Add more keys if available
]
if not GEMINI_API_KEYS or GEMINI_API_KEYS[0] == "YOUR_GEMINI_API_KEY_1":
    print("WARNING: Update GEMINI_API_KEYS in app.py with your actual API keys.")
    # Fallback to a single key from original code if not configured, but rotation won't work.
    # Consider raising an error or using a default non-functional key to force configuration.
    GEMINI_API_KEYS = ["AIzaSyDICG9LCC31Im5u72t4CK1Wp9ByA8zIWRs"] # Original key as fallback

current_api_key_index = 0
# --- End Gemini API Key Configuration ---


# Webscraping - Table data - Generalized function
def scrape_table_from_page(url, click_sequence, table_selector, headless=True):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument('--window-size=1920,1080')

    driver = webdriver.Chrome(options=options)
    driver.get(url)
    try:
        wait = WebDriverWait(driver, 15)
        for selector in click_sequence:
            element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            element.click()
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, table_selector)))
        time.sleep(2)
        print(f"✅ Table loaded from {url}!")
        soup = BeautifulSoup(driver.page_source, "html.parser")
        table = soup.select_one(table_selector)
        if table:
            headings = [th.get_text(strip=True) for th in table.find_all("th")]
            data = []
            for row in table.find_all("tr"):
                cols = [col.get_text(strip=True) for col in row.find_all("td")]
                if cols:
                    row_data = {headings[i]: cols[i] for i in range(len(cols)) if i < len(headings)}
                    data.append(row_data)
            return json.dumps(data, indent=4)
        else:
            print(f"⚠️ Table not found at {url} with selector {table_selector}!")
            return None
    except Exception as e:
        print(f"❌ Error scraping {url}: {e}")
        return None
    finally:
        driver.quit()

def upload_to_chromadb(json_string, name, desc="", collection_name="kmit"):
    if not json_string: # Check if json_string is None or empty
        print(f"⚠️ Skipping upload for '{name}' to ChromaDB as data is empty.")
        return
    if not isinstance(json_string, str):
        print(f"❌ json_string for '{name}' must be a string, got {type(json_string)}")
        return
    if not isinstance(name, str):
        print("❌ name must be a string")
        return

    embedding_function = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_function
    )
    metadata = {"description": desc} if desc else {}
    collection.add(
        documents=[json_string],
        metadatas=[metadata],
        ids=[name]
    )
    print(f"✅ Document '{name}' uploaded to ChromaDB collection '{collection_name}'")

def query_chroma(collection, query):
    results = collection.query(query_texts=[query], n_results=5)
    # Ensure results are not empty and documents exist
    if results and results['documents'] and results['documents'][0]:
        return results['documents'][0] # Retrieve the most relevant document list (which contains strings)
    return "" # Return empty string if no relevant documents found

def generate_response_with_gemini(context, query, api_key):
    # Configure Gemini with the specific API key for this call
    genai.configure(api_key=api_key)
    prompt = f"""
    Your name is Chitraguptha.
    Answer in a polite and detailed manner like you are a virtual assistant for the KMIT college website.
    Kmit website data:{context}
    Answer the query:{query}
    Please answer in a full sentence and in plain text.
    """
    try:
        model = genai.GenerativeModel("gemini-2.0-flash-lite") # Using gemini-1.5-pro
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"❌ Gemini API Error: {e} (using key ending ...{api_key[-4:] if api_key else 'N/A'})")
        # If a specific error indicates a rate limit, we might want to signal that
        # for more intelligent key rotation, but for now, just return an error message.
        if "API key not valid" in str(e) or "permission" in str(e).lower():
            raise # Re-raise specific key-related errors to be handled by worker's rotation
        return f"Sorry, I encountered an issue while trying to generate a response. Error: {e}"


def run_query_for_worker(collection, query, api_key_to_use):
    """
    This function will be called by the worker.
    It includes the ChromaDB query and the Gemini API call.
    """
    retrieved_data_list = query_chroma(collection, query) # This is a list of strings
    
    # Join the list of document strings into a single context string
    context = " ".join(retrieved_data_list) if isinstance(retrieved_data_list, list) else retrieved_data_list
    
    if not context:
      print(f"⚠️ No context found in ChromaDB for query: {query}")
      # Decide if you want to proceed to Gemini without context or return a specific message
      # context = "No specific information found in the college database for this query."

    # print(f"Worker CONTEXT for query '{query}': \n{context[:200]}...") # Print start of context

    response_text = generate_response_with_gemini(context, query, api_key_to_use)
    # print(f"\nWorker 🔍 Query: {query}\nWorker ✅ Response: {response_text}\n")
    return response_text, context


# --- Background Worker Function for Gemini API Calls---
def gemini_api_call_worker():
    global current_api_key_index
    global GEMINI_API_KEYS

    print("INFO: Gemini API call worker thread started.")
    kmit_collection = None
    try:
        kmit_collection = client.get_collection(name="kmit")
    except Exception as e:
        print(f"CRITICAL: Worker could not get ChromaDB collection 'kmit'. Error: {e}. Worker stopping.")
        return # Stop worker if DB is not accessible

    while True:
        task_data = None
        request_id = None
        original_thread_event = None
        try:
            task_data = request_queue.get() # Blocks until an item is available
            request_id = task_data['id']
            user_query = task_data['query']
            original_thread_event = task_data['event']

            print(f"WORKER: Processing request_id: {request_id} for query: '{user_query}'")

            api_key_to_use = None
            response_text = None
            context_text = ""
            max_retries_per_key = 2 # How many times to retry with the same key for generic errors
            total_key_rotations = 0 # How many times we've tried all keys

            while total_key_rotations < len(GEMINI_API_KEYS) * 2: # Try each key up to twice
                with shared_resource_lock:
                    current_api_key_index = (current_api_key_index % len(GEMINI_API_KEYS)) # Ensure index is valid
                    api_key_to_use = GEMINI_API_KEYS[current_api_key_index]
                
                print(f"WORKER: Attempting query for {request_id} with API Key ending ...{api_key_to_use[-4:]}")
                
                try:
                    # Call the combined function
                    response_text, context_text = run_query_for_worker(kmit_collection, user_query, api_key_to_use)
                    
                    # Check if response indicates an internal Gemini error passed as text
                    if "Sorry, I encountered an issue" in response_text and "API key not valid" not in response_text:
                        # This might be a model issue, not a key issue. Break to not rotate uselessly.
                        print(f"WORKER: Gemini returned an issue for {request_id}, not necessarily key-related. Using response.")
                        break 
                    elif "API key not valid" in response_text or "permission" in str(response_text).lower(): # Assuming generate_response_with_gemini returns this string
                        print(f"WORKER: API Key ...{api_key_to_use[-4:]} seems invalid or has permission issues for {request_id}.")
                        raise ValueError("Simulated API Key Error for rotation") # Force rotation

                    print(f"WORKER: Successfully processed {request_id} with key ...{api_key_to_use[-4:]}")
                    break # Success, exit retry loop

                except ValueError as ve: # Catch our simulated key error or actual key errors re-raised
                    print(f"WORKER: Key ...{api_key_to_use[-4:]} failed for {request_id}. Rotating. Error: {ve}")
                    with shared_resource_lock:
                        current_api_key_index = (current_api_key_index + 1) % len(GEMINI_API_KEYS)
                    total_key_rotations += 1
                    time.sleep(1) # Brief pause before trying next key
                    if response_text is None: # Ensure response_text is not None if all keys fail
                        response_text = "Sorry, all attempts to contact the AI service failed due to key or permission issues."


                except Exception as e:
                    print(f"WORKER: Error during Gemini call for {request_id} with key ...{api_key_to_use[-4:]}. Error: {e}")
                    # For other errors, you might retry with the same key or rotate.
                    # For simplicity here, we'll rotate.
                    with shared_resource_lock:
                        current_api_key_index = (current_api_key_index + 1) % len(GEMINI_API_KEYS)
                    total_key_rotations +=1
                    time.sleep(1)
                    if response_text is None:
                        response_text = f"An unexpected error occurred while processing your query. Error: {e}"
            
            if response_text is None: # Should be set by loops above
                 response_text = "Apologies, the AI service could not process your request at this time."


            print(f"WORKER: Final response for {request_id}: '{response_text[:100]}...'")
            with shared_resource_lock:
                results_store[request_id] = (response_text, context_text) # Store as tuple
            original_thread_event.set()
            request_queue.task_done()

        except Exception as e_outer:
            print(f"CRITICAL WORKER ERROR: {e_outer} for task_data: {task_data}")
            if request_id and original_thread_event and not original_thread_event.is_set():
                with shared_resource_lock:
                    results_store[request_id] = (f"A critical error occurred in the processing worker: {e_outer}", "")
                original_thread_event.set() # Ensure Flask route doesn't hang indefinitely
# --- End Background Worker ---


# --- Flask Route for Queries ---
@app.route('/query', methods=['POST'])
def process_query_endpoint():
    try:
        data = request.get_json()
        if not data or 'query' not in data:
            return jsonify({'error': 'No query provided'}), 400

        user_query = data['query']
        request_id = str(uuid.uuid4())
        event_for_this_request = threading.Event()

        task_to_queue = {
            'id': request_id,
            'query': user_query,
            'event': event_for_this_request
        }
        request_queue.put(task_to_queue)
        print(f"FLASK_ROUTE: Queued request {request_id} for query: '{user_query}'")

        # Wait for the worker to process this request, with a timeout
        event_set = event_for_this_request.wait(timeout=120.0) # Increased timeout

        if not event_set:
            print(f"FLASK_ROUTE: Timeout for request {request_id}")
            # Attempt to clean up, though worker might still complete and log an error if it tries to set results
            with shared_resource_lock:
                results_store.pop(request_id, None)
            return jsonify({'error': 'Processing your request timed out. Please try again.'}), 504

        with shared_resource_lock:
            response_tuple = results_store.pop(request_id, ("Error: Result not found in store.", ""))
        
        final_response_text, _ = response_tuple # context_text is also available if needed

        print(f"FLASK_ROUTE: Responding for request {request_id}")
        return jsonify({
            'query': user_query,
            'response': final_response_text,
            # 'context': context_text # You can uncomment this if frontend needs it
        })

    except Exception as e:
        print(f"FLASK_ROUTE_ERROR: {e}")
        return jsonify({'error': f'An internal server error occurred: {str(e)}'}), 500
# --- End Flask Route ---

# --- Main Execution Block ---
if __name__ == '__main__':
    print("INFO: Starting application setup...")

    # SCRAPING DATA FROM KMIT WEBSITE (runs once at startup)
    print("INFO: Starting data scraping...")
    # (Your scraping calls - ensure they handle None results gracefully for ChromaDB upload)
    # Example:
    placement_url = "https://kmit.in/placements/placement.php"
    clicks_24_25 = ["body > div.background > div:nth-child(2) > div > ul > li:nth-child(4) > a"]
    table_selector_24_25 = "#cp2024-25 > div > table"
    placements_data_24_25 = scrape_table_from_page(placement_url, clicks_24_25, table_selector_24_25)

    clicks_23_24 = [
        "body > div.background > div:nth-child(2) > div > ul > li:nth-child(4) > a",
        "#campus > div > ul > li:nth-child(2) > a > b"
    ]
    table_selector_23_24 = "#cp2023-24 > div > table"
    placements_data_23_24 = scrape_table_from_page(placement_url, clicks_23_24, table_selector_23_24)

    admission_url = "https://kmit.in/admissions/admission-procedure.php"
    admission_table_selector = "div.box table.table.table-striped.custom"
    admissions_data = scrape_table_from_page(admission_url, [], admission_table_selector)
    
    courses_url = "https://kmit.in/admissions/coursesoffered.php"
    courses_table_selector = "div.box table.table.table-striped.custom"
    courses_data = scrape_table_from_page(courses_url, [], courses_table_selector)

    cse_url = "https://www.kmit.in/department/faculty_CSE.php"
    cse_table_selector = "div.box table.table.table-striped.custom"
    cse_data = scrape_table_from_page(cse_url, [], cse_table_selector)

    csm_url = "https://www.kmit.in/department/faculty_csm.php"
    csm_table_selector = "div.box table.table.table-striped.custom"
    csm_data = scrape_table_from_page(csm_url, [], csm_table_selector)

    it_url = "https://www.kmit.in/department/faculty_it.php"
    it_table_selector = "div.box table.table.table-striped.custom"
    it_data = scrape_table_from_page(it_url, [], it_table_selector)

    research_url = "https://kmit.in/research/researchpublications.php"
    research_table_selector = "div.box table.table.table-striped"
    research_data = scrape_table_from_page(research_url, [], research_table_selector)

    contact_url = "https://kmit.in/examination/contact_exam.php"
    contact_table_selector = "table" # Simpler selector
    contact_data = scrape_table_from_page(contact_url, [], contact_table_selector)
    
    council_url = "https://kmit.in/intiatives/studentcouncil.php"
    council_table_selector = "div.box table.table.table-striped" # Original selector
    council_data = scrape_table_from_page(council_url, [], council_table_selector)
    print("INFO: Data scraping finished.")

    # UPLOADING DATA TO CHROMADB
    print("INFO: Uploading data to ChromaDB...")
    upload_to_chromadb(placements_data_24_25, "placements_data_2024_2025", "Placements Data 2024-2025")
    upload_to_chromadb(placements_data_23_24, "placements_data_2023_2024", "Placements Data 2023-2024")
    upload_to_chromadb(admissions_data, "admission_procedure_data", "Admission Procedure Information")
    upload_to_chromadb(courses_data, "courses_offered_data", "Courses Offered Information")
    upload_to_chromadb(cse_data, "cse_faculty_data", "CSE Faculty Information")
    upload_to_chromadb(csm_data, "csm_faculty_data", "CSM Faculty Information")
    upload_to_chromadb(it_data, "it_faculty_data", "IT Faculty Information")
    upload_to_chromadb(research_data, "research_publications_data", "Research Publications")
    upload_to_chromadb(contact_data, "examination_contact_data", "Examination Branch Contacts")
    upload_to_chromadb(council_data, "student_council_data", "Student Council Information")
    print("INFO: ChromaDB upload finished.")

    # Start the background worker thread for Gemini API calls
    print("INFO: Starting Gemini API call worker thread...")
    api_worker_thread = threading.Thread(target=gemini_api_call_worker, daemon=True)
    api_worker_thread.start()

    # Run Flask app
    print(f"INFO: Starting Flask app on port 5000. Use threaded={True} for dev server with this queue model.")
    # For Flask's development server, `threaded=True` allows it to handle multiple
    # requests concurrently using threads, which is necessary for our event waiting model.
    # For production, use a proper WSGI server like Gunicorn with appropriate worker config.
    app.run(debug=False, host='0.0.0.0', port=5000, threaded=True)