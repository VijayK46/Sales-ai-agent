import os
import json
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import traceback

# --- 1. APP CONFIGURATION ---
app = Flask(__name__)

# Database Setup
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# --- 2. DATABASE MODELS ---
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
db.init_app(app)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    items = db.Column(db.Text, nullable=True)

with app.app_context():
    db.create_all()

# --- 3. AI CONFIGURATION ---
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- 4. ROUTES ---

@app.route("/")
def home():
    return """
    <html>
        <body style="font-family: Arial; text-align: center; padding: 50px; background-color: #f4f4f9;">
            <div style="background: white; padding: 30px; border-radius: 10px; display: inline-block; box-shadow: 0 4px 8px rgba(0,0,0,0.1);">
                <h1 style="color: #333;">🚀 Sales AI (Gemini 2.0 Flash Lite)</h1>
                <p>Upload Purchase Order (PDF) to Extract Data</p>
                <form action="/analyze-order" method="post" enctype="multipart/form-data">
                    <input type="file" name="file" accept=".pdf" required style="margin-bottom: 15px;">
                    <br>
                    <button type="submit" style="background-color: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px;">
                        ⚡ Analyze with Flash Lite
                    </button>
                </form>
            </div>
        </body>
    </html>
    """

@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    try:
        # Check Setup
        if not api_key:
            return "❌ Error: API Key is missing.", 500
        
        file = request.files.get("file")
        if not file or file.filename == "":
            return "❌ Error: No file uploaded.", 400

        # --- MODEL: GEMINI 2.0 FLASH LITE ---
        # Neenga ketta adhe model!
        model = genai.GenerativeModel("gemini-2.0-flash-lite")
        
        file_data = file.read()

        # Prompt
        prompt = """
        Extract the following details from this Purchase Order PDF:
        1. PO Number
        2. Vendor Name
        3. Total Amount
        4. List of items (name, quantity, price)
        
        Output must be strictly JSON format:
        {
            "po_number": "PO-XXX",
            "vendor_name": "Vendor Name",
            "total_amount": 1000.00,
            "items": [{"name": "Item 1", "qty": 10, "price": 100}]
        }
        """

        # Generate
        response = model.generate_content([
            {"mime_type": "application/pdf", "data": file_data},
            prompt
        ])
        
        # Clean JSON String
        text = response.text.replace("```json", "").replace("```", "").strip()
        
        # Parse JSON
        try:
            data = json.loads(text)
        except:
            return f"❌ AI Output format error. Raw Text: {text}", 500

        # Save to DB
        new_order = Order(
            po_number=data.get("po_number", "UNKNOWN"),
            vendor_name=data.get("vendor_name", "UNKNOWN"),
            total_amount=float(data.get("total_amount", 0.0)),
            items=json.dumps(data.get("items", []))
        )
        db.session.add(new_order)
        db.session.commit()

        # Export to Excel
        df = pd.DataFrame([data])
        output_file = "po_data.xlsx"
        df.to_excel(output_file, index=False)

        return send_file(output_file, as_attachment=True)

    except Exception as e:
        print(traceback.format_exc())
        return f"❌ Server Error: {str(e)}", 500

if __name__ == "__main__":
    # Render Port (Important!)
    app.run(host='0.0.0.0', port=10000)
