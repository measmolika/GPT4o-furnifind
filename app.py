from flask import Flask, request, render_template, redirect, g
import os
import sqlite3
import base64
from openai import OpenAI
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.getenv("UPLOAD_FOLDER")
ALLOWED_EXTENSIONS = set(os.getenv("ALLOWED_EXTENSIONS").split(","))

client = OpenAI(
    api_key=os.getenv('OPENAI_API_KEY')
)

# Function to connect to the database
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect("furniture_album.db")
        g.db.row_factory = sqlite3.Row
    return g.db

# Function to close the database connection
@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# Ensure the table is created
with app.app_context():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS furniture (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT,
                    color TEXT,
                    material TEXT,
                    image_path TEXT
                )""")
    db.commit()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

def classify_image(image_path):
    base64_image = encode_image(image_path)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": [
                {"type": "text", "text": "Select the one main furniture in the picture and tell: type, color, material. For example `Bed, White, Wood`"},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{base64_image}"}
                }
            ]}
        ],
    )

    description = response.choices[0].message.content
    print(f"Description from model: {description}")

    try:
        type_, color, material = [item.strip() for item in description.split(',')]
        return type_, color, material
    except ValueError:
        print("Failed to parse GPT response:", description)
        return None, None, None

def add_image_to_database(image_path, furniture_type, color, material):
    db = get_db()
    db.execute(
        "INSERT INTO furniture (type, color, material, image_path) VALUES (?, ?, ?, ?)",
        (furniture_type, color, material, image_path),
    )
    db.commit()
    print(f"Added {furniture_type} - {color} - {material} to the database.")

@app.route('/')
def index():
    db = get_db()
    cursor = db.execute("SELECT type, color, material, image_path FROM furniture")
    all_records = cursor.fetchall()

    # Format the records for rendering
    all_results = [{"type": row["type"], "color": row["color"], "material": row["material"], "image_path": row["image_path"]} for row in all_records]

    return render_template('index.html', all_results=all_results)

@app.route('/upload', methods=['POST'])
def upload_files():
    if 'files' not in request.files:
        return redirect(request.url)
    
    files = request.files.getlist('files')
    results = []
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            type_, color, material = classify_image(filepath)
            add_image_to_database(str(filename), type_, color, material)

            results.append({
                "filename": filename,
                "type": type_,
                "color": color,
                "material": material
            })
    
    return redirect('/')

# Search furniture based on user query
@app.route('/search_furniture', methods=['POST'])
def search_furniture():
    query = request.form['query']
    prompt = f"Filter this query to determine the furniture type, color, and material: {query}. Return exactly the type, color, and material, separated by comma. If any attribute doesn't exist, just write None."

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": prompt},
        ],
    )

    # Assuming GPT-4 returns structured keywords like "Table, white, wood"
    parsed_response = response.choices[0].message.content.strip()
    search_params = {
        "type": None,
        "color": None,
        "material": None
    }
    
    parsed = parsed_response.split(",")
    search_params["type"] = parsed[0].strip() if parsed[0].strip() != "None" else None
    search_params["color"] = parsed[1].strip() if parsed[1].strip() != "None" else None
    search_params["material"] = parsed[2].strip() if parsed[2].strip() != "None" else None

    # Filter database based on GPT-4 parsed results
    db = get_db()
    query = "SELECT * FROM furniture WHERE 1=1"
    filters = []
    print(search_params)
    if search_params["type"] is not None:
        query += " AND type LIKE ?"
        filters.append(f"%{search_params['type']}%")
    if search_params["color"] is not None:
        query += " AND color LIKE ?"
        filters.append(f"%{search_params['color']}%")
    if search_params["material"] is not None:
        query += " AND material LIKE ?"
        filters.append(f"%{search_params['material']}%")

    cursor = db.execute(query, filters)
    filtered_results = cursor.fetchall()

    # Render the filtered results on the same page
    all_results = [{"type": row["type"], "color": row["color"], "material": row["material"], "image_path": row["image_path"]} for row in filtered_results]

    return render_template('index.html', all_results=all_results)

@app.route('/delete_all', methods=['POST'])
def delete_all():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM furniture")  # Delete all records from the 'furniture' table
    db.commit()
    db.close()
    return redirect('/')