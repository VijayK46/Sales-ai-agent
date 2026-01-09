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
    # PUDHU TABLE (v3) - Payment Term add pannirukkom
    __tablename__ = 'orders_v3' 
    
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    payment_terms = db.Column(db.String(200), nullable=True) # New Column
    items = db.Column(db.Text, nullable=True) 

with app.app_context():
    db.create_all()

# --- 3. AI CONFIGURATION ---
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- 4. MASTER EXCEL GENERATOR (Specific Format) ---
def generate_master_excel():
    """
    Creates Excel with specific columns: 
    [Sl.No, Institute Name, Item Name, PO Number, Payment Term, Total Value, Remarks]
    """
    all_orders = Order.query.all()
    
    excel_data = []
    
    # Start Serial Number from 1
    sl_no = 1
    
    for order in all_orders:
        # 1. Prepare Item Names (Combine all into one string)
        try:
            items_list = json.loads(order.items) if order.items else []
        except:
            items_list = []
            
        if isinstance(items_list, list):
            # Example: "Item A, Item B, Item C"
            item_names_str = ", ".join([i.get('name', '') for i in items_list])
        else:
            item_names_str = ""

        # 2. Add Row to Excel Data
        excel_data.append({
            "Sl. No": sl_no,
            "Institute Name": order.vendor_name,
            "Item Name": item_names_str,
            "PO Number": order.po_number,
            "Payment Term": order.payment_terms if order.payment_terms else "N/A",
            "Total Value": order.total_amount,
            "Remarks": ""  # Empty column for user
        })
        
        sl_no += 1 # Increment counter

    # 3. Create DataFrame with Specific Column Order
    df = pd.DataFrame(excel_data)
    
    # Reorder columns explicitly to match user request
    columns_order = ["Sl. No", "Institute Name", "Item Name", "PO Number", "Payment Term", "Total Value", "Remarks"]
    
    # Handle case where DB might be empty
    if not df.empty:
        df = df[columns_order]
    else:
        df = pd.DataFrame(columns=columns_order)
    
    # 4. Save to Memory
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Tracker')
        
        # Auto-adjust column width (Optional polish)
        worksheet = writer.sheets['Tracker']
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value)) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = length + 2

    output.seek(0)
    return output

# --- 5. ROUTES ---

@app.route("/")
def home():
    return """
    <html>
        <body style="font-family: 'Segoe UI', sans-serif; text-align: center; padding: 50px; background-color: #f4f4f9;">
            <div style="background: white; padding: 40px; border-radius: 12px; display: inline-block; box-shadow: 0 4px 15px rgba(0,0,0,0.1);">
                <h1 style="color: #2c3e50;">🚀 Sales AI Tracker</h1>
                <p style="color: #666;">Upload PDF -> Add to Master List</p>
                
                <form action="/analyze-order" method="post" enctype="multipart/form-data" style="margin-top: 20px;">
                    <input type="file" name="file" accept=".pdf" required style="padding: 10px; border: 1px solid #ddd; border-radius: 5px;">
                    <br><br>
                    <button type="submit" style="background-color: #28a745; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px;">
                        ⚡ Add to Tracker
                    </button>
                </form>

                <hr style="margin: 30px 0; border: 0; border-top: 1px solid #eee;">
                
                <a href="/download-master" style="color: #007bff; text-decoration: none; font-weight: bold;">
                    📥 Download Master Excel Sheet
                </a>
            </div>
        </body>
    </html>
    """

@app.route("/analyze-order", methods=["POST"])
@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    try:
        if not api_key: return "❌ Error: API Key Missing", 500
        file = request.files.get("file")
        if not file or file.filename == "": return "❌ Error: No file", 400

        # 1. AI Extraction
        model = genai.GenerativeModel("gemini-flash-latest")
        file_data = file.read()
        
        # Payment Term Extraction Prompt
        prompt = """
        Extract from PDF:
        1. PO Number
        2. Vendor Name
        3. Total Amount
        4. Payment Terms (if not found return "N/A")
        5. Items (name, qty, price)
        
        Return JSON:
        {
            "po_number": "...",
            "vendor_name": "...",
            "total_amount": 0.0,
            "payment_terms": "...",
            "items": [{"name": "...", "qty": 1, "price": 100}]
        }
        """
        
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_data}, prompt])
        text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)

        # 2. Save to Database (No Excel Creation here)
        new_order = Order(
            po_number=data.get("po_number", "UNKNOWN"),
            vendor_name=data.get("vendor_name", "UNKNOWN"),
            total_amount=float(data.get("total_amount", 0.0) if data.get("total_amount") else 0.0),
            payment_terms=data.get("payment_terms", "N/A"),
            items=json.dumps(data.get("items", []))
        )
        db.session.add(new_order)
        db.session.commit()

        # 3. SUCCESS MESSAGE (Don't Download Excel)
        return """
        <html>
            <body style="font-family: 'Segoe UI', sans-serif; text-align: center; padding: 50px; background-color: #f4f4f9;">
                <div style="background: white; padding: 40px; border-radius: 12px; display: inline-block; box-shadow: 0 4px 15px rgba(0,0,0,0.1);">
                    <h1 style="color: #28a745; font-size: 50px; margin: 0;">✅</h1>
                    <h2 style="color: #333;">Saved Successfully!</h2>
                    <p style="color: #666;">PO Data has been added to the Master List.</p>
                    
                    <div style="margin-top: 30px;">
                        <a href="/" style="background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold;">
                            ⬅️ Upload Another PO
                        </a>
                    </div>
                    
                    <br><br>
                    <a href="/download-master" style="color: #666; text-decoration: underline;">
                        📥 Download Updated Master Excel
                    </a>
                </div>
            </body>
        </html>
        """

    except Exception as e:
        print(traceback.format_exc())
        return f"❌ Error: {str(e)}", 500
