import os
import json
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
# Database Setup
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Email Config (Render Environment Variables irundhu edukum)
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")

# AI Setup
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

# --- DATABASE MODEL ---
class Base(DeclarativeBase):
    pass
db = SQLAlchemy(model_class=Base)
db.init_app(app)

class Order(db.Model):
    __tablename__ = 'orders_final'
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    items = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="PO Received") # Status Track Panna

with app.app_context():
    db.create_all()

# --- AI LOGIC (Gemini 1.5 Flash) ---
def process_document(file_data):
    try:
        # Neenga ketta "gemini-1.5-flash" inga iruku. 
        # requirements.txt update pannathala idhu work aagum.
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        prompt = """
        Analyze PDF. Determine Type: "CUSTOMER_PO", "OA", "SHIPPING".
        
        1. CUSTOMER_PO: Extract po_number, vendor_name, total_amount, items(name, qty, price).
        2. OA (Order Acknowledgement): Extract 'Reference PO Number'.
        3. SHIPPING (Invoice): Extract 'Reference PO Number'.
        
        Return JSON:
        {
            "type": "CUSTOMER_PO" or "OA" or "SHIPPING",
            "po_number": "...",
            "vendor_name": "...",
            "total_amount": 0.0,
            "items": [...]
        }
        """
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_data}, prompt])
        text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        
        doc_type = data.get("type")
        po_num = data.get("po_number")
        
        with app.app_context():
            # Scenario 1: Pudhu PO vandhal
            if doc_type == "CUSTOMER_PO":
                new_order = Order(
                    po_number=po_num,
                    vendor_name=data.get("vendor_name", "Unknown"),
                    total_amount=data.get("total_amount", 0),
                    items=json.dumps(data.get("items", [])),
                    status="PO Received"
                )
                db.session.add(new_order)
                db.session.commit()
                return "New PO Created"

            # Scenario 2: OA vandhal (Update Status)
            elif doc_type == "OA":
                order = Order.query.filter(Order.po_number.ilike(f"%{po_num}%")).first()
                if order:
                    order.status = "OA Received"
                    db.session.commit()
                    return "Status Updated: OA Received"

            # Scenario 3: Shipping Doc vandhal (Update Status)
            elif doc_type == "SHIPPING":
                order = Order.query.filter(Order.po_number.ilike(f"%{po_num}%")).first()
                if order:
                    order.status = "Shipped"
                    db.session.commit()
                    return "Status Updated: Shipped"
                    
        return "Processed"
    except Exception as e:
        print(f"Error: {e}")
        return "Error"

# --- EMAIL WATCHER (Automatic) ---
def email_bot():
    while True:
        try:
            if not EMAIL_USER: 
                time.sleep(30)
                continue
                
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL_USER, EMAIL_PASS)
            mail.select("inbox")
            
            # Unread mails with PDF
            status, messages = mail.search(None, 'UNREAD')
            for e_id in messages[0].split():
                res, msg = mail.fetch(e_id, "(RFC822)")
                for response in msg:
                    if isinstance(response, tuple):
                        msg_body = email.message_from_bytes(response[1])
                        # Process PDF
                        for part in msg_body.walk():
                            if part.get_filename() and part.get_filename().endswith(".pdf"):
                                pdf_data = part.get_payload(decode=True)
                                process_document(pdf_data)
                        
                        mail.store(e_id, '+FLAGS', '\\Seen') # Mark as Read
            mail.logout()
        except:
            pass
        time.sleep(30) # Check every 30 seconds

# Start Email Bot in Background
if os.environ.get("EMAIL_USER"):
    t = threading.Thread(target=email_bot)
    t.daemon = True
    t.start()

# --- WEB & EXCEL ---
def generate_excel():
    orders = Order.query.all()
    data = []
    for o in orders:
        # High Value Item Logic
        try:
            items = json.loads(o.items)
            # Find item with max (price * qty)
            best = max(items, key=lambda x: float(str(x.get('price',0)).replace(',','')) * float(str(x.get('qty',1)).replace(',','')), default={})
            main_item = best.get('name', '-')
        except: main_item = "-"
        
        data.append({
            "PO Number": o.po_number,
            "Vendor": o.vendor_name,
            "Main Item": main_item,
            "Total": o.total_amount,
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
    <h1>🚀 Distributor Sales Bot</h1>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file" required> <button>Manual Upload</button>
    </form>
    <a href="/download">Download Excel</a>
    <table border="1" style="width:100%; border-collapse:collapse; margin-top:20px;">
        <tr><th>PO #</th><th>Vendor</th><th>Status</th></tr>
        {% for o in orders %}
        <tr><td>{{o.po_number}}</td><td>{{o.vendor_name}}</td>
        <td style="color:{% if 'Shipped' in o.status %}green{% elif 'OA' in o.status %}blue{% else %}orange{% endif %}">
        {{o.status}}</td></tr>
        {% endfor %}
    </table>
    """, orders=orders)

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if f: process_document(f.read())
    return "<script>window.location.href='/'</script>"

@app.route("/download")
def download():
    return send_file(generate_excel(), as_attachment=True, download_name="Tracker.xlsx")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
