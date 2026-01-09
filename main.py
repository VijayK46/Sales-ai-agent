import os
import json
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import traceback

# --- 1. ENGINE START (APP SETUP) ---
app = Flask(__name__)

# --- 2. DATABASE CONFIG ---
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# --- 3. DATABASE TABLES ---
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

# --- 4. AI SETUP ---
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- 5. ROUTES ---

@app.route("/")
def home():
    return """
    <html>
        <body style="font-family: Arial; text-align: center; padding: 50px;">
            <h1>🚀 Sales AI Agent (Gemini 2.0 Powered)</h1>
            <p>Upload Purchase Order (PDF)</p>
            <form action="/analyze-order" method="post" enctype="multipart/form-data">
                <input type="file" name="file" accept=".pdf" required>
                <br><br>
                <button type="submit" style="padding:10px; background-color: #4CAF50; color: white; border: none; cursor: pointer;">Analyze PDF</button>
            </form>
        </body>
    </html>
    """

@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    try:
        if not api_key:
            return "❌ API Key Missing", 500
            
        file = request.files.get("file")
        if not file or file.filename == "":
            return "❌ No file selected", 400

        # --- MODEL SELECTION (UPDATED!) ---
        # Using Gemini 2.0 Flash as per your list
        model = genai.GenerativeModel("gemini-2.0-flash")
        
        # Read File
        file_data = file.read()

        # Send to AI
        prompt = """
        Extract the following details from this PDF document:
        1. PO Number
        2. Vendor Name
        3. Total Amount
        4. List of items (name, quantity, price)
        
        Return the result strictly in JSON format like this:
        {
            "po_number": "PO-123",
            "vendor_name": "ABC Corp",
            "total_amount": 1000.50,
            "items": [{"name": "Item A", "qty": 10, "price": 100}]
        }
        """

        response = model.generate_content([
            {"mime_type": "application/pdf", "data": file_data},
            prompt
        ])
        
        # Clean JSON
        text = response.text.replace("```json", "").replace("```", "").strip()
        
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Sila neram AI text ah pesum, appo raw text ah kaatu
            return f"❌ JSON Error. Raw AI Response: {text}", 500

        # Save to DB
        new_order = Order(
            po_number=data.get("po_number", "N/A"),
            vendor_name=data.get("vendor_name", "N/A"),
            total_amount=float(data.get("total_amount", 0.0)),
            items=json.dumps(data.get("items", []))
        )
        db.session.add(new_order)
        db.session.commit()

        # Export Excel
        df = pd.DataFrame([data])
        df.to_excel("po_data.xlsx", index=False)
        return send_file("po_data.xlsx", as_attachment=True)

    except Exception as e:
        print(traceback.format_exc())
        return f"❌ ERROR: {str(e)}", 500

if __name__ == "__main__":
    # Port 10000 match panna maathirukken
    app.run(host='0.0.0.0', port=10000)
