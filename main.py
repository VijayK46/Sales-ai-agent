import os
import json
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import traceback

app = Flask(__name__)

# --- CONFIG ---
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

class Base(DeclarativeBase):
    pass
db = SQLAlchemy(model_class=Base)
db.init_app(app)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50))
    vendor_name = db.Column(db.String(100))
    total_amount = db.Column(db.Float)
    items = db.Column(db.Text)

with app.app_context():
    db.create_all()

# --- 🔍 SPECIAL DIAGNOSTIC ROUTE (CHECK MODELS) ---
@app.route("/check-models")
def check_models():
    try:
        if not api_key:
            return "❌ API KEY MISSING"
        
        # Google kitta 'List' kelunga
        model_list = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                model_list.append(m.name)
        
        return f"""
        <h1>🔍 Available Models</h1>
        <ul>
            {''.join([f'<li>{m}</li>' for m in model_list])}
        </ul>
        """
    except Exception as e:
        return f"❌ ERROR LISTING MODELS: {str(e)}"

# --- NORMAL ROUTES ---
@app.route("/")
def home():
    return """
    <html>
        <body>
            <h1>🚀 Sales AI Agent</h1>
            <p><a href="/check-models">👉 Click Here to Check Available Models</a></p>
            <form action="/analyze-order" method="post" enctype="multipart/form-data">
                <input type="file" name="file" accept=".pdf" required> <br><br>
                <button type="submit">Analyze</button>
            </form>
        </body>
    </html>
    """

@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    try:
        if "file" not in request.files: return "No file", 400
        file = request.files["file"]
        if file.filename == "": return "No file", 400

        # --- USE THE MODEL NAME YOU FIND IN /check-models ---
        # Ippo thaikku 'gemini-pro' veppom. List pathu mathalam.
        model = genai.GenerativeModel("gemini-pro") 
        
        file_data = file.read()
        prompt = "Extract PO Number, Vendor, Amount, Items as JSON."

        response = model.generate_content([
            {"mime_type": "application/pdf", "data": file_data},
            prompt
        ])
        
        # Simple extraction for testing
        return f"SUCCESS! Output: {response.text}"

    except Exception as e:
        return f"❌ ERROR: {str(e)}", 500

if __name__ == "__main__":
    app.run(debug=True)