import os
import json
import re  # New: For cleaning prices properly
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, send_file, render_template_string
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from io import BytesIO
import threading
import imaplib
import email
from email.header import decode_header
import time

app = Flask(__name__)

# --- CONFIGURATION ---
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- DATABASE MODEL ---
class Base(DeclarativeBase):
    pass
db = SQLAlchemy(model_class=Base)
db.init_app(app)

class Order(db.Model):
    __tablename__ = 'orders_v6' # Version 6 for Fresh Start
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False) # Stores number like 5000.0
    items = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="PO Received")

with app.app_context():
    db.create_all()

# --- HELPER: CLEAN PRICE (Idhu dhaan mukkiyam!) ---
def clean_float(value):
    """ Converts '$ 1,200.50' -> 1200.50 (Safe Math) """
    try:
        if not value: return 0.0
        # Remove everything that is NOT a number or dot
        clean_str = re.sub(r'[^\d.]', '', str(value))
        return float(clean_str) if clean_str else 0.0
    except:
        return 0.0

# --- HELPER: GET HIGH VALUE ITEM ---
def get_high_value_item_name(items_json):
    """ Finds item with highest (Qty * Price) """
    try:
        if not items_json: return "-"
        items = json.loads(items_json)
        if not items: return "-"
        
        best_item = "-"
        max_val = -1.0
        
        for item in items:
            name = item.get('name', 'Unknown')
            price = clean_float(item.get('price', 0))
            qty = clean_float(item.get('qty', 1))
            total = price * qty
            
            if total > max_val:
                max_val = total
                best_item = name
                
        return best_item
    except: return "-"

# --- AI LOGIC ---
def process_document(file_data):
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        # STRICT PROMPT
        prompt = """
        Analyze PDF. Determine Type: "CUSTOMER_PO", "OA", "SHIPPING".
        
        1. CUSTOMER_PO: Extract po_number, vendor_name, total_amount, items(name, qty, price).
        2. OA: Extract 'Reference PO Number'.
        3. SHIPPING: Extract 'Reference PO Number'.
        
        Return STRICT JSON:
        {
            "type": "CUSTOMER_PO",
            "po_number": "PO-12345",
            "vendor_name": "ABC Corp",
            "total_amount": "5000.00",
            "items": [{"name": "Laptop", "qty": 1, "price": "1000"}]
        }
        """
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_data}, prompt])
        text = response.text.replace("```json", "").replace("```", "").strip()
        
        print(f"AI Response: {text}") # Check Render Logs if fails
        
        data = json.loads(text)
        doc_type = data.get("type", "UNKNOWN")
        po_num = str(data.get("po_number", "")).strip()
        
        if not po_num: return "Skipped: No PO Number found"

        with app.app_context():
            # Create NEW PO
            if doc_type == "CUSTOMER_PO":
                # Check if exists (Prevent Duplicates)
                existing = Order.query.filter_by(po_number=po_num).first()
                if existing:
                    return f"Duplicate: PO {po_num} already exists."
                
                # CLEAN DATA BEFORE SAVING
                amount = clean_float(data.get("total_amount"))
                
                new_order = Order(
                    po_number=po_num,
                    vendor_name=data.get("vendor_name", "Unknown"),
                    total_amount=amount,
                    items=json.dumps(data.get("items", [])),
                    status="PO Received"
                )
                db.session.add(new_order)
                db.session.commit()
                return "Success: PO Created"

            # Update Existing (OA / Shipping)
            elif doc_type in ["OA", "SHIPPING"]:
                # Fuzzy Search (Matches '123' in 'PO-123')
                order = Order.query.filter(Order.po_number.ilike(f"%{po_num}%")).first()
                if order:
                    if doc_type == "OA": order.status = "OA Received"
                    if doc_type == "SHIPPING": order.status = "Shipped"
                    db.session.commit()
                    return f"Success: Status Updated to {order.status}"
                else:
                    return f"Failed: PO {po_num} not found in DB."
                    
        return "Processed (No Action)"

    except Exception as e:
        print(f"❌ Error: {e}")
        # IMPORTANT: Fix for 'One PO Only' issue
        with app.app_context():
            db.session.rollback() 
        return f"Error: {str(e)}"

# --- ROUTES ---
def generate_excel():
    orders = Order.query.all()
    data = []
    for o in orders:
        data.append({
            "PO Number": o.po_number,
            "Vendor": o.vendor_name,
            "Main Item": get_high_value_item_name(o.items),
            "Total Value": o.total_amount,
            "Status": o.status
        })
    df = pd.DataFrame(data)
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    out.seek(0)
    return out

