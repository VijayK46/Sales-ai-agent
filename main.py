import os
import json
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, render_template, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import traceback  # Idhu dhaan Error-a kandupudikkum spy!

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
    items = db.Column(db.Text, nullable=True)  # JSON string

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
    # Simple Upload Page
    return """
    <html>
        <head><title>Sales AI Agent</title></head>
        <body style="font-family: Arial; text-align: center; padding: 50px;">
            <h1>🚀 Sales AI Agent (Level 3)</h1>
            <p>Upload your Purchase Order (PDF) below:</p>
            <form action="/analyze-order" method="post" enctype="multipart/form-data">
                <input type="file" name="file" accept=".pdf" required>
                <br><br>
                <button type="submit" style="padding: 10px 20px; font-size: 16px; cursor: pointer;">Analyze & Save PO</button>
            </form>
        </body>
    </html>
    """

@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    try:
        # Check if file exists
        if "file" not in request.files:
            return "No file part", 400
        
        file = request.files["file"]
        if file.filename == "":
            return "No selected file", 400

        print("✅ 1. File Received: ", file.filename)

        # Step 1: Read PDF using Gemini
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        # Read file bytes
        file_data = file.read()
        
        prompt = """
        Extract the following details from the attached Purchase Order PDF:
        1. PO Number
        2. Vendor Name
        3. Total Amount
        4. List of items (name, quantity, price)
        
        Return the output purely as a valid JSON object. No markdown, no ```json.
        Format:
        {
            "po_number": "PO-123",
            "vendor_name": "ABC Corp",
            "total_amount": 1000.50,
            "items": [{"name": "Widget", "qty": 10, "price": 100}]
        }
        """

        print("✅ 2. Sending to Gemini AI...")
        response = model.generate_content([
            {"mime_type": "application/pdf", "data": file_data},
            prompt
        ])
        
        print("✅ 3. Gemini Responded!")
        
        # Step 2: Clean JSON
        raw_text = response.text.strip()
        # Remove markdown if Gemini adds it
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        data = json.loads(raw_text)
        print(f"✅ 4. Data Extracted: {data}")

        # Step 3: Save to Database
        new_order = Order(
            po_number=data.get("po_number", "UNKNOWN"),
            vendor_name=data.get("vendor_name", "UNKNOWN"),
            total_amount=float(data.get("total_amount", 0.0)),
            items=json.dumps(data.get("items", []))
        )
        
        db.session.add(new_order)
        db.session.commit()
        print("✅ 5. Saved to PostgreSQL Database!")

        # Step 4: Create Excel for Download
        df = pd.DataFrame([data])
        excel_filename = "po_data.xlsx"
        df.to_excel(excel_filename, index=False)

        return send_file(excel_filename, as_attachment=True)

   except Exception as e:
        # Error-a logs-la podu
        import traceback
        traceback.print_exc()
        
        # MUKKIYAM: Error-a Screen-laye kaattu!
        return f"❌ SERVER ERROR: {str(e)}", 500

if __name__ == "__main__":
    app.run(debug=True)

