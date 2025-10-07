from flask import Flask, render_template, send_file, Response, request, redirect, url_for, session
import requests
import base64
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import time
from datetime import datetime, timedelta
import os
import threading
import logging
import shutil
from functools import wraps
import json
from collections import defaultdict
import random

global last_cleanup_time

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this to a random secret key

# Robot configuration
ROBOTS = {
    "x01": "http://10.158.17.140:8000/color_image_base64",
    "x02": "http://10.158.17.43:8000/color_image_base64",
    "x03": "http://10.158.17.69:8000/color_image_base64",
    "x04": "http://10.158.17.38:8000/color_image_base64"
}

# Global variable to store the latest combined image
latest_combined_image = None
latest_combined_image_path = None
last_cleanup_time = None

# Error tracking
ERROR_COUNTER_FILE = "error_counts.json"
error_counts = defaultdict(lambda: defaultdict(int))

def load_error_counts():
    """Load error counts from file"""
    global error_counts
    try:
        if os.path.exists(ERROR_COUNTER_FILE):
            with open(ERROR_COUNTER_FILE, 'r') as f:
                # Convert loaded JSON to nested defaultdict
                loaded_data = json.load(f)
                for robot_id, dates in loaded_data.items():
                    for date, count in dates.items():
                        error_counts[robot_id][date] = count
            logger.info("Loaded error counts from file")
    except Exception as e:
        logger.error(f"Error loading error counts: {e}")

def save_error_counts():
    """Save error counts to file"""
    try:
        # Convert defaultdict to regular dict for JSON serialization
        save_data = {robot_id: dict(dates) for robot_id, dates in error_counts.items()}
        with open(ERROR_COUNTER_FILE, 'w') as f:
            json.dump(save_data, f)
    except Exception as e:
        logger.error(f"Error saving error counts: {e}")

def cleanup_old_error_counts():
    """Remove error counts older than 7 days"""
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    
    for robot_id in list(error_counts.keys()):
        for date in list(error_counts[robot_id].keys()):
            if date < seven_days_ago:
                del error_counts[robot_id][date]
        
        # Remove robot entry if no dates left
        if not error_counts[robot_id]:
            del error_counts[robot_id]
    
    save_error_counts()

def record_error(robot_id):
    """Record an error for a robot"""
    today = datetime.now().strftime("%Y-%m-%d")
    error_counts[robot_id][today] += 1
    save_error_counts()

def get_error_count(robot_id):
    """Get the total error count for a robot over the last 7 days"""
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    total = 0
    
    if robot_id in error_counts:
        for date, count in error_counts[robot_id].items():
            if date >= seven_days_ago:
                total += count
    
    return total

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def load_users():
    """Load users from the users.txt file"""
    users = {}
    try:
        if os.path.exists('users.txt'):
            with open('users.txt', 'r') as f:
                for line in f:
                    if ':' in line:
                        username, password = line.strip().split(':', 1)
                        users[username] = password
        else:
            # Create a default users file if it doesn't exist
            with open('users.txt', 'w') as f:
                f.write('admin:admin\n')
                f.write('user:password\n')
            users = {'admin': 'admin', 'user': 'password'}
            logger.info("Created default users.txt file")
    except Exception as e:
        logger.error(f"Error loading users: {e}")
        # Fallback to default users if there's an error
        users = {'admin': 'admin', 'user': 'password'}
    
    return users

def delete_old_folders():
    """Delete folders older than 7 days to save disk space"""
    global last_cleanup_time
    print("Delete folders older than 7 days to save disk space : ",last_cleanup_time)

    current_time = datetime.now()
    
    try:
        for item in os.listdir('.'):
            if os.path.isdir(item) and len(item) == 8 and item.isdigit():
                # Check if the folder name is a date (YYYYMMDD format)
                try:
                    folder_date = datetime.strptime(item, "%Y%m%d")
                    # Delete if older than 7 days
                    if (current_time - folder_date).days > 7:
                        shutil.rmtree(item)
                        logger.info(f"Deleted old folder: {item}")
                        last_cleanup_time = current_time
                except ValueError:
                    # Not a valid date folder, skip
                    continue
        
        #last_cleanup_time = current_time
        logger.info("Cleanup of old folders completed")
        
    except Exception as e:
        logger.error(f"Error during folder cleanup: {e}")

