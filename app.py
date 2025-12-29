import os
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
from google.cloud import vision
import io

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

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

@app.route('/')
def index():
    """Render the main page"""
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and process with Vision API"""
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
        
        # Detect food items using Vision API
        food_items = detect_food_items(filepath)
        
        # Clean up uploaded file
        os.remove(filepath)
        
        return jsonify({
            'success': True,
            'items': food_items,
            'count': len(food_items),
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

