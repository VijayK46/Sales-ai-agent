import os
import json
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, send_file, render_template_string
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import traceback
from io import BytesIO

# --- 1. APP CONFIGURATION ---
app = Flask(__name__)

# SQL Database Setup (Powerful & Fast)
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
    __tablename__ = 'orders_v3' 
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    payment_terms = db.Column(db.String(200), nullable=True)
    items = db.Column(db.Text, nullable=True) 

with app.app_context():
    db.create_all()

# --- 3. AI CONFIGURATION ---
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- 4. EXCEL GENERATOR (For Backup Download) ---
def generate_master_excel():
    all_orders = Order.query.all()
    excel_data = []
    sl_no = 1
    
    for order in all_orders:
        try:
            items_list = json.loads(order.items) if order.items else []
        except:
            items_list = []
        
        if isinstance(items_list, list):
            item_names_str = ", ".join([i.get('name', '') for i in items_list])
        else:
            item_names_str = ""

        excel_data.append({
            "Sl. No": sl_no,
            "Institute Name": order.vendor_name,
            "Item Name": item_names_str,
            "PO Number": order.po_number,
            "Payment Term": order.payment_terms if order.payment_terms else "N/A",
            "Total Value": order.total_amount,
            "Remarks": ""
        })
        sl_no += 1

    df = pd.DataFrame(excel_data)
    columns_order = ["Sl. No", "Institute Name", "Item Name", "PO Number", "Payment Term", "Total Value", "Remarks"]
    
    if not df.empty:
        df = df[columns_order]
    else:
        df = pd.DataFrame(columns=columns_order)
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Tracker')
    output.seek(0)
    return output

# --- 5. ROUTES ---

@app.route("/")
def home():
    # Fetch Data from SQL to display directly on screen
    orders = Order.query.order_by(Order.id.desc()).all() # Show latest first
    
    # Process data for HTML View
    display_data = []
    for order in orders:
        try:
            items = json.loads(order.items)
            item_str = ", ".join([i['name'] for i in items])
        except:
            item_str = ""
            
        display_data.append({
            "id": order.id,
            "vendor": order.vendor_name,
            "items": item_str,
            "po": order.po_number,
            "payment": order.payment_terms,
            "amount": order.total_amount
        })

    # HTML with Dashboard Table
    html_template = """
    <html>
        <head>
            <title>Sales AI Dashboard</title>
            <style>
                body { font-family: 'Segoe UI', sans-serif; background-color: #f4f7f6; padding: 20px; }
                .container { max-width: 1200px; margin: auto; }
                
                /* Upload Section */
                .upload-box { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center; margin-bottom: 30px; }
                .btn { padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; color: white; text-decoration: none; display: inline-block;}
                .btn-green { background: #28a745; }
                .btn-blue { background: #007bff; }
                
                /* Table Section */
                table { width: 100%; border-collapse: collapse; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
                th, td { padding: 15px; text-align: left; border-bottom: 1px solid #ddd; }
                th { background-color: #343a40; color: white; }
                tr:hover { background-color: #f1f1f1; }
                .amount { font-weight: bold; color: #28a745; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1 style="text-align:center; color:#333;">🚀 Sales AI Manager</h1>
                
                <div class="upload-box">
                    <h3>➕ Upload New PO</h3>
                    <form action="/analyze-order" method="post" enctype="multipart/form-data">
                        <input type="file" name="file" accept=".pdf" required>
                        <button type="submit" class="btn btn-green">Analyze & Add</button>
                    </form>
                    <br>
                    <a href="/download-master" class="btn btn-blue">📥 Download Excel Backup</a>
                </div>

                <h3>📊 Live Order List (From SQL Database)</h3>
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Institute Name</th>
                            <th>Item Name</th>
                            <th>PO Number</th>
                            <th>Payment Term</th>
                            <th>Total Value</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in data %}
                        <tr>
                            <td>{{ row.id }}</td>
                            <td>{{ row.vendor }}</td>
                            <td>{{ row.items }}</td>
                            <td>{{ row.po }}</td>
                            <td>{{ row.payment }}</td>
                            <td class="amount">{{ row.amount }}</td>
                        </tr>
                        {% else %}
                        <tr><td colspan="6" style="text-align:center;">No orders found yet. Upload a PDF!</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </body>
    </html>
    """
    return render_template_string(html_template, data=display_data)

@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    try:
        if not api_key: return "❌ Error: API Key Missing", 500
        file = request.files.get("file")
        if not file or file.filename == "": return "❌ Error: No file", 400

        # AI Extraction
        model = genai.GenerativeModel("gemini-flash-latest")
        file_data = file.read()
        
        prompt = """
        Extract: PO Number, Vendor Name, Total Amount, Payment Terms (or "N/A"), Items (name, qty, price).
        Return JSON: {"po_number": "...", "vendor_name": "...", "total_amount": 0.0, "payment_terms": "...", "items": [{"name": "...", "qty": 1, "price": 100}]}
        """
        
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_data}, prompt])
        text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)

        # Save to SQL
        new_order = Order(
            po_number=data.get("po_number", "UNKNOWN"),
            vendor_name=data.get("vendor_name", "UNKNOWN"),
            total_amount=float(data.get("total_amount", 0.0) if data.get("total_amount") else 0.0),
            payment_terms=data.get("payment_terms", "N/A"),
            items=json.dumps(data.get("items", []))
        )
        db.session.add(new_order)
        db.session.commit()

        # Redirect back to Dashboard (Automatically updates the table)
        return """<script>alert('✅ Added Successfully!'); window.location.href='/';</script>"""

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
            download_name="Sales_Tracker_Master.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return f"❌ Error: {str(e)}", 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