def get_robot_image_old(robot_id, url): #### to be confirmed to delete
    """Fetch and process image from a robot"""
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        # Try to find the base64 data
        base64_data = None
        possible_keys = ['base64', 'image', 'data', 'color_image', 'image_base64']
        
        for key in possible_keys:
            if key in data:
                base64_data = data[key]
                break
        
        if not base64_data:
            # If none of the common keys work, try to find any string value that might be base64
            for key, value in data.items():
                if isinstance(value, str) and len(value) > 100:
                    base64_data = value
                    break
        
        if not base64_data:
            raise ValueError("No base64 data found in response")
        
        # Remove data URL prefix if present
        if "," in base64_data:
            base64_data = base64_data.split(",", 1)[1]
        
        # Decode base64 to image
        image_bytes = base64.b64decode(base64_data)
        image = Image.open(BytesIO(image_bytes))
        
        # Get error count for this robot
        error_count = get_error_count(robot_id)
        
        # Add timestamp, robot ID, and error count to image
        draw = ImageDraw.Draw(image)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
        text = f"{robot_id.upper()}_{timestamp} (Errors: {error_count})"
        
        # Create a black background rectangle for text
        try:
            # Try to use a default font
            font = ImageFont.load_default()
        except:
            font = None
            
        bbox = draw.textbbox((0, 0), text, font=font) if font else draw.textbbox((0, 0), text)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        draw.rectangle([5, 5, 15 + text_width, 15 + text_height], fill="black")
        
        # Add text
        if font:
            draw.text((10, 10), text, fill="white", font=font)
        else:
            draw.text((10, 10), text, fill="white")
        
        # Create daily directory if it doesn't exist
        date_dir = datetime.now().strftime("%Y%m%d")
        os.makedirs(date_dir, exist_ok=True)
        
        # Save individual image
        filename = f"{date_dir}/{robot_id.upper()}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
        image.save(filename)
        
        # Clean up old error counts occasionally
        if random.random() < 0.01:  # ~1% chance on each request
            cleanup_old_error_counts()
        
        return image, None
        
    except Exception as e:
        logger.error(f"Error getting image from {robot_id}: {e}")
        # Record the error
        record_error(robot_id)
        
        # Get updated error count
        error_count = get_error_count(robot_id)
        
        # Create a blank image with error message
        error_img = Image.new('RGB', (640, 480), color='black')
        draw = ImageDraw.Draw(error_img)
        try:
            font = ImageFont.load_default()
            draw.text((50, 200), f"Error: {robot_id.upper()} - {str(e)}", fill="red", font=font)
            draw.text((50, 220), f"Total errors (7 days): {error_count}", fill="red", font=font)
        except:
            draw.text((50, 200), f"Error: {robot_id.upper()} - {str(e)}", fill="red")
            draw.text((50, 220), f"Total errors (7 days): {error_count}", fill="red")
        return error_img, str(e)

