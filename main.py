import os
import google.generativeai as genai
from flask import Flask, request, jsonify, send_file, render_template_string
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import io
import json

app = Flask(__name__)

# --- 1. CONFIGURATION ---
GENAI_API_KEY = os.environ.get("GENAI_API_KEY")
genai.configure(api_key=GENAI_API_KEY)

# SQL Database Setup
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1) # Render Fix

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Email Setup (Replace with your details if testing locally)
SENDER_EMAIL = "unga_email@gmail.com"  
SENDER_PASSWORD = "abcd efgh ijkl mnop"

STRICT_VENDORS = ["Newport", "Thorlabs", "Edmund", "Micro-Controle", "Coherent"]

# --- 2. DATABASE MODEL ---
class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50))
    date = db.Column(db.String(50))
    customer_name = db.Column(db.String(200))
    vendor_name = db.Column(db.String(200))
    item_name = db.Column(db.Text)
    amount = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# --- 3. AI & EMAIL FUNCTIONS ---
def extract_po_data(file_content):
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = """Extract: PO Number, Date, Customer Name, Vendor Name, Highest Value Item, Total Amount.
    Output JSON: {"po_number": "", "po_date": "", "customer_name": "", "vendor_name": "", "highest_value_product": "", "total_amount": 0.0}"""
    try:
        response = model.generate_content([{"mime_type": "application/pdf", "data": file_content}, prompt])
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except:
        return None

def send_smart_email(customer_email, po_data):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = customer_email
    
    vendor = po_data.get('vendor_name', '')
    is_strict = any(v.lower() in vendor.lower() for v in STRICT_VENDORS) if vendor else False
    
    if is_strict:
        msg['Subject'] = f"ACTION REQUIRED: PO {po_data['po_number']}"
        body = f"Dear Customer,\n\nOrder for {vendor} requires End User Form.\n\nRegards,\nSales Team"
    else:
        msg['Subject'] = f"Order Received: PO {po_data['po_number']}"
        body = f"Dear Customer,\n\nThank you for order PO {po_data['po_number']}.\n\nRegards,\nSales Team"
        
    msg.attach(MIMEText(body, 'plain'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, customer_email, msg.as_string())
        server.quit()
    except Exception as e:
        print(f"Email Error: {e}")

# --- 4. WEBSITE UI (HTML) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Sales Agent AI 🤖</title>
    <style>
        body { font-family: sans-serif; text-align: center; padding: 50px; background-color: #f4f4f9; }
        .container { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.1); max-width: 500px; margin: auto; }
        h1 { color: #2c3e50; }
        input[type=file] { margin: 20px 0; }
        button { background: #27ae60; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 16px; }
        button:hover { background: #219150; }
        .link { display: block; margin-top: 20px; color: #3498db; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Sales Agent AI</h1>
        <p>Upload a Purchase Order PDF to process.</p>
        <form action="/analyze-order" method="post" enctype="multipart/form-data">
            <input type="file" name="file" accept=".pdf" required>
            <br>
            <button type="submit">Analyze & Process</button>
        </form>
        <a href="/download-report" class="link">📥 Download Excel Report</a>
    </div>
</body>
</html>
"""

# --- 5. ROUTES ---
@app.route("/")
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    if 'file' not in request.files: return "No file", 400
    file = request.files['file']
    data = extract_po_data(file.read())
    
    if data:
        # Save to SQL
        new_order = Order(
            po_number=data.get("po_number"), date=data.get("po_date"),
            customer_name=data.get("customer_name"), vendor_name=data.get("vendor_name"),
            item_name=data.get("highest_value_product"), amount=data.get("total_amount")
        )
        db.session.add(new_order)
        db.session.commit()
        
        # Send Email
        send_smart_email(SENDER_EMAIL, data) # Testing with your email
        
        return f"<h1>✅ Success!</h1><p>PO {data['po_number']} saved to SQL Database.</p><p>Email Sent.</p><a href='/'>Go Back</a>"
    return "Failed to extract", 500

@app.route("/download-report")
def download_report():
    orders = Order.query.all()
    data = [{"PO": o.po_number, "Vendor": o.vendor_name, "Amount": o.amount} for o in orders]
    df = pd.DataFrame(data)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    out.seek(0)
    return send_file(out, download_name="report.xlsx", as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
