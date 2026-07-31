import os
import json
import re
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, send_file, render_template_string, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from io import BytesIO
from datetime import datetime
import fcntl
import threading
import imaplib
import email
from email.header import decode_header
import time

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
EMAIL_WATCHER_LOCK = os.environ.get("EMAIL_WATCHER_LOCK", "/tmp/sales_ai_email_watcher.lock")
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
    url = db.Column(db.String(300), nullable=False)
    title = db.Column(db.String(300), nullable=True)
    channel = db.Column(db.String(200), nullable=True)
    duration_sec = db.Column(db.Integer, default=0)
    source = db.Column(db.String(20), nullable=True)      # captions | audio
    status = db.Column(db.String(60), default="Queued")   # Queued | <stage> | Done | Error
    error = db.Column(db.Text, nullable=True)
    transcript = db.Column(db.Text, nullable=True)
    summary = db.Column(db.Text, nullable=True)           # JSON blob
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
            # Anything else (a random PDF, a quote, an invoice) - say so instead
            # of falling off the end and reporting "None" to the user.
            return f"Skipped: Not a PO/OA/Shipping document (type: {doc_type})"
    except Exception as e:
        with app.app_context(): db.session.rollback()
        return f"Error: {str(e)}"

# --- YOUTUBE TRANSCRIPT AGENT ---
def run_transcript_job(job_id, prefer_captions=True):
    """Background worker: runs the agent and keeps the job row up to date."""
    with app.app_context():
        job = db.session.get(Transcript, job_id)
        if not job:
            return

        def progress(stage):
            with app.app_context():
                row = db.session.get(Transcript, job_id)
                if row:
                    row.status = stage
                    db.session.commit()

        try:
            result = youtube_agent.transcribe_youtube(
                job.url, prefer_captions=prefer_captions, progress=progress
            )
            job = db.session.get(Transcript, job_id)
            job.title = result.get("title")
            job.channel = result.get("channel")
            job.duration_sec = result.get("duration_sec", 0)
            job.source = result.get("source")
            job.transcript = result.get("transcript")
            job.status = "Summarising"
            db.session.commit()

            summary = youtube_agent.summarize_transcript(job.transcript, job.title)
            job = db.session.get(Transcript, job_id)
            job.summary = json.dumps(summary) if summary else None
            job.status = "Done"
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            job = db.session.get(Transcript, job_id)
            if job:
                job.status = "Error"
                job.error = str(e)
                db.session.commit()

def start_transcript_job(url, prefer_captions=True):
    """Create the job row, kick off the worker, return the new job id."""
    video_id = youtube_agent.extract_video_id(url)  # raises AgentError on junk input
    job = Transcript(video_id=video_id, url=youtube_agent.watch_url(video_id), status="Queued")
    db.session.add(job)
    db.session.commit()
    threading.Thread(target=run_transcript_job, args=(job.id, prefer_captions), daemon=True).start()
    return job.id

def load_summary(job):
    try:
        return json.loads(job.summary) if job.summary else None
    except Exception:
        return None

YT_STYLE = """
<style>
body{font-family:sans-serif;padding:20px;max-width:900px;margin:auto;color:#222}
table{width:100%;border-collapse:collapse;margin-top:20px}
th,td{border:1px solid #ddd;padding:10px;text-align:left}
.btn{padding:10px 14px;background:#1a73e8;color:#fff;text-decoration:none;border:0;border-radius:4px;cursor:pointer}
input[type=text]{padding:10px;width:60%}
pre{white-space:pre-wrap;background:#f7f7f7;padding:15px;border-radius:6px;line-height:1.5}
.badge{padding:3px 8px;border-radius:10px;font-size:12px;background:#eee}
.done{background:#d7f0d7}.err{background:#f8d7d7}.run{background:#fdf1c8}
</style>
"""

def status_class(status):
    if status == "Done": return "done"
    if status == "Error": return "err"
    return "run"

@app.route("/youtube")
def youtube_home():
    jobs = Transcript.query.order_by(Transcript.id.desc()).limit(50).all()
    return render_template_string(YT_STYLE + """
    <h1>🎧 YouTube Transcript Agent</h1>
    <a href="/">&larr; Back to Sales AI Manager</a>
    <form action="/youtube/transcribe" method="post" style="margin-top:20px">
        <input type="text" name="url" placeholder="https://www.youtube.com/watch?v=..." required>
        <button class="btn">Transcribe</button><br><br>
        <label><input type="checkbox" name="force_audio" value="1">
        Ignore captions and transcribe the audio with AI</label>
    </form>
    {% if error %}<p style="color:#c00">❌ {{ error }}</p>{% endif %}
    <table>
        <tr><th>Video</th><th>Source</th><th>Length</th><th>Status</th><th></th></tr>
        {% for j in jobs %}
        <tr>
            <td>{{ j.title or j.video_id }}<br><small>{{ j.channel or '' }}</small></td>
            <td>{{ j.source or '-' }}</td>
            <td>{{ fmt(j.duration_sec or 0) }}</td>
            <td><span class="badge {{ cls(j.status) }}">{{ j.status }}</span></td>
            <td><a href="/youtube/{{ j.id }}">Open</a></td>
        </tr>
        {% endfor %}
    </table>
    """, jobs=jobs, error=request.args.get("error"),
         fmt=youtube_agent.format_timestamp, cls=status_class)

@app.route("/youtube/transcribe", methods=["POST"])
def youtube_transcribe():
    try:
        job_id = start_transcript_job(
            request.form.get("url", ""),
            prefer_captions=not request.form.get("force_audio"),
        )
        return redirect(url_for("youtube_detail", job_id=job_id))
    except youtube_agent.AgentError as e:
        return redirect(url_for("youtube_home", error=str(e)))