def get_robot_image(robot_id, url):
    """Fetch and process image from a robot"""
    # Track previous state for this robot
    previous_state = getattr(get_robot_image, "previous_states", {}).get(robot_id, "success")
    
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        # Try to find the base64 data
        base64_data = None
        possible_keys = ['base64', 'image', 'data', 'color_image', 'image_base64']
        
        for key in possible_keys:
            if key in data:
                base64_data = data[key]
                break
        
        if not base64_data:
            # If none of the common keys work, try to find any string value that might be base64
            for key, value in data.items():
                if isinstance(value, str) and len(value) > 100:
                    base64_data = value
                    break
        
        if not base64_data:
            raise ValueError("No base64 data found in response")
        
        # Remove data URL prefix if present
        if "," in base64_data:
            base64_data = base64_data.split(",", 1)[1]
        
        # Decode base64 to image
        image_bytes = base64.b64decode(base64_data)
        image = Image.open(BytesIO(image_bytes))
        
        # Get error count for this robot
        error_count = get_error_count(robot_id)
        
        # Add timestamp, robot ID, and error count to image
        draw = ImageDraw.Draw(image)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
        text = f"{robot_id.upper()}_{timestamp} (Errors: {error_count})"
        
        # Create a black background rectangle for text
        try:
            # Try to use a default font
            font = ImageFont.load_default()
        except:
            font = None
            
        bbox = draw.textbbox((0, 0), text, font=font) if font else draw.textbbox((0, 0), text)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        draw.rectangle([5, 5, 15 + text_width, 15 + text_height], fill="black")
        
        # Add text
        if font:
            draw.text((10, 10), text, fill="white", font=font)
        else:
            draw.text((10, 10), text, fill="white")
        
        # Create daily directory if it doesn't exist
        date_dir = datetime.now().strftime("%Y%m%d")
        os.makedirs(date_dir, exist_ok=True)
        
        # Save individual image
        filename = f"{date_dir}/{robot_id.upper()}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
        image.save(filename)
        
        # Update previous state to success
        if not hasattr(get_robot_image, "previous_states"):
            get_robot_image.previous_states = {}
        get_robot_image.previous_states[robot_id] = "success"
        
        # Clean up old error counts occasionally
        if random.random() < 0.01:  # ~1% chance on each request
            cleanup_old_error_counts()
        
        return image, None
        
    except Exception as e:
        logger.error(f"Error getting image from {robot_id}: {e}")
        
        # Only record error if previous state was success
        if previous_state == "success":
            record_error(robot_id)
        
        # Update previous state to error
        if not hasattr(get_robot_image, "previous_states"):
            get_robot_image.previous_states = {}
        get_robot_image.previous_states[robot_id] = "error"
        
        # Get updated error count
        error_count = get_error_count(robot_id)
        
        # Create a blank image with error message
        error_img = Image.new('RGB', (640, 480), color='black')
        draw = ImageDraw.Draw(error_img)
        try:
            font = ImageFont.load_default()
            draw.text((50, 200), f"Error: {robot_id.upper()} - {str(e)}", fill="red", font=font)
            draw.text((50, 220), f"Total errors (7 days): {error_count}", fill="red", font=font)
        except:
            draw.text((50, 200), f"Error: {robot_id.upper()} - {str(e)}", fill="red")
            draw.text((50, 220), f"Total errors (7 days): {error_count}", fill="red")
        return error_img, str(e)
    
def create_combined_image():
    """Create a combined 2x2 image from all robot images"""
    global latest_combined_image, latest_combined_image_path
    
    # Clean up old folders (runs once per day)
    delete_old_folders()
    
    images = {}
    errors = {}
    
    # Get images from all robots
    for robot_id, url in ROBOTS.items():
        images[robot_id], errors[robot_id] = get_robot_image(robot_id, url)
    
    # Create a 2x2 grid
    width, height = 640, 480  # Default size
    if images and list(images.values())[0]:
        width, height = list(images.values())[0].size
    
    combined = Image.new('RGB', (width * 2, height * 2))
    
    # Arrange images in grid
    positions = [
        (0, 0),     # x01 - top left
        (width, 0),  # x02 - top right
        (0, height), # x03 - bottom left
        (width, height)  # x04 - bottom right
    ]
    
    for i, (robot_id, pos) in enumerate(zip(ROBOTS.keys(), positions)):
        if robot_id in images and images[robot_id]:
            combined.paste(images[robot_id], pos)
    
    # Save combined image
    date_dir = datetime.now().strftime("%Y%m%d")
    os.makedirs(date_dir, exist_ok=True)
    filename = f"{date_dir}/combined_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
    combined.save(filename)
    
    # Update global variables
    latest_combined_image = combined
    latest_combined_image_path = filename
    
    logger.info(f"Updated combined image at {datetime.now().strftime('%H:%M:%S')}")

