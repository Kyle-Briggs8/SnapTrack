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
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

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
                        detected_items.append({
                            'description': entity.description,
                            'confidence': round(entity.score * 100, 2),
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
                    detected_items.append({
                        'description': obj.name,
                        'confidence': round(obj.score * 100, 2),
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
                    detected_items.append({
                        'description': label.description,
                        'confidence': round(label.score * 100, 2),
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
        prompt = """Analyze this food image and provide a detailed description. 
        
Please identify:
1. The main food items (e.g., "Hamburger with lettuce, tomato, and pickles")
2. All visible ingredients and components
3. Any side items visible (fries, drinks, etc.)
4. The type of cuisine or food category

Format your response as a JSON-like structure with:
- A main description of what the food is
- A list of all identified food items with their specific ingredients/components
- Any additional context (restaurant type, cuisine style, etc.)

Be specific and detailed. For example, instead of just "hamburger", say "hamburger with beef patty, lettuce, tomato, pickles, and special sauce on a sesame seed bun"."""
        
        # Prepare the image
        import PIL.Image
        image = PIL.Image.open(io.BytesIO(image_data))
        
        # Generate content
        response = model.generate_content([prompt, image])
        
        # Parse the response
        description_text = response.text
        
        # Extract structured information
        detected_items = []
        
        # Improved parsing: Look for numbered lists, bullet points, or structured content
        lines = description_text.split('\n')
        food_keywords = ['burger', 'sandwich', 'pizza', 'salad', 'fries', 'chicken', 'beef', 'pork', 
                        'fish', 'rice', 'pasta', 'bread', 'lettuce', 'tomato', 'onion', 'cheese',
                        'sauce', 'dressing', 'patty', 'bun', 'pickle', 'mayo', 'ketchup', 'mustard']
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip markdown headers and formatting
            if line.startswith('#') or line.startswith('*') and len(line) < 5:
                continue
            
            # Remove list markers (1., 2., -, *, etc.)
            cleaned_line = line
            if cleaned_line and cleaned_line[0] in ['1', '2', '3', '4', '5', '-', '*', '•']:
                cleaned_line = cleaned_line[1:].strip()
                if cleaned_line and cleaned_line[0] == '.':
                    cleaned_line = cleaned_line[1:].strip()
            
            # Check if line contains food-related content
            if cleaned_line and len(cleaned_line) > 15:
                line_lower = cleaned_line.lower()
                # If it mentions food keywords or seems descriptive
                if any(keyword in line_lower for keyword in food_keywords) or len(cleaned_line) > 30:
                    # Avoid duplicates
                    if cleaned_line.lower() not in [item['description'].lower() for item in detected_items]:
                        detected_items.append({
                            'description': cleaned_line,
                            'confidence': 90.0,
                            'type': 'gemini_detailed'
                        })
        
        # If we didn't get good structured items, split by sentences
        if len(detected_items) < 2:
            sentences = [s.strip() for s in description_text.replace('\n', ' ').split('.') 
                        if s.strip() and len(s.strip()) > 25]
            for sentence in sentences[:8]:  # Limit to top 8 sentences
                sentence = sentence.strip()
                if sentence and sentence.lower() not in [item['description'].lower() for item in detected_items]:
                    detected_items.append({
                        'description': sentence + '.',
                        'confidence': 90.0,
                        'type': 'gemini_description'
                    })
        
        # If still no items, use the full description as a single item
        if not detected_items:
            detected_items.append({
                'description': description_text[:200] + ('...' if len(description_text) > 200 else ''),
                'confidence': 90.0,
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

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and process with Gemini API (with Vision API fallback)"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Please upload an image (PNG, JPG, JPEG, GIF, WEBP)'}), 400
    
    try:
        # Save the uploaded file
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Try Gemini API first (for detailed descriptions)
        use_gemini = request.form.get('use_gemini', 'true').lower() == 'true'
        gemini_result = None
        vision_items = []
        
        if use_gemini and GEMINI_API_KEY:
            try:
                gemini_result = analyze_food_with_gemini(filepath)
            except Exception as gemini_error:
                # Fall back to Vision API if Gemini fails
                print(f"Gemini API error, falling back to Vision API: {str(gemini_error)}")
                vision_items = detect_food_items(filepath)
        else:
            # Use Vision API if Gemini is not available or disabled
            vision_items = detect_food_items(filepath)
        
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

