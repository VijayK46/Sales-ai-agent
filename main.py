import os
import json
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, send_file, render_template_string
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import traceback
from io import BytesIO
import threading
import imaplib
import email
from email.header import decode_header
import time
import re

app = Flask(__name__)

# --- DATABASE SETUP ---
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")

class Base(DeclarativeBase):
    pass
db = SQLAlchemy(model_class=Base)
db.init_app(app)

class Order(db.Model):
    __tablename__ = 'orders_v5'  # New Version for Status
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    currency = db.Column(db.String(10), nullable=True)
    total_amount = db.Column(db.Float, nullable=False)
    payment_terms = db.Column(db.String(200), nullable=True)
    items = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="PO Received") # New Status Column
    oa_received = db.Column(db.Boolean, default=False)       # Track OA

with app.app_context():
    db.create_all()

api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- SMART AI PROCESSOR (Handles PO, OA, Shipping) ---
def process_document(file_data, filename=""):
    try:
        model = genai.GenerativeModel("gemini-flash-latest")
        
        # Super Prompt: Decide Document Type
        prompt = """
        Analyze this PDF document.
        Determine the Type: "CUSTOMER_PO", "OEM_OA", "SHIPPING_DOC", or "OTHER".
        
        1. If CUSTOMER_PO: Extract po_number, vendor_name, currency, total_amount, items.
        2. If OEM_OA (Order Acknowledgement): Extract the 'Reference PO Number' (The customer PO this is acknowledging).
        3. If SHIPPING_DOC (Invoice/Packing List): Extract the 'Reference PO Number'.
        
        Return JSON:
        {
            "doc_type": "CUSTOMER_PO" | "OEM_OA" | "SHIPPING_DOC" | "OTHER",
            "po_number": "...",  (Crucial for matching)
            "vendor_name": "...",
            "currency": "...",
            "total_amount": 0.0,
            "items": [...]
        }
        """
        
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_data}, prompt])
        text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        
        doc_type = data.get("doc_type", "OTHER")
        po_num = data.get("po_number", "").strip()
        
        with app.app_context():
            # CASE 1: New Customer PO
            if doc_type == "CUSTOMER_PO" and po_num:
                # Check if exists to avoid duplicates
                existing = Order.query.filter_by(po_number=po_num).first()
                if not existing:
                    new_order = Order(
                        po_number=po_num,
                        vendor_name=data.get("vendor_name", "UNKNOWN"),
                        currency=data.get("currency", ""),
                        total_amount=float(data.get("total_amount", 0.0)),
                        payment_terms=data.get("payment_terms", "N/A"),
                        items=json.dumps(data.get("items", [])),
                        status="PO Received"
                    )
                    db.session.add(new_order)
                    db.session.commit()
                    return f"New PO {po_num} Created"
                else:
                    return f"PO {po_num} already exists"

            # CASE 2: OA from OEM (Update Existing)
            elif doc_type == "OEM_OA" and po_num:
                # Find the original PO
                # (Logic: Search for PO Number in DB)
                order = Order.query.filter(Order.po_number.ilike(f"%{po_num}%")).first()
                if order:
                    order.status = "OA Received (In Process)"
                    order.oa_received = True
                    db.session.commit()
                    return f"Updated: OA Received for {po_num}"
                else:
                    return f"OA found for {po_num}, but original PO not in DB."

            # CASE 3: Shipping Doc (Update Existing)
            elif doc_type == "SHIPPING_DOC" and po_num:
                order = Order.query.filter(Order.po_number.ilike(f"%{po_num}%")).first()
                if order:
                    order.status = "Shipped / Invoice Recvd"
                    db.session.commit()
                    return f"Updated: Shipped status for {po_num}"
        
        return "Document Processed (No Action)"

    except Exception as e:
        print(f"AI Error: {e}")
        return "Error"

