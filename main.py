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
from datetime import datetime
import youtube_agent

app = Flask(__name__)

# --- CONFIGURATION (UPDATED FOR DB STABILITY) ---
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 🔥 CRITICAL FIX: Auto-Reconnect if DB connection drops
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,  # Checks connection before using
    "pool_recycle": 300,    # Refreshes connection every 5 mins
}

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
    __tablename__ = 'orders_v13_stable' # Version 13
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), nullable=False)
    customer_name = db.Column(db.String(100), nullable=False)
    currency_symbol = db.Column(db.String(10), nullable=True)
    total_amount = db.Column(db.Float, nullable=False)
    items = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="PO Received")

class Transcript(db.Model):
    __tablename__ = 'youtube_transcripts_v1'
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.String(20), nullable=False)
    url = db.Column(db.String(255), nullable=False)
    title = db.Column(db.String(255), nullable=True)
    channel = db.Column(db.String(150), nullable=True)
    source = db.Column(db.String(20), nullable=True)   # captions / audio
    duration = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default="Queued")  # Queued / Processing / Done / Error
    error = db.Column(db.Text, nullable=True)
    text = db.Column(db.Text, nullable=True)
    summary = db.Column(db.Text, nullable=True)  # JSON blob from the AI summariser
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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

# --- YOUTUBE TRANSCRIPT AGENT ---
def run_transcript_job(record_id, languages, want_summary):
    """Fetch + transcribe in the background so the request returns instantly."""
    with app.app_context():
        record = db.session.get(Transcript, record_id)
        if not record: return
        try:
            record.status = "Processing"
            db.session.commit()

            data = youtube_agent.get_transcript(record.url, languages=languages)
            record.source = data.get("source")
            record.title = (data.get("title") or "")[:255] or None
            record.channel = (data.get("channel") or "")[:150] or None
            record.duration = data.get("duration") or 0
            record.text = data.get("text")

            if want_summary:
                summary = youtube_agent.summarize_transcript(data.get("text", ""))
                if summary: record.summary = json.dumps(summary)

            record.status = "Done"
            record.error = None
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            record = db.session.get(Transcript, record_id)
            if record:
                record.status = "Error"
                record.error = str(e)[:2000]
                db.session.commit()

YT_PAGE = """
<style>
 body{font-family:sans-serif;padding:20px;max-width:1000px}
 table{width:100%;border-collapse:collapse;margin-top:20px}
 th,td{border:1px solid #ddd;padding:10px;text-align:left}
 .btn{padding:10px;background:blue;color:white;text-decoration:none;border-radius:4px}
 input[type=text]{padding:8px;width:420px}
 .err{color:#b00}
</style>
<h1>🎬 YouTube Transcript Agent</h1>
<a href="/" class="btn">← Back to Orders</a>
<form action="/youtube" method="post" style="margin-top:20px">
  <input type="text" name="url" placeholder="YouTube URL or video id" required>
  <input type="text" name="lang" value="en" size="6" title="Preferred caption languages">
  <label><input type="checkbox" name="summary" checked> AI summary</label>
  <button>Get Transcript</button>
</form>
<p>Captions are used when available; otherwise the audio is downloaded and transcribed by Gemini.
   This page refreshes every 10s while jobs are running.</p>
<table>
  <tr><th>Video</th><th>Source</th><th>Length</th><th>Status</th><th></th></tr>
  {% for r in rows %}
  <tr>
    <td><a href="{{r.url}}" target="_blank">{{ r.title or r.video_id }}</a>
        {% if r.channel %}<br><small>{{r.channel}}</small>{% endif %}</td>
    <td>{{ r.source or '-' }}</td>
    <td>{{ r.length }}</td>
    <td>{{ r.status }}{% if r.error %}<br><small class="err">{{r.error}}</small>{% endif %}</td>
    <td>{% if r.status == 'Done' %}<a href="/youtube/{{r.id}}">View</a> |
        <a href="/youtube/{{r.id}}/download">.txt</a>{% endif %}</td>
  </tr>
  {% endfor %}
</table>
{% if busy %}<script>setTimeout(function(){location.reload()},10000)</script>{% endif %}
"""

