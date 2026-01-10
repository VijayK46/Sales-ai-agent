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
    __tablename__ = 'orders_v12_smart' # Version 12
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    customer_name = db.Column(db.String(100), nullable=False)
    currency_symbol = db.Column(db.String(10), nullable=True)
    total_amount = db.Column(db.Float, nullable=False)
    items = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="PO Received")

with app.app_context():
    db.create_all()

# --- HELPERS ---
def clean_float(value):
    try:
        clean_str = re.sub(r'[^\d.]', '', str(value)) if value else "0"
        return float(clean_str) if clean_str else 0.0
    except: return 0.0

def get_high_value_item_name(items_json):
    try:
        if not items_json: return "-"
        items = json.loads(items_json)
        if not items: return "-"
        best, max_val = "-", -1.0
        for item in items:
            raw_name = item.get('name', 'Unknown')
            short_name = " ".join(raw_name.split()[:4]) if len(raw_name.split()) > 4 else raw_name
            price = clean_float(item.get('price', 0))
            qty = clean_float(item.get('qty', 1))
            if (price * qty) > max_val:
                max_val = (price * qty)
                best = short_name
        return best
    except: return "-"

# --- AI LOGIC ---
def process_document(file_data):
    try:
        model = genai.GenerativeModel("gemini-flash-latest")
        prompt = """
        Analyze PDF. Types: "CUSTOMER_PO", "OA", "SHIPPING".
        1. CUSTOMER_PO: Extract customer_name, po_number, total_amount, currency_symbol, items(name,qty,price).
           * Item Name: PRODUCT CATEGORY ONLY. No Part No.
        2. OA/SHIPPING: Extract Reference PO Number.
        Return JSON.
        """
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_data}, prompt])
        text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        
        doc_type = data.get("type", "UNKNOWN")
        po_num = str(data.get("po_number", "")).strip()

        if not po_num: return "Skipped: No PO Number"

        with app.app_context():
            if doc_type == "CUSTOMER_PO":
                if Order.query.filter_by(po_number=po_num).first(): return "Duplicate PO"
                new_order = Order(
                    po_number=po_num,
                    customer_name=data.get("customer_name", "Unknown"),
                    currency_symbol=data.get("currency_symbol", ""),
                    total_amount=clean_float(data.get("total_amount")),
                    items=json.dumps(data.get("items", [])),
                    status="PO Received"
                )
                db.session.add(new_order)
                db.session.commit()
                return "✅ PO Created"
            elif doc_type in ["OA", "SHIPPING"]:
                order = Order.query.filter(Order.po_number.ilike(f"%{po_num}%")).first()
                if order:
                    order.status = "OA Received" if doc_type == "OA" else "Shipped"
                    db.session.commit()
                    return f"✅ Updated: {order.status}"
                return "❌ PO Not Found"
    except Exception as e:
        with app.app_context(): db.session.rollback()
        return f"Error: {str(e)}"

# --- ROUTES ---
@app.route("/")
def home_view():
    orders = Order.query.order_by(Order.id.desc()).all()
    data = [{"po": o.po_number, "customer": o.customer_name, "item": get_high_value_item_name(o.items), "total": f"{o.currency_symbol or ''} {o.total_amount}", "status": o.status} for o in orders]
    return render_template_string("""
    <style>body{font-family:sans-serif;padding:20px} table{width:100%;border-collapse:collapse;margin-top:20px} th,td{border:1px solid #ddd;padding:10px} .btn{padding:10px;background:blue;color:white;text-decoration:none}</style>
    <h1>🚀 Sales AI Manager</h1>
    <a href="/test-email" class="btn" style="background:orange">🛠️ Test Email Connection</a>
    <br><br>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".pdf" required> <button>Analyze</button>
    </form>
    <table>
        <tr><th>PO #</th><th>Customer</th><th>Main Item</th><th>Total</th><th>Status</th></tr>
        {% for row in data %}
        <tr><td>{{row.po}}</td><td>{{row.customer}}</td><td>{{row.item}}</td><td>{{row.total}}</td><td>{{row.status}}</td></tr>
        {% endfor %}
    </table>
    """, data=data)

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if f: return f"<script>alert('{process_document(f.read())}');window.location.href='/'</script>"
    return "<script>window.location.href='/'</script>"

@app.route("/test-email")
def test_email():
    try:
        if not EMAIL_USER or not EMAIL_PASS: return "❌ Error: Email/Pass Missing."
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")
        # 🔥 Filter: Search only UNSEEN mails with "PO" in Subject
        status, messages = mail.search(None, '(UNSEEN SUBJECT "PO")')
        count = len(messages[0].split()) if messages[0] else 0
        mail.logout()
        return f"✅ <b>SUCCESS!</b><br>Filtered Search Active (Looking for Subject: 'PO')<br>Relevant Unread Emails: {count}"
    except Exception as e:
        return f"❌ <b>FAILED!</b><br>Error: {str(e)}"

# --- EMAIL WATCHER (SMART FILTER) ---
def email_bot():
    while True:
        try:
            if not EMAIL_USER: time.sleep(30); continue
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL_USER, EMAIL_PASS)
            mail.select("inbox")
            
            # 🔥 IMPORTANT: Now fetching ONLY emails with "PO" in Subject
            # This skips your 5000 junk emails!
            status, messages = mail.search(None, '(UNSEEN SUBJECT "PO")')
            
            if messages[0]:
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
        time.sleep(30) # Check every 30 seconds

if os.environ.get("EMAIL_USER"):
    t = threading.Thread(target=email_bot)
    t.daemon = True
    t.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