def update_images_periodically():
    """Periodically update images every 10 seconds"""
    while True:
        try:
            create_combined_image()
        except Exception as e:
            logger.error(f"Error in update thread: {e}")
        time.sleep(10)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        users = load_users()
        
        if username in users and users[username] == password:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html', username=session.get('username'))

@app.route('/latest_image')
@login_required
def get_latest_image():
    """Serve the latest combined image"""
    try:
        if latest_combined_image_path and os.path.exists(latest_combined_image_path):
            return send_file(latest_combined_image_path, mimetype='image/jpeg')
        else:
            # Return a placeholder if no image is available
            img = Image.new('RGB', (640, 480), color='gray')
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.load_default()
                draw.text((200, 200), "No image available yet", fill="black", font=font)
            except:
                draw.text((200, 200), "No image available yet", fill="black")
            
            img_io = BytesIO()
            img.save(img_io, 'JPEG')
            img_io.seek(0)
            
            return send_file(img_io, mimetype='image/jpeg')
    except Exception as e:
        logger.error(f"Error serving image: {e}")
        return str(e), 500

@app.route('/error_stats')
@login_required
def error_stats():
    """Display error statistics for all robots"""
    stats = {}
    for robot_id in ROBOTS.keys():
        stats[robot_id] = get_error_count(robot_id)
    
    return render_template('error_stats.html', 
                          stats=stats, 
                          username=session.get('username'))

# Create templates if they don't exist
if not os.path.exists('templates'):
    os.makedirs('templates')

