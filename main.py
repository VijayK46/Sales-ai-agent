from fastapi import FastAPI, UploadFile, File
import uvicorn
import os
import google.generativeai as genai
from dotenv import load_dotenv
import json

app = FastAPI()

# 1. SETUP
load_dotenv()

# Render kitta irundhu Key-a edukkurom
my_secret_key = os.getenv("API_KEY")

# Google kitta Key-a kudukkurom
genai.configure(api_key=my_secret_key)

# --- 🕵️‍♂️ CHECK MODELS (Idhu dhaan mukkiyam) ---
print("========================================")
print("🔍 GOOGLE KITTA IRUKKURA MODELS LIST:")
try:
    model_list = []
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"👉 {m.name}")
            model_list.append(m.name)
    if not model_list:
        print("❌ Model List Empty-a irukku! (API Key-la prachanai)")
except Exception as e:
    print(f"❌ Error checking models: {e}")
print("========================================")
# ---------------------------------------------

# Namakku therinja oru model-a try panrom (List paatha aprom idhai maathalam)
model = genai.GenerativeModel('gemini-1.5-flash')

@app.get("/")
def home():
    return {"message": "Sales AI Agent is Live!", "models_found": model_list}

@app.post("/analyze-order")
async def analyze_order(file: UploadFile = File(...)):
    content = await file.read()
    
    # Prompt Setup
    prompt = """
    Extract the following details from this Purchase Order PDF:
    - PO Number
    - Vendor Name
    - Product Names and Quantities
    - Total Amount
    Return the output strictly in JSON format.
    """
    
    # Generate Response
    # PDF content-a Google-ku anuppurom
    response = model.generate_content([
        {'mime_type': 'application/pdf', 'data': content},
        prompt
    ])
    
    return json.loads(response.text)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