@app.route("/")
def home():
    orders = Order.query.order_by(Order.id.desc()).all()
    return render_template_string("""
    <style>
        body{font-family:sans-serif; padding:20px; background:#f4f7f6}
        table{width:100%; border-collapse:collapse; background:white; box-shadow:0 2px 5px rgba(0,0,0,0.1)}
        th,td{padding:12px; border-bottom:1px solid #ddd; text-align:left}
        th{background:#2c3e50; color:white}
        .btn{padding:10px 20px; background:#27ae60; color:white; text-decoration:none; border-radius:4px; display:inline-block; margin-bottom:10px;}
        .status-po{color:orange; font-weight:bold}
        .status-oa{color:#2980b9; font-weight:bold}
        .status-ship{color:green; font-weight:bold}
    </style>
    
    <h1>🚀 Sales AI Manager (v6 Stable)</h1>
    
    <div style="background:white; padding:20px; border-radius:8px; display:inline-block;">
        <form action="/upload" method="post" enctype="multipart/form-data">
            <b>Upload PO / OA / Invoice:</b><br><br>
            <input type="file" name="file" accept=".pdf" required> 
            <button class="btn" style="border:none; cursor:pointer;">Analyze PDF</button>
        </form>
    </div>
    
    <br><br>
    <a href="/download" class="btn" style="background:#2980b9;">📥 Download Excel</a>
    
    <h3>Live Database:</h3>
    <table>
        <tr><th>PO #</th><th>Vendor</th><th>Main Item (High Value)</th><th>Total Value</th><th>Status</th></tr>
        {% for o in orders %}
        <tr>
            <td>{{o.po_number}}</td>
            <td>{{o.vendor_name}}</td>
            <td>{{o.items}}</td> <td>{{o.total_amount}}</td>
            <td class="{% if 'Shipped' in o.status %}status-ship{% elif 'OA' in o.status %}status-oa{% else %}status-po{% endif %}">
                {{o.status}}
            </td>
        </tr>
        {% else %}
        <tr><td colspan="5">No Orders Found. Upload a PDF!</td></tr>
        {% endfor %}
    </table>
    """, orders=orders)

# Inject Helper into Template Logic (Simulated by re-processing for view)
@app.route("/")
def home_view():
    orders = Order.query.order_by(Order.id.desc()).all()
    # Process display data safely
    display_data = []
    for o in orders:
        main_item = get_high_value_item_name(o.items)
        display_data.append({
            "po": o.po_number, "vendor": o.vendor_name, 
            "item": main_item, "total": o.total_amount, "status": o.status
        })
        
    return render_template_string("""
    <style>
        body{font-family:sans-serif; padding:20px; background:#f4f7f6}
        table{width:100%; border-collapse:collapse; background:white; box-shadow:0 2px 5px rgba(0,0,0,0.1)}
        th,td{padding:12px; border-bottom:1px solid #ddd; text-align:left}
        th{background:#2c3e50; color:white}
        .btn{padding:10px 20px; background:#27ae60; color:white; text-decoration:none; border-radius:4px; display:inline-block; margin-bottom:10px;}
    </style>
    
    <h1>🚀 Sales AI Manager (Final Fix)</h1>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".pdf" required> <button class="btn">Analyze</button>
    </form>
    <a href="/download" class="btn" style="background:#2980b9">Download Excel</a>
    
    <table>
        <tr><th>PO #</th><th>Vendor</th><th>Main Item (High Value)</th><th>Total</th><th>Status</th></tr>
        {% for row in data %}
        <tr>
            <td>{{row.po}}</td><td>{{row.vendor}}</td>
            <td style="color:#c0392b; font-weight:bold">{{row.item}}</td>
            <td>{{row.total}}</td><td>{{row.status}}</td>
        </tr>
        {% endfor %}
    </table>
    """, data=display_data)

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if f: 
        result = process_document(f.read())
        return f"<script>alert('{result}'); window.location.href='/';</script>"
    return "<script>window.location.href='/'</script>"

@app.route("/download")
def download():
    return send_file(generate_excel(), as_attachment=True, download_name="Tracker.xlsx")

# --- EMAIL WATCHER ---
def email_bot():
    while True:
        try:
            if not EMAIL_USER: 
                time.sleep(30); continue
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL_USER, EMAIL_PASS)
            mail.select("inbox")
            status, messages = mail.search(None, 'UNREAD')
            for e_id in messages[0].split():
                res, msg = mail.fetch(e_id, "(RFC822)")
                for response in msg:
                    if isinstance(response, tuple):
                        msg_body = email.message_from_bytes(response[1])
                        for part in msg_body.walk():
                            if part.get_filename() and part.get_filename().endswith(".pdf"):
                                print(f"📧 Auto-Processing PDF from Email...")
                                process_document(part.get_payload(decode=True))
                        mail.store(e_id, '+FLAGS', '\\Seen')
            mail.logout()
        except Exception as e:
            print(f"Email Error: {e}")
        time.sleep(30)

if os.environ.get("EMAIL_USER"):
    t = threading.Thread(target=email_bot)
    t.daemon = True
    t.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
