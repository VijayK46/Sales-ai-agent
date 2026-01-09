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
    # NEW TABLE v4 (Added Currency Column)
    __tablename__ = 'orders_v4' 
    
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    currency = db.Column(db.String(10), nullable=True) # New: USD, INR, EUR
    total_amount = db.Column(db.Float, nullable=False)
    payment_terms = db.Column(db.String(200), nullable=True)
    items = db.Column(db.Text, nullable=True) 

with app.app_context():
    db.create_all()

# --- 3. AI CONFIGURATION ---
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- 4. EXCEL GENERATOR (With Currency) ---
def generate_master_excel():
    all_orders = Order.query.all()
    excel_data = []
    sl_no = 1
    
    for order in all_orders:
        # 1. Clean Item Names
        try:
            items_list = json.loads(order.items) if order.items else []
        except:
            items_list = []
        
        # Join items with comma (Ex: "Item A, Item B")
        if isinstance(items_list, list):
            item_names_str = ", ".join([str(i.get('name', '')).strip() for i in items_list])
        else:
            item_names_str = ""

        # 2. Format Amount with Currency (Ex: "USD 5000")
        currency_symbol = order.currency if order.currency else ""
        formatted_value = f"{currency_symbol} {order.total_amount}"

        excel_data.append({
            "Sl. No": sl_no,
            "Institute Name": order.vendor_name,
            "Item Name": item_names_str,
            "PO Number": order.po_number,
            "Payment Term": order.payment_terms if order.payment_terms else "N/A",
            "Total Value": formatted_value, # Now includes Currency
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
        # Auto-adjust width
        worksheet = writer.sheets['Tracker']
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value)) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = length + 2

    output.seek(0)
    return output

# --- 5. ROUTES ---

@app.route("/")
def home():
    orders = Order.query.order_by(Order.id.desc()).all()
    
    display_data = []
    for order in orders:
        try:
            items = json.loads(order.items)
            # Take only first 2 items for display summary
            item_names = [i['name'] for i in items]
            item_str = ", ".join(item_names[:2]) + ("..." if len(item_names) > 2 else "")
        except:
            item_str = ""
        
        curr = order.currency if order.currency else ""
            
        display_data.append({
            "id": order.id,
            "vendor": order.vendor_name,
            "items": item_str,
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
                h1 { color: #2c3e50; text-align: center; }
                
                table { width: 100%; border-collapse: collapse; margin-top: 10px; }
                th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #e9ecef; }
                th { background-color: #343a40; color: white; border-radius: 5px 5px 0 0; }
                tr:hover { background-color: #f1f1f1; }
                
                .btn { padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; color: white; text-decoration: none; font-weight: bold; display: inline-block; }
                .btn-green { background: #28a745; }
                .btn-blue { background: #007bff; }
                input[type=file] { border: 1px solid #ced4da; padding: 8px; border-radius: 5px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🚀 Sales AI Manager (Currency Support)</h1>
                
                <div class="card" style="text-align: center;">
                    <h3>➕ Upload New PO</h3>
                    <form action="/analyze-order" method="post" enctype="multipart/form-data">
                        <input type="file" name="file" accept=".pdf" required>
                        <button type="submit" class="btn btn-green">Analyze & Add</button>
                    </form>
                    <br>
                    <a href="/download-master" class="btn btn-blue">📥 Download Full Excel Report</a>
                </div>

                <div class="card">
                    <h3>📊 Live Database View</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Sl. No</th>
                                <th>Institute Name</th>
                                <th>Item Summary</th>
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
                                <td style="color: green; font-weight: bold;">{{ row.amount }}</td>
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
        
        # UPDATED PROMPT: Asks for Currency explicitly
        prompt = """
        Extract the following details from the PDF:
        1. PO Number
        2. Vendor Name (Institute Name)
        3. Currency (e.g., USD, EUR, INR, GBP). If symbol is used ($), convert to code (USD).
        4. Total Amount (Just the number)
        5. Payment Terms (e.g., "Net 30", "100% Advance"). If not found, "N/A".
        6. Items (List of item names only)
        
        Return JSON format:
        {
            "po_number": "PO-123",
            "vendor_name": "ABC Corp",
            "currency": "USD",
            "total_amount": 5000.00,
            "payment_terms": "Net 30",
            "items": [{"name": "Item A"}, {"name": "Item B"}]
        }
        """
        
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_data}, prompt])
        text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)

        new_order = Order(
            po_number=data.get("po_number", "UNKNOWN"),
            vendor_name=data.get("vendor_name", "UNKNOWN"),
            currency=data.get("currency", ""),  # Saves Currency
            total_amount=float(data.get("total_amount", 0.0) if data.get("total_amount") else 0.0),
            payment_terms=data.get("payment_terms", "N/A"),
            items=json.dumps(data.get("items", []))
        )
        db.session.add(new_order)
        db.session.commit()

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
