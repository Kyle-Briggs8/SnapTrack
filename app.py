import os
import base64
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
from google.cloud import vision
import google.generativeai as genai
import io

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Configure Gemini API
# Get API key from environment variable or use default
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
print(f"[STARTUP] Checking for GEMINI_API_KEY...")
print(f"[STARTUP] Environment variable present: {bool(os.environ.get('GEMINI_API_KEY'))}")
if GEMINI_API_KEY:
    print(f"[STARTUP] GEMINI_API_KEY found: {GEMINI_API_KEY[:20]}...")
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print(f"[INFO] Gemini API configured successfully with key: {GEMINI_API_KEY[:20]}...")
    except Exception as e:
        print(f"[WARNING] Failed to configure Gemini API: {e}")
        GEMINI_API_KEY = None
else:
    print(f"[INFO] GEMINI_API_KEY not set - will use Vision API only")

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def detect_food_items(image_path):
    """Use Google Vision API to detect food items in the image"""
    try:
        # Try to get credentials from environment variable, or use default path
        credentials_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        
        # Default path (can be customized)
        default_path = r"C:\Users\kyle\Downloads\snaptrack-482706-0d453712c512.json"
        
        # If not set, try default path
        if not credentials_path:
            if os.path.exists(default_path):
                os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = default_path
                credentials_path = default_path
            else:
                raise Exception(
                    'Google Cloud credentials not found. Please set the GOOGLE_APPLICATION_CREDENTIALS '
                    'environment variable to the path of your service account JSON key file. '
                    'See README.md for setup instructions.'
                )
        elif not os.path.exists(credentials_path):
            raise Exception(
                f'Credentials file not found at: {credentials_path}. '
                'Please check the path and try again.'
            )
        
        # Initialize the Vision API client
        client = vision.ImageAnnotatorClient()
        
        # Read the image file
        with io.open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        
        # Perform multiple detection types for comprehensive results
        detected_items = []
        seen_descriptions = set()
        
        # 1. Web Detection - Provides more descriptive labels based on web context
        # This often gives more detailed descriptions like "hamburger with lettuce and tomato"
        web_response = client.web_detection(image=image)
        web_detection = web_response.web_detection
        
        # Best guess labels from web detection (often more descriptive)
        if web_detection.best_guess_labels:
            for label in web_detection.best_guess_labels:
                desc = label.label.lower()
                if desc not in seen_descriptions:
                    detected_items.append({
                        'description': label.label,
                        'confidence': 95.0,  # Best guess labels don't have scores, but are high confidence
                        'type': 'web_best_guess'
                    })
                    seen_descriptions.add(desc)
        
        # Web entities (descriptive entities found on the web)
        if web_detection.web_entities:
            for entity in web_detection.web_entities:
                if entity.score > 0.5 and entity.description:
                    desc = entity.description.lower()
                    # Filter out very generic terms
                    if desc not in seen_descriptions and len(desc) > 2:
                        # Ensure confidence is between 0-100%
                        confidence = min(100.0, round(entity.score * 100, 2))
                        detected_items.append({
                            'description': entity.description,
                            'confidence': confidence,
                            'type': 'web_entity'
                        })
                        seen_descriptions.add(desc)
        
        # 2. Object Localization - Detects specific objects in the image
        objects_response = client.object_localization(image=image)
        objects = objects_response.localized_object_annotations
        
        for obj in objects:
            if obj.score > 0.5:
                desc = obj.name.lower()
                if desc not in seen_descriptions:
                    # Ensure confidence is between 0-100%
                    confidence = min(100.0, round(obj.score * 100, 2))
                    detected_items.append({
                        'description': obj.name,
                        'confidence': confidence,
                        'type': 'object'
                    })
                    seen_descriptions.add(desc)
        
        # 3. Label Detection - General labels (fallback)
        label_response = client.label_detection(image=image)
        labels = label_response.label_annotations
        
        for label in labels:
            if label.score > 0.5:
                desc = label.description.lower()
                # Skip very generic terms if we already have specific ones
                generic_terms = {'food', 'dish', 'cuisine', 'meal', 'ingredient'}
                if desc not in seen_descriptions and desc not in generic_terms:
                    # Ensure confidence is between 0-100%
                    confidence = min(100.0, round(label.score * 100, 2))
                    detected_items.append({
                        'description': label.description,
                        'confidence': confidence,
                        'type': 'label'
                    })
                    seen_descriptions.add(desc)
        
        # Check for errors
        if label_response.error.message:
            raise Exception(f'Google Vision API error: {label_response.error.message}')
        
        # Sort by confidence (highest first)
        detected_items.sort(key=lambda x: x['confidence'], reverse=True)
        
        return detected_items
    
    except Exception as e:
        raise Exception(f'Error detecting food items: {str(e)}')