# Create login template
with open('templates/login.html', 'w', encoding='utf-8') as f:
    f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Robot Camera Monitor</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f0f0f0;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .login-container {
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
            width: 300px;
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 20px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
            box-sizing: border-box;
        }
        button {
            width: 100%;
            padding: 10px;
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        button:hover {
            background-color: #45a049;
        }
        .error {
            color: red;
            text-align: center;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>Robot Camera Monitor</h1>
        <form method="POST">
            <div class="form-group">
                <label for="username">Username:</label>
                <input type="text" id="username" name="username" required>
            </div>
            <div class="form-group">
                <label for="password">Password:</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit">Login</button>
            {% if error %}
                <p class="error">{{ error }}</p>
            {% endif %}
        </form>
    </div>
</body>
</html>
''')

# Create index template
with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Robot Camera Monitor</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f0f0f0;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        .container {
            max-width: 1300px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        }
        h1 {
            text-align: center;
            color: #333;
            margin: 0;
        }
        .image-container {
            text-align: center;
            margin: 20px 0;
        }
        #robotImage {
            max-width: 100%;
            border: 1px solid #ddd;
            border-radius: 4px;
        }
        .last-updated {
            text-align: center;
            color: #666;
            margin-bottom: 20px;
        }
        .status {
            text-align: center;
            margin: 10px 0;
            padding: 10px;
            border-radius: 4px;
        }
        .status-info {
            background-color: #e7f3fe;
            border-left: 6px solid #2196F3;
        }
        .user-info {
            text-align: right;
            color: #666;
        }
        .logout-btn {
            background-color: #f44336;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
        }
        .logout-btn:hover {
            background-color: #d32f2f;
        }
        .nav {
            margin-bottom: 20px;
            text-align: center;
        }
        .nav a {
            margin: 0 10px;
            text-decoration: none;
            color: #2196F3;
        }
        .nav a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Robot Camera Monitoring System</h1>
        <div class="user-info">
            Welcome, {{ username }} | <a href="{{ url_for('logout') }}" class="logout-btn">Logout</a>
        </div>
    </div>
    
    <div class="nav">
        <a href="{{ url_for('index') }}">Live View</a>
        <a href="{{ url_for('error_stats') }}">Error Statistics</a>
    </div>
    
    <div class="container">
        <div class="status status-info">
            <p>Images are automatically saved in dated folders and old folders (7+ days) are automatically deleted</p>
            <p>Error counts show the number of connection failures for each robot over the last 7 days</p>
        </div>
        <div class="last-updated" id="lastUpdated">
            Last updated: <span id="updateTime">Loading...</span>
        </div>
        <div class="image-container">
            <img id="robotImage" src="{{ url_for('get_latest_image') }}" alt="Robot Camera Feed">
        </div>
    </div>

    <script>
        function updateImage() {
            const img = document.getElementById('robotImage');
            // Add a timestamp to the URL to prevent caching
            img.src = "{{ url_for('get_latest_image') }}?" + new Date().getTime();
            
            // Update the timestamp display
            document.getElementById('updateTime').textContent = new Date().toLocaleTimeString();
        }
        
        // Update the image every 10 seconds
        setInterval(updateImage, 10000);
        
        // Initial update
        updateImage();
    </script>
</body>
</html>
''')

# Replace the error_stats.html template creation section with this code:

# Create error statistics template
with open('templates/error_stats.html', 'w', encoding='utf-8') as f:
    f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error Statistics - Robot Camera Monitor</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f0f0f0;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        }
        h1 {
            text-align: center;
            color: #333;
            margin: 0;
        }
        .user-info {
            text-align: right;
            color: #666;
        }
        .logout-btn {
            background-color: #f44336;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
        }
        .logout-btn:hover {
            background-color: #d32f2f;
        }
        .nav {
            margin-bottom: 20px;
            text-align: center;
        }
        .nav a {
            margin: 0 10px;
            text-decoration: none;
            color: #2196F3;
        }
        .nav a:hover {
            text-decoration: underline;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        th {
            background-color: #f2f2f2;
        }
        .high-error {
            color: #f44336;
            font-weight: bold;
        }
        .medium-error {
            color: #ff9800;
        }
        .low-error {
            color: #4CAF50;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Robot Error Statistics</h1>
        <div class="user-info">
            Welcome, {{ username }} | <a href="{{ url_for('logout') }}" class="logout-btn">Logout</a>
        </div>
    </div>
    
    <div class="nav">
        <a href="{{ url_for('index') }}">Live View</a>
        <a href="{{ url_for('error_stats') }}">Error Statistics</a>
    </div>
    
    <div class="container">
        <h2>Connection Errors (Last 7 Days)</h2>
        <table>
            <thead>
                <tr>
                    <th>Robot ID</th>
                    <th>Error Count</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {% for robot_id, count in stats.items() %}
                <tr>
                    <td>{{ robot_id.upper() }}</td>
                    <td class="{% if count > 20 %}high-error{% elif count > 5 %}medium-error{% else %}low-error{% endif %}">
                        {{ count }}
                    </td>
                    <td>
                        {% if count == 0 %}
                            [GOOD] No errors
                        {% elif count <= 5 %}
                            [WARNING] Low errors
                        {% elif count <= 20 %}
                            [WARNING] Moderate errors
                        {% else %}
                            [CRITICAL] High errors
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        
        <div style="margin-top: 30px;">
            <h3>About Error Tracking</h3>
            <p>This system tracks connection errors to each robot over the last 7 days.</p>
            <p>Error counts persist across application restarts and are saved to a file.</p>
            <p>Errors are automatically cleaned up after 7 days to prevent unlimited growth.</p>
        </div>
    </div>
</body>
</html>
''')

if __name__ == '__main__':
    # Load error counts from previous runs
    load_error_counts()
    
    # Create initial combined image
    create_combined_image()
    
    # Start the image update thread
    update_thread = threading.Thread(target=update_images_periodically, daemon=True)
    update_thread.start()
    
    # Run the Flask app
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)