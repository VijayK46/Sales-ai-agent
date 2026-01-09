import os
import json
import re
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
    __tablename__ = 'orders_v8_flash' # Fresh Table
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    items = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="PO Received")

with app.app_context():
    db.create_all()

# --- HELPER: CLEAN PRICE (Fixes 'Value Varla' Issue) ---
def clean_float(value):
    try:
        if not value: return 0.0
        # Remove currency symbols and commas
        clean_str = re.sub(r'[^\d.]', '', str(value))
        return float(clean_str) if clean_str else 0.0
    except: return 0.0

# --- HELPER: HIGH VALUE ITEM (Fixes 'Item Name Thappu') ---
def get_high_value_item_name(items_json):
    try:
        if not items_json: return "-"
        items = json.loads(items_json)
        if not items: return "-"
        
        best_item = "-"
        max_val = -1.0
        
        for item in items:
            name = item.get('name', 'Unknown')
            # Calculate Value carefully
            price = clean_float(item.get('price', 0))
            qty = clean_float(item.get('qty', 1))
            total = price * qty
            
            if total > max_val:
                max_val = total
                best_item = name
        return best_item
    except: return "-"

# --- AI LOGIC (GEMINI FLASH LATEST) ---
def process_document(file_data):
    try:
        # ✅ Neenga sonna padiye "gemini-flash-latest" use panrom
        model = genai.GenerativeModel("gemini-flash-latest")
        
        prompt = """
        Analyze PDF. 
        Determine Type: "CUSTOMER_PO", "OA", "SHIPPING".
        
        1. CUSTOMER_PO: Extract po_number, vendor_name, total_amount, items(name, qty, price).
        2. OA: Extract 'Reference PO Number'.
        3. SHIPPING: Extract 'Reference PO Number'.
        
        Return JSON:
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
        data = json.loads(text)
        
        doc_type = data.get("type", "UNKNOWN")
        po_num = str(data.get("po_number", "")).strip()

        if not po_num: return "Skipped: No PO Number"

        with app.app_context():
            # 1. New PO
            if doc_type == "CUSTOMER_PO":
                existing = Order.query.filter_by(po_number=po_num).first()
                if existing: return f"Duplicate: PO {po_num} Exists"
                
                # Fix Price before saving
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
                return "✅ Success: PO Created"

            # 2. Update Status (OA / Shipping)
            elif doc_type in ["OA", "SHIPPING"]:
                # Match PO Number
                order = Order.query.filter(Order.po_number.ilike(f"%{po_num}%")).first()
                if order:
                    if doc_type == "OA": order.status = "OA Received"
                    if doc_type == "SHIPPING": order.status = "Shipped"
                    db.session.commit()
                    return f"✅ Status Updated: {order.status}"
                else:
                    return f"❌ Error: Original PO {po_num} Not Found"
                    
        return "Processed"

    except Exception as e:
        print(f"Error: {e}")
        with app.app_context(): db.session.rollback() # Reset DB on error
        return f"Error: {str(e)}"

# --- ROUTES ---
@app.route("/")
def home_view():
    orders = Order.query.order_by(Order.id.desc()).all()
    display_data = []
    for o in orders:
        display_data.append({
            "po": o.po_number, "vendor": o.vendor_name, 
            "item": get_high_value_item_name(o.items), 
            "total": o.total_amount, "status": o.status
        })
    return render_template_string("""
    <style>body{font-family:sans-serif;padding:20px} table{width:100%;border-collapse:collapse;margin-top:20px} th,td{border:1px solid #ddd;padding:10px}</style>
    <h1>🚀 Sales AI (Gemini Flash)</h1>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".pdf" required> <button>Analyze</button>
    </form>
    <a href="/download">Download Excel</a>
    <table>
        <tr><th>PO #</th><th>Vendor</th><th>Main Item</th><th>Total</th><th>Status</th></tr>
        {% for row in data %}
        <tr><td>{{row.po}}</td><td>{{row.vendor}}</td><td style="color:blue;font-weight:bold">{{row.item}}</td><td>{{row.total}}</td><td>{{row.status}}</td></tr>
        {% endfor %}
    </table>
    """, data=display_data)

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if f: 
        res = process_document(f.read())
        return f"<script>alert('{res}');window.location.href='/'</script>"
    return "<script>window.location.href='/'</script>"

@app.route("/download")
def download():
    orders = Order.query.all()
    data = []
    for o in orders:
        data.append({"PO": o.po_number, "Vendor": o.vendor_name, "Item": get_high_value_item_name(o.items), "Total": o.total_amount, "Status": o.status})
    df = pd.DataFrame(data)
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="Tracker.xlsx")

# --- EMAIL WATCHER ---
def email_bot():
    while True:
        try:
            if not EMAIL_USER: time.sleep(30); continue
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
                                process_document(part.get_payload(decode=True))
                        mail.store(e_id, '+FLAGS', '\\Seen')
            mail.logout()
        except: pass
        time.sleep(30)

if os.environ.get("EMAIL_USER"):
    t = threading.Thread(target=email_bot)
    t.daemon = True
    t.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