@app.route("/youtube/<int:job_id>")
def youtube_detail(job_id):
    job = db.session.get(Transcript, job_id)
    if not job:
        return redirect(url_for("youtube_home", error="Transcript not found"))
    running = job.status not in ("Done", "Error")
    return render_template_string(YT_STYLE + """
    {% if running %}<meta http-equiv="refresh" content="5">{% endif %}
    <a href="/youtube">&larr; All transcripts</a>
    <h1>{{ job.title or job.video_id }}</h1>
    <p>
        <span class="badge {{ cls(job.status) }}">{{ job.status }}</span>
        {{ job.channel or '' }} · {{ fmt(job.duration_sec or 0) }}
        · <a href="{{ job.url }}" target="_blank">Watch on YouTube</a>
        {% if job.source %} · source: {{ job.source }}{% endif %}
    </p>
    {% if running %}<p>⏳ Working on it - this page refreshes every 5 seconds.</p>{% endif %}
    {% if job.error %}<p style="color:#c00">❌ {{ job.error }}</p>{% endif %}
    {% if summary %}
        <h2>Summary</h2>
        <p>{{ summary.get('summary','') }}</p>
        {% for label, key in [('Key points','key_points'), ('Action items','action_items'), ('Topics','topics')] %}
            {% if summary.get(key) %}
            <h3>{{ label }}</h3>
            <ul>{% for row in summary.get(key) %}<li>{{ row }}</li>{% endfor %}</ul>
            {% endif %}
        {% endfor %}
    {% endif %}
    {% if job.transcript %}
        <h2>Transcript <a class="btn" href="/youtube/{{ job.id }}/download">⬇ Download .txt</a></h2>
        <pre>{{ job.transcript }}</pre>
    {% endif %}
    <form action="/youtube/{{ job.id }}/delete" method="post" style="margin-top:20px">
        <button class="btn" style="background:#b00">Delete</button>
    </form>
    """, job=job, summary=load_summary(job), running=running,
         fmt=youtube_agent.format_timestamp, cls=status_class)

@app.route("/youtube/<int:job_id>/download")
def youtube_download(job_id):
    job = db.session.get(Transcript, job_id)
    if not job or not job.transcript:
        return redirect(url_for("youtube_home", error="Nothing to download yet"))
    header = f"{job.title or job.video_id}\n{job.url}\n\n"
    safe_name = re.sub(r'[^A-Za-z0-9_-]+', '_', job.title or job.video_id)[:60]
    return send_file(
        BytesIO((header + job.transcript).encode("utf-8")),
        mimetype="text/plain",
        as_attachment=True,
        download_name=f"{safe_name}.txt",
    )

@app.route("/youtube/<int:job_id>/delete", methods=["POST"])
def youtube_delete(job_id):
    job = db.session.get(Transcript, job_id)
    if job:
        db.session.delete(job)
        db.session.commit()
    return redirect(url_for("youtube_home"))

@app.route("/api/youtube/<int:job_id>")
def youtube_api(job_id):
    job = db.session.get(Transcript, job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": job.id, "video_id": job.video_id, "url": job.url, "title": job.title,
        "channel": job.channel, "duration_sec": job.duration_sec, "source": job.source,
        "status": job.status, "error": job.error, "transcript": job.transcript,
        "summary": load_summary(job),
    })

@app.route("/api/youtube", methods=["POST"])
def youtube_api_create():
    payload = request.get_json(silent=True) or request.form
    try:
        job_id = start_transcript_job(
            payload.get("url", ""),
            prefer_captions=not payload.get("force_audio"),
        )
        return jsonify({"id": job_id, "status_url": f"/api/youtube/{job_id}"}), 202
    except youtube_agent.AgentError as e:
        return jsonify({"error": str(e)}), 400

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
        <a href="/youtube" class="btn" style="background:red">🎧 YouTube Transcript Agent</a>
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

def js_alert(message):
    """Build the alert+redirect page. json.dumps quotes the message safely, so
    an error containing ' or a newline no longer breaks the whole script."""
    literal = (json.dumps(str(message), ensure_ascii=False)
               .replace("<", "\\u003c")        # cannot close the <script> tag
               .replace(" ", "\\u2028")   # valid JSON, but breaks JS strings
               .replace(" ", "\\u2029"))
    return f"<script>alert({literal});window.location.href='/'</script>"

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if f: return js_alert(process_document(f.read()))
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
        except Exception as e:
            # Keep polling, but say what went wrong - a silent 'except: pass'
            # here made a wrong password look exactly like an empty inbox.
            print(f"[email_bot] poll failed: {e}", flush=True)
        time.sleep(30)

def acquire_watcher_lock(path=EMAIL_WATCHER_LOCK):
    """Return an open file handle if this process won the inbox, else None.

    gunicorn runs 4 workers and each one imports this module, so without a lock
    all 4 poll the same mailbox every 30s and race to process the same PDFs.
    The handle is deliberately never closed - it holds the lock for the life of
    the process."""
    try:
        handle = open(path, "w")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        return None

if os.environ.get("EMAIL_USER"):
    _watcher_lock = acquire_watcher_lock()
    if _watcher_lock:
        t = threading.Thread(target=email_bot)
        t.daemon = True
        t.start()
# --- KADASI 2 LINES-A IDHAI VECHU REPLACE PANNUNGA ---

if __name__ == "__main__":
    # Render tharra Port-a eduthukko, illana 10000 use pannu
    port = int(os.environ.get("PORT", 10000)) 
    app.run(host='0.0.0.0', port=port)

