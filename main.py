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
    __tablename__ = 'orders_v4' 
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    currency = db.Column(db.String(10), nullable=True)
    total_amount = db.Column(db.Float, nullable=False)
    payment_terms = db.Column(db.String(200), nullable=True)
    items = db.Column(db.Text, nullable=True) 

with app.app_context():
    db.create_all()

# --- 3. AI CONFIGURATION ---
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- 4. LOGIC: HIGH VALUE ITEM ---
def get_high_value_item_name(items_json):
    try:
        if not items_json: return "No Items"
        items = json.loads(items_json)
        if not isinstance(items, list) or len(items) == 0: return "No Items"

        best_item = None
        max_val = -1

        for item in items:
            try:
                price = float(str(item.get('price', 0)).replace(',', '').replace('$', ''))
                qty = float(str(item.get('qty', 1)).replace(',', ''))
                total_val = price * qty
                if total_val > max_val:
                    max_val = total_val
                    best_item = item.get('name', 'Unknown')
            except: continue
        
        return best_item if best_item else "Unknown Item"
    except: return "Error Parsing"

# --- 5. EXCEL GENERATOR ---
def generate_master_excel():
    all_orders = Order.query.all()
    excel_data = []
    sl_no = 1
    
    for order in all_orders:
        item_name = get_high_value_item_name(order.items)
        currency_symbol = order.currency if order.currency else ""
        formatted_value = f"{currency_symbol} {order.total_amount}"

        excel_data.append({
            "Sl. No": sl_no,
            "Institute Name": order.vendor_name,
            "Main Item (High Value)": item_name,
            "PO Number": order.po_number,
            "Payment Term": order.payment_terms if order.payment_terms else "N/A",
            "Total Value": formatted_value,
            "Remarks": ""
        })
        sl_no += 1

    df = pd.DataFrame(excel_data)
    columns_order = ["Sl. No", "Institute Name", "Main Item (High Value)", "PO Number", "Payment Term", "Total Value", "Remarks"]
    
    if not df.empty: df = df[columns_order]
    else: df = pd.DataFrame(columns=columns_order)
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Tracker')
        worksheet = writer.sheets['Tracker']
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value)) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = length + 2
    output.seek(0)
    return output

# --- 6. ROUTES ---

@app.route("/")
def home():
    orders = Order.query.order_by(Order.id.desc()).all()
    
    display_data = []
    for order in orders:
        main_item_name = get_high_value_item_name(order.items)
        curr = order.currency if order.currency else ""
        
        display_data.append({
            "id": order.id,
            "vendor": order.vendor_name,
            "items": main_item_name,
            "po": order.po_number,
            "payment": order.payment_terms,
            "amount": f"{curr} {order.total_amount}"
        })

    html_template = """
    <html>
        <head>
            <title>Sales AI Dashboard</title>
            <style>
                body { font-family: 'Segoe UI', sans-serif; background-color: #f8f9fa; padding: 20px; }
                .container { max-width: 1200px; margin: auto; }
                .card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 20px; }
                table { width: 100%; border-collapse: collapse; margin-top: 10px; }
                th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #e9ecef; }
                th { background-color: #343a40; color: white; }
                .high-value { color: #d63384; font-weight: bold; }
                .amount { color: green; font-weight: bold; }
                .btn { padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; color: white; text-decoration: none; display: inline-block; }
                .btn-green { background: #28a745; }
                .btn-blue { background: #007bff; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1 style="text-align:center;">🚀 Sales AI Manager</h1>
                
                <div class="card" style="text-align:center;">
                    <h3>➕ Upload New PO</h3>
                    <form action="/analyze-order" method="post" enctype="multipart/form-data">
                        <input type="file" name="file" accept=".pdf" required>
                        <br><br>
                        <button type="submit" class="btn btn-green">Analyze & Add</button>
                    </form>
                    <br>
                    <a href="/download-master" class="btn btn-blue">📥 Download Excel Report</a>
                </div>

                <div class="card">
                    <h3>📊 Live Database (Only Valid POs)</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Sl. No</th>
                                <th>Institute Name</th>
                                <th>High Value Item</th>
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
                                <td class="high-value">{{ row.items }}</td>
                                <td>{{ row.po }}</td>
                                <td>{{ row.payment }}</td>
                                <td class="amount">{{ row.amount }}</td>
                            </tr>
                            {% else %}
                            <tr><td colspan="6" style="text-align:center;">No Data Found. Upload a PDF!</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
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

        model = genai.GenerativeModel("gemini-flash-latest")
        file_data = file.read()
        
        # --- THE WATCHMAN PROMPT 👮 ---
        prompt = """
        Analyze this document. 
        Step 1: Is this a Purchase Order (PO) or an Order Confirmation? Answer TRUE or FALSE.
        Step 2: If TRUE, extract details.
        
        Return STRICT JSON:
        {
            "is_purchase_order": true,  <-- THIS IS KEY
            "po_number": "...", 
            "vendor_name": "...", 
            "currency": "...",
            "total_amount": 0.0, 
            "payment_terms": "...",
            "items": [{"name": "...", "qty": 1, "price": 100}]
        }
        
        If it is NOT a PO (e.g., Invoice, Quote, Email text), return:
        {"is_purchase_order": false}
        """
        
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_data}, prompt])
        text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)

        # --- THE GATEKEEPER CHECK 🛑 ---
        if data.get("is_purchase_order") != True:
            # Reject the file!
            return """
            <script>
                alert('⚠️ Valid PO Illa! (Skipped)\\nAI says this document is NOT a Purchase Order.'); 
                window.location.href='/';
            </script>
            """

        # Only Save if "is_purchase_order" is TRUE
        new_order = Order(
            po_number=data.get("po_number", "UNKNOWN"),
            vendor_name=data.get("vendor_name", "UNKNOWN"),
            currency=data.get("currency", ""),
            total_amount=float(data.get("total_amount", 0.0) if data.get("total_amount") else 0.0),
            payment_terms=data.get("payment_terms", "N/A"),
            items=json.dumps(data.get("items", []))
        )
        db.session.add(new_order)
        db.session.commit()

        return """<script>alert('✅ Valid PO Added Successfully!'); window.location.href='/';</script>"""

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
