import os
import json
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import traceback

# 1. App Setup
app = Flask(__name__)

# 2. Database Configuration (Auto-fix for Render)
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 3. Initialize Database
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
db.init_app(app)

# 4. Define Table (Model)
class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    items = db.Column(db.Text, nullable=True)

# 5. Create Tables
with app.app_context():
    db.create_all()

# 6. Configure Gemini AI
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- ROUTES ---

@app.route("/")
def home():
    return """
    <html>
        <head>
            <title>Sales AI Agent</title>
            <style>
                body { font-family: sans-serif; text-align: center; padding: 50px; }
                form { background: #f4f4f4; padding: 20px; display: inline-block; border-radius: 10px; }
                button { background: #28a745; color: white; padding: 10px 20px; border: none; cursor: pointer; font-size: 16px; }
                button:hover { background: #218838; }
            </style>
        </head>
        <body>
            <h1>🚀 Sales AI Agent (Final Version)</h1>
            <p>Upload Purchase Order (PDF) to Extract Data & Save to SQL</p>
            <form action="/analyze-order" method="post" enctype="multipart/form-data">
                <input type="file" name="file" accept=".pdf" required>
                <br><br>
                <button type="submit">Analyze & Download Excel</button>
            </form>
        </body>
    </html>
    """

@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    try:
        # Check API Key
        if not api_key:
            return "❌ Error: GENAI_API_KEY is missing in Render Environment!", 500

        # Check File
        if "file" not in request.files:
            return "❌ Error: No file part", 400
        
        file = request.files["file"]
        if file.filename == "":
            return "❌ Error: No selected file", 400

        # Step 1: Read PDF using Gemini
        model = genai.GenerativeModel("gemini-1.5-flash-latest")
        file_data = file.read()
        
        prompt = """
        Extract the following details from the PDF:
        1. PO Number
        2. Vendor Name
        3. Total Amount
        4. List of items (name, quantity, price)
        
        Return ONLY valid JSON. Format:
        {
            "po_number": "PO-123",
            "vendor_name": "ABC Corp",
            "total_amount": 1000.50,
            "items": [{"name": "Widget", "qty": 10, "price": 100}]
        }
        """

        response = model.generate_content([
            {"mime_type": "application/pdf", "data": file_data},
            prompt
        ])
        
        # Step 2: Clean JSON
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        data = json.loads(raw_text)

        # Step 3: Save to Database
        new_order = Order(
            po_number=data.get("po_number", "UNKNOWN"),
            vendor_name=data.get("vendor_name", "UNKNOWN"),
            total_amount=float(data.get("total_amount", 0.0)),
            items=json.dumps(data.get("items", []))
        )
        
        db.session.add(new_order)
        db.session.commit()

        # Step 4: Create Excel (Uses openpyxl)
        df = pd.DataFrame([data])
        excel_filename = "po_data.xlsx"
        df.to_excel(excel_filename, index=False)

        return send_file(excel_filename, as_attachment=True)

    except Exception as e:
        # 🚨 IDHU DHAAN UNGALUKKU SCREEN LA ERROR KAATTUM 🚨
        error_message = f"❌ BIG ERROR: {str(e)}"
        print(traceback.format_exc())
        return error_message, 500

if __name__ == "__main__":
    app.run(debug=True)