def analyze_food_with_gemini(image_path):
    """Use Google Gemini API to get detailed food descriptions"""
    try:
        # Check if Gemini API key is configured
        if not GEMINI_API_KEY:
            raise Exception(
                'Gemini API key not found. Please set the GEMINI_API_KEY environment variable. '
                'Get your API key from https://ai.google.dev/'
            )
        
        # Read the image file
        with io.open(image_path, 'rb') as image_file:
            image_data = image_file.read()
        
        # Initialize Gemini model (using gemini-1.5-pro for better vision capabilities)
        model = genai.GenerativeModel('gemini-1.5-pro')
        
        # Create a detailed prompt for food analysis
        prompt = """You are a food identification expert. Analyze this food image CAREFULLY and accurately.

CRITICAL RULES:
1. ONLY describe what you can ACTUALLY SEE in the image. Do NOT guess or assume items that aren't visible.
2. Be extremely specific about ingredients. NEVER use generic terms like "burger" or "cheeseburger" alone.
3. If you see a burger, describe it in detail: "Hamburger with [specific ingredients you can see]"
4. If you DON'T see fries, do NOT mention fries. Only list items that are clearly visible.

REQUIRED FORMAT - Provide your response in this exact structure:

MAIN ITEM: [One detailed description of the primary food item with ALL visible ingredients]
Example: "Hamburger with grilled beef patty, fresh iceberg lettuce, sliced red tomatoes, dill pickles, and special sauce on a toasted sesame seed bun"

ADDITIONAL ITEMS: [List any other food items you can clearly see, or write "None" if there are none]
Example: "French fries" or "None"

DETAILED DESCRIPTION: [2-3 sentences describing everything visible in detail]

Be accurate and specific. Only mention items you can actually see in the image."""
        
        # Prepare the image
        import PIL.Image
        image = PIL.Image.open(io.BytesIO(image_data))
        
        # Generate content
        response = model.generate_content([prompt, image])
        
        # Parse the response
        description_text = response.text
        print(f"[DEBUG] Gemini raw response: {description_text[:500]}...")  # Log first 500 chars
        
        # Extract structured information
        detected_items = []
        
        # Parse the structured response format
        lines = description_text.split('\n')
        main_item = None
        additional_items = []
        
        current_section = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Look for MAIN ITEM section
            if 'MAIN ITEM:' in line.upper() or line.upper().startswith('MAIN ITEM'):
                # Extract the description after "MAIN ITEM:"
                main_item = line.split(':', 1)[1].strip() if ':' in line else line
                current_section = 'main'
                continue
            elif current_section == 'main' and main_item and not line.upper().startswith('ADDITIONAL'):
                # Continue building main item description
                if line and not line.upper().startswith('EXAMPLE'):
                    main_item += ' ' + line
            
            # Look for ADDITIONAL ITEMS section
            elif 'ADDITIONAL ITEMS:' in line.upper() or line.upper().startswith('ADDITIONAL ITEMS'):
                current_section = 'additional'
                additional_text = line.split(':', 1)[1].strip() if ':' in line else ''
                if additional_text and additional_text.upper() != 'NONE':
                    additional_items.append(additional_text)
                continue
            elif current_section == 'additional' and not line.upper().startswith('DETAILED'):
                if line and line.upper() != 'NONE' and not line.upper().startswith('EXAMPLE'):
                    additional_items.append(line)
            
            # If we find DETAILED DESCRIPTION, we can stop parsing structured format
            elif 'DETAILED DESCRIPTION:' in line.upper():
                current_section = 'detailed'
                break
        
        # Add main item if found
        if main_item and len(main_item) > 10:
            detected_items.append({
                'description': main_item,
                'confidence': 95.0,
                'type': 'gemini_main'
            })
        
        # Add additional items
        for item in additional_items:
            if item and len(item) > 5 and item.upper() != 'NONE':
                detected_items.append({
                    'description': item,
                    'confidence': 90.0,
                    'type': 'gemini_additional'
                })
        
        # If structured parsing didn't work, try to extract the most descriptive sentence
        if not detected_items:
            # Look for the longest, most descriptive sentence
            sentences = [s.strip() for s in description_text.replace('\n', ' ').split('.') 
                        if s.strip() and len(s.strip()) > 30]
            
            # Filter out generic terms
            generic_terms = ['burger', 'cheeseburger', 'food', 'dish', 'meal']
            descriptive_sentences = [s for s in sentences 
                                   if any(term in s.lower() for term in ['with', 'and', 'on', 'topped', 'served', 'includes'])
                                   and not all(term in s.lower() for term in generic_terms if len(s.split()) < 5)]
            
            if descriptive_sentences:
                # Use the most descriptive sentence
                main_desc = max(descriptive_sentences, key=len)
                detected_items.append({
                    'description': main_desc + '.',
                    'confidence': 90.0,
                    'type': 'gemini_parsed'
                })
            else:
                # Fallback: use the full description
                detected_items.append({
                    'description': description_text[:300] + ('...' if len(description_text) > 300 else ''),
                    'confidence': 85.0,
                    'type': 'gemini_full'
                })
        
        return {
            'items': detected_items,
            'full_description': description_text,
            'source': 'gemini'
        }
    
    except Exception as e:
        raise Exception(f'Error analyzing food with Gemini: {str(e)}')