@app.route("/youtube", methods=["GET", "POST"])
def youtube_view():
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        video_id = youtube_agent.extract_video_id(url)
        if not video_id:
            return "<script>alert('Not a valid YouTube URL or video id');window.location.href='/youtube'</script>"

        languages = tuple(l.strip() for l in (request.form.get("lang") or "en").split(",") if l.strip())
        record = Transcript(video_id=video_id, url=youtube_agent.watch_url(video_id), status="Queued")
        db.session.add(record)
        db.session.commit()

        t = threading.Thread(
            target=run_transcript_job,
            args=(record.id, languages or ("en",), bool(request.form.get("summary"))),
        )
        t.daemon = True
        t.start()
        return "<script>window.location.href='/youtube'</script>"

    records = Transcript.query.order_by(Transcript.id.desc()).limit(50).all()
    rows = [{
        "id": r.id, "video_id": r.video_id, "url": r.url, "title": r.title,
        "channel": r.channel, "source": r.source, "status": r.status, "error": r.error,
        "length": youtube_agent.format_timestamp(r.duration) if r.duration else "-",
    } for r in records]
    busy = any(r["status"] in ("Queued", "Processing") for r in rows)
    return render_template_string(YT_PAGE, rows=rows, busy=busy)

@app.route("/youtube/<int:record_id>")
def youtube_detail(record_id):
    record = db.session.get(Transcript, record_id)
    if not record: return "Not found", 404
    summary = {}
    if record.summary:
        try: summary = json.loads(record.summary)
        except: summary = {}
    return render_template_string("""
    <style>
     body{font-family:sans-serif;padding:20px;max-width:900px}
     pre{white-space:pre-wrap;background:#f6f6f6;padding:15px;border-radius:6px;line-height:1.6}
     .btn{padding:10px;background:blue;color:white;text-decoration:none;border-radius:4px}
    </style>
    <a href="/youtube" class="btn">← Back</a>
    <h1>{{ r.title or r.video_id }}</h1>
    <p><a href="{{r.url}}" target="_blank">{{r.url}}</a> &middot; source: {{r.source}}
       &middot; <a href="/youtube/{{r.id}}/download">download .txt</a></p>
    {% if summary %}
      <h3>Summary</h3><p>{{ summary.get('summary','') }}</p>
      {% for key in ['key_points','action_items','topics'] %}
        {% if summary.get(key) %}
          <h4>{{ key.replace('_',' ').title() }}</h4>
          <ul>{% for item in summary[key] %}<li>{{ item }}</li>{% endfor %}</ul>
        {% endif %}
      {% endfor %}
    {% endif %}
    <h3>Transcript</h3>
    <pre>{{ r.text }}</pre>
    """, r=record, summary=summary)

@app.route("/youtube/<int:record_id>/download")
def youtube_download(record_id):
    record = db.session.get(Transcript, record_id)
    if not record or not record.text: return "Not found", 404
    buffer = BytesIO((record.text or "").encode("utf-8"))
    name = re.sub(r'[^\w\-]+', '_', record.title or record.video_id)[:60]
    return send_file(buffer, mimetype="text/plain", as_attachment=True,
                     download_name=f"{name}.txt")

# --- ROUTES ---
@app.route("/")
def home_view():
    try:
        orders = Order.query.order_by(Order.id.desc()).all()
        data = [{"po": o.po_number, "customer": o.customer_name, "item": get_high_value_item_name(o.items), "total": f"{o.currency_symbol or ''} {o.total_amount}", "status": o.status} for o in orders]
        return render_template_string("""
        <style>body{font-family:sans-serif;padding:20px} table{width:100%;border-collapse:collapse;margin-top:20px} th,td{border:1px solid #ddd;padding:10px} .btn{padding:10px;background:blue;color:white;text-decoration:none}</style>
        <h1>🚀 Sales AI Manager</h1>
        <a href="/test-email" class="btn" style="background:orange">🛠️ Test Email Connection</a>
        <a href="/youtube" class="btn" style="background:red">🎬 YouTube Transcript Agent</a>
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
    except Exception as e:
        return f"<h2>Database Error: {e}</h2><p>Please refresh the page.</p>"

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
        status, messages = mail.search(None, '(UNSEEN SUBJECT "PO")')
        count = len(messages[0].split()) if messages[0] else 0
        mail.logout()
        return f"✅ <b>SUCCESS!</b><br>Filtered Search Active (Looking for Subject: 'PO')<br>Relevant Unread Emails: {count}"
    except Exception as e:
        return f"❌ <b>FAILED!</b><br>Error: {str(e)}"

# --- EMAIL WATCHER ---
def email_bot():
    while True:
        try:
            if not EMAIL_USER: time.sleep(30); continue
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL_USER, EMAIL_PASS)
            mail.select("inbox")
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
        time.sleep(30)

if os.environ.get("EMAIL_USER"):
    t = threading.Thread(target=email_bot)
    t.daemon = True
    t.start()
# --- KADASI 2 LINES-A IDHAI VECHU REPLACE PANNUNGA ---

if __name__ == "__main__":
    # Render tharra Port-a eduthukko, illana 10000 use pannu
    port = int(os.environ.get("PORT", 10000)) 
    app.run(host='0.0.0.0', port=port)