# --- EMAIL WATCHER (Filters Types) ---
def email_watcher():
    print("📧 Watcher Started...")
    while True:
        try:
            if not EMAIL_USER or not EMAIL_PASS:
                time.sleep(60); continue

            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL_USER, EMAIL_PASS)
            mail.select("inbox")

            # Look for Keywords in Subject
            status, messages = mail.search(None, '(UNREAD OR (SUBJECT "PO") (SUBJECT "OA") (SUBJECT "Acknowledgement") (SUBJECT "Shipping") (SUBJECT "Invoice"))')
            
            for e_id in messages[0].split():
                res, msg_data = mail.fetch(e_id, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        subject = decode_header(msg["Subject"])[0][0]
                        if isinstance(subject, bytes): subject = subject.decode()
                        
                        print(f"📩 Checking: {subject}")
                        
                        # Only process if PDF attached
                        for part in msg.walk():
                            if part.get_content_maintype() == 'multipart': continue
                            if part.get("Content-Disposition") is None: continue
                            filename = part.get_filename()
                            
                            if filename and filename.lower().endswith(".pdf"):
                                pdf_data = part.get_payload(decode=True)
                                result = process_document(pdf_data, filename)
                                print(f"   👉 Result: {result}")

                        mail.store(e_id, '+FLAGS', '\\Seen')
            mail.logout()
        except: pass
        time.sleep(30)

if os.environ.get("EMAIL_USER"):
    t = threading.Thread(target=email_watcher)
    t.daemon = True
    t.start()

# --- WEB & EXCEL ---
def get_high_value_item_name(items_json):
    # (Same High Value Logic as before)
    try:
        if not items_json: return "-"
        items = json.loads(items_json)
        if not items: return "-"
        best = max(items, key=lambda x: float(str(x.get('price',0)).replace(',','')) * float(str(x.get('qty',1)).replace(',','')), default=None)
        return best['name'] if best else "-"
    except: return "-"

def generate_master_excel():
    orders = Order.query.all()
    data = []
    for o in orders:
        data.append({
            "Sl. No": o.id, "Vendor": o.vendor_name, "Item": get_high_value_item_name(o.items),
            "PO Number": o.po_number, "Total": o.total_amount, "Status": o.status 
        })
    df = pd.DataFrame(data)
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w: df.to_excel(w, index=False)
    out.seek(0)
    return out

@app.route("/")
def home():
    orders = Order.query.order_by(Order.id.desc()).all()
    # (Show Status in Dashboard)
    return render_template_string("""
    <html>
        <head><title>Sales AI</title><style>table{width:100%;border-collapse:collapse} th,td{border:1px solid #ddd;padding:8px}</style></head>
        <body>
            <h1>🚀 Distributor Order Tracker</h1>
            <form action="/analyze-order" method="post" enctype="multipart/form-data">
                <input type="file" name="file" accept=".pdf" required> <button>Upload PO/OA/Doc</button>
            </form>
            <br><a href="/download">Download Report</a>
            <br><br>
            <table>
                <tr><th>PO #</th><th>Vendor</th><th>Item</th><th>Status</th><th>Action</th></tr>
                {% for o in orders %}
                <tr>
                    <td>{{o.po_number}}</td><td>{{o.vendor_name}}</td><td>{{o.items|length}}</td>
                    <td style="font-weight:bold; color: {% if 'Shipped' in o.status %}green{% elif 'OA' in o.status %}blue{% else %}orange{% endif %}">
                        {{o.status}}
                    </td>
                    <td>-</td>
                </tr>
                {% endfor %}
            </table>
        </body>
    </html>
    """, orders=orders)

@app.route("/analyze-order", methods=["POST"])
def analyze():
    f = request.files.get("file")
    if f: process_document(f.read())
    return "<script>window.location.href='/'</script>"

@app.route("/download")
def download():
    return send_file(generate_master_excel(), as_attachment=True, download_name="Tracker.xlsx")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