@app.route('/')
def index():
    """Render the main page"""
    return render_template('index.html')

@app.route('/api/status')
def api_status():
    """Check API status for debugging"""
    return jsonify({
        'gemini_api_key_set': GEMINI_API_KEY is not None,
        'gemini_api_key_preview': GEMINI_API_KEY[:20] + '...' if GEMINI_API_KEY else None,
        'vision_api_configured': os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') is not None,
        'environment_gemini_key': 'SET' if os.environ.get('GEMINI_API_KEY') else 'NOT SET'
    })

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and process with Gemini API (with Vision API fallback)"""
    print(f"\n[UPLOAD] ===== Upload request received =====")
    print(f"[UPLOAD] Request method: {request.method}")
    print(f"[UPLOAD] Files in request: {list(request.files.keys())}")
    
    if 'file' not in request.files:
        print(f"[UPLOAD] ERROR: No file in request")
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    print(f"[UPLOAD] File received: {file.filename}")
    
    if file.filename == '':
        print(f"[UPLOAD] ERROR: Empty filename")
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        print(f"[UPLOAD] ERROR: Invalid file type")
        return jsonify({'error': 'Invalid file type. Please upload an image (PNG, JPG, JPEG, GIF, WEBP)'}), 400
    
    try:
        # Save the uploaded file
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        print(f"[UPLOAD] File saved to: {filepath}")
        
        # Try Gemini API first (for detailed descriptions)
        use_gemini = request.form.get('use_gemini', 'true').lower() == 'true'
        gemini_result = None
        vision_items = []
        
        # Debug: Check API key status
        print(f"[UPLOAD] ===== Checking API Status =====")
        print(f"[UPLOAD] GEMINI_API_KEY present: {GEMINI_API_KEY is not None}")
        print(f"[UPLOAD] GEMINI_API_KEY value: {GEMINI_API_KEY[:20] if GEMINI_API_KEY else 'None'}...")
        print(f"[UPLOAD] use_gemini flag: {use_gemini}")
        print(f"[UPLOAD] Environment GEMINI_API_KEY: {os.environ.get('GEMINI_API_KEY', 'NOT SET')[:20] if os.environ.get('GEMINI_API_KEY') else 'NOT SET'}...")
        
        if use_gemini and GEMINI_API_KEY:
            try:
                print(f"[UPLOAD] ===== Attempting to use Gemini API =====")
                gemini_result = analyze_food_with_gemini(filepath)
                print(f"[UPLOAD] SUCCESS: Gemini API succeeded! Returned {len(gemini_result.get('items', []))} items")
                print(f"[UPLOAD] Full description length: {len(gemini_result.get('full_description', ''))}")
            except Exception as gemini_error:
                # Fall back to Vision API if Gemini fails
                print(f"[UPLOAD] ERROR: Gemini API error, falling back to Vision API")
                print(f"[UPLOAD] Error details: {str(gemini_error)}")
                import traceback
                traceback.print_exc()
                vision_items = detect_food_items(filepath)
                print(f"[UPLOAD] Using Vision API results: {len(vision_items)} items")
        else:
            # Use Vision API if Gemini is not available or disabled
            if not GEMINI_API_KEY:
                print(f"[UPLOAD] ERROR: Gemini API key not set, using Vision API only")
                print(f"[UPLOAD] GEMINI_API_KEY value: {GEMINI_API_KEY}")
                print(f"[UPLOAD] Environment variable: {os.environ.get('GEMINI_API_KEY', 'NOT FOUND')}")
            else:
                print(f"[UPLOAD] Gemini disabled by request, using Vision API")
            vision_items = detect_food_items(filepath)
            print(f"[UPLOAD] Using Vision API results: {len(vision_items)} items")
        
        # Clean up uploaded file
        os.remove(filepath)
        
        # Return results
        if gemini_result:
            return jsonify({
                'success': True,
                'items': gemini_result['items'],
                'full_description': gemini_result.get('full_description', ''),
                'count': len(gemini_result['items']),
                'source': 'gemini'
            })
        else:
            return jsonify({
                'success': True,
                'items': vision_items,
                'count': len(vision_items),
                'source': 'vision_api'
            })
    
    except Exception as e:
        # Clean up file if it exists
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
        if os.path.exists(filepath):
            os.remove(filepath)
        
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)

