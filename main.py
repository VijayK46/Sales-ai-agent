from fastapi import FastAPI, UploadFile, File
import os
from dotenv import load_dotenv
import google.generativeai as genai
from pypdf import PdfReader
import io
import json

app = FastAPI()

# --- 1. SETUP ---
# 1. Load the .env file (File-a open panni padikkum)
load_dotenv()

# 2. Get the key safely (File-la irundhu key-a edukkum)
api_key = os.getenv("api_key")

# 3. Configure Gemini
genai.configure(api_key=api_key)

# --- 2. LOAD DATABASE (Oru vaati load pannna pothum) ---
with open("products.json", "r") as f:
    product_db = json.load(f)
print(f"📦 Database Loaded: {len(product_db)} products found.")

# --- 3. HELPER: SMART SEARCH (The RAG Logic) ---
def search_catalog(order_text):
    # Customer order text-la irukura vaarthaigala vechu DB-la thedurom
    found_items = []
    
    # Simple keyword search (Real projects-la 'Vector Search' use pannuvom)
    for product in product_db:
        # Product name order-la irukka?
        # (Using simple lower case matching)
        if product["name"].lower() in order_text.lower():
            found_items.append(f"- {product['name']} (Status: {product['stock']})")
            
    if not found_items:
        return "No direct matches found in catalog."
    
    return "\n".join(found_items)

# --- 4. API ENDPOINT ---
@app.post("/analyze-order")
async def analyze_order(file: UploadFile = File(...)):
    
    # A. Read PDF
    content = await file.read()
    reader = PdfReader(io.BytesIO(content))
    pdf_text = ""
    for page in reader.pages:
        pdf_text += page.extract_text()
        
    # B. SEARCH DATABASE (Magic happens here) 🔍
    # Full catalog-a AI ku anuppama, thevaiyanatha mattum anupurom
    relevant_catalog = search_catalog(pdf_text)
    
    print(f"🔍 Found Relevant Items: \n{relevant_catalog}")

    # C. AI PROMPT
    prompt = f"""
    Act as Edmund Optics Sales Manager.
    
    This is what we found in our Warehouse for this order:
    {relevant_catalog}
    
    Customer Order Text:
    {pdf_text}
    
    Task:
    1. If the item is in our Warehouse list, confirm it.
    2. If it's NOT in the list, say it's unavailable.
    3. Draft a SHORT professional email.
    
    Return JSON: {{ "email_draft": "..." }}
    """

    # 1. Get Response
    response = model.generate_content(prompt)
    raw_text = response.text

    # 2. Cleaning (Markdown remove panrom)
    cleaned_text = raw_text.replace("```json", "").replace("```", "").strip()

    # 3. Convert String to JSON (Idhu dhaan mukkiyam!)
    try:
        # String-a Python Dictionary-a mathurom
        final_data = json.loads(cleaned_text)
    except json.JSONDecodeError:
        # Suppose JSON convert aagalana, verum text-a anuppuvom (Safety kaga)
        final_data = {"email_draft": cleaned_text}

    # 4. Return the Object (Not string)
    return {"status": "success", "ai_result": final_data}


