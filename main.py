import os
import json
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import traceback
from io import BytesIO

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
    __tablename__ = 'orders_v2' 
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    items = db.Column(db.Text, nullable=True) # Stored as JSON string

with app.app_context():
    db.create_all()

# --- 3. AI CONFIGURATION ---
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- 4. MASTER EXCEL GENERATOR (Magic Part 🪄) ---
def generate_master_excel():
    """
    Fetches ALL data from the database and creates one Clean Master Excel.
    """
    # 1. Get ALL history from Database
    all_orders = Order.query.all()
    
    excel_data = []
    
    for order in all_orders:
        # DB Data
        po_num = order.po_number
        vendor = order.vendor_name
        total = order.total_amount
        
        # Parse Items (JSON String -> List)
        try:
            items_list = json.loads(order.items) if order.items else []
        except:
            items_list = []

        if not isinstance(items_list, list):
            items_list = []

        # 2. Add Rows (Explode Items)
        if items_list:
            for item in items_list:
                excel_data.append({
                    "PO Number": po_num,
                    "Vendor Name": vendor,
                    "Total Amount": total,
                    "Item Name": item.get("name", ""),
                    "Quantity": item.get("qty", ""),
                    "Unit Price": item.get("price", "")
                })
        else:
            # If no items, add single row
            excel_data.append({
                "PO Number": po_num,
                "Vendor Name": vendor,
                "Total Amount": total,
                "Item Name": "N/A", "Quantity": "", "Unit Price": ""
            })

    # 3. Create DataFrame
    df = pd.DataFrame(excel_data)
    
    # 4. Save to Memory (BytesIO)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Master Data')
    output.seek(0)
    
    return output

# --- 5. ROUTES ---

@app.route("/")
def home():
    return """
    <html>
        <body style="font-family: 'Segoe UI', sans-serif; text-align: center; padding: 50px; background-color: #f4f4f9;">
            <div style="background: white; padding: 40px; border-radius: 12px; display: inline-block; box-shadow: 0 4px 15px rgba(0,0,0,0.1);">
                <h1 style="color: #2c3e50;">🚀 Sales AI Agent</h1>
                <p style="color: #666;">Upload PDF -> Get Updated Master Excel</p>
                
                <form action="/analyze-order" method="post" enctype="multipart/form-data" style="margin-top: 20px;">
                    <input type="file" name="file" accept=".pdf" required style="padding: 10px; border: 1px solid #ddd; border-radius: 5px;">
                    <br><br>
                    <button type="submit" style="background-color: #28a745; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px;">
                        ⚡ Analyze & Update Excel
                    </button>
                </form>

                <hr style="margin: 30px 0; border: 0; border-top: 1px solid #eee;">
                
                <a href="/download-master" style="color: #007bff; text-decoration: none; font-weight: bold;">
                    📥 Download Existing Master File Only
                </a>
            </div>
        </body>
    </html>
    """

@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    try:
        if not api_key: return "❌ Error: API Key Missing", 500
        file = request.files.get("file")
        if not file or file.filename == "": return "❌ Error: No file", 400

        # 1. AI Extraction
        model = genai.GenerativeModel("gemini-flash-latest")
        file_data = file.read()
        
        prompt = """
        Extract PO Number, Vendor Name, Total Amount, Items (name, qty, price).
        Return JSON: {"po_number": "...", "vendor_name": "...", "total_amount": 0.0, "items": [{"name": "...", "qty": 1, "price": 100}]}
        """
        
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_data}, prompt])
        text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)

        # 2. Save NEW data to Database
        new_order = Order(
            po_number=data.get("po_number", "UNKNOWN"),
            vendor_name=data.get("vendor_name", "UNKNOWN"),
            total_amount=float(data.get("total_amount", 0.0) if data.get("total_amount") else 0.0),
            items=json.dumps(data.get("items", []))
        )
        db.session.add(new_order)
        db.session.commit()

        # 3. Generate & Return the FULL MASTER EXCEL (Old + New)
        master_excel = generate_master_excel()
        
        return send_file(
            master_excel,
            as_attachment=True,
            download_name="Master_PO_Database.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    except Exception as e:
        print(traceback.format_exc())
        return f"❌ Error: {str(e)}", 500

@app.route("/download-master")
def download_master():
    try:
        master_excel = generate_master_excel()
        return send_file(
            master_excel,
            as_attachment=True,
            download_name="Master_PO_Database.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return f"❌ Error: {str(e)}", 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
