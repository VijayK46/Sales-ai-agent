from fastapi import FastAPI, UploadFile, File
import uvicorn
import os
import google.generativeai as genai
from dotenv import load_dotenv
import json

app = FastAPI()

# 1. SETUP
load_dotenv()
my_secret_key = os.getenv("API_KEY")
genai.configure(api_key=my_secret_key)

# 2. MODEL SETUP (Using Standard Free Model)
model = genai.GenerativeModel('gemini-1.5-flash')

@app.get("/")
def home():
    return {"message": "Sales AI Agent is Live!", "status": "Running"}

@app.post("/analyze-order")
async def analyze_order(file: UploadFile = File(...)):
    try:
        content = await file.read()
        
        # 3. Stronger Prompt (JSON Mattum Kudu nu miratturom)
        prompt = """
        You are an AI Sales Assistant. Extract data from this Purchase Order PDF.
        
        Return ONLY a raw JSON object with these keys:
        {
            "po_number": "String",
            "vendor_name": "String",
            "items": [{"name": "String", "quantity": "Number", "price": "Number"}],
            "total_amount": "Number"
        }
        
        IMPORTANT: Do not output markdown code blocks (like ```json). Just the raw JSON string.
        """
        
        print("⏳ Sending to AI...")
        response = model.generate_content([
            {'mime_type': 'application/pdf', 'data': content},
            prompt
        ])
        
        # 4. DEBUGGING (Idhu dhaan mukkiyam)
        print(f"🤖 RAW AI RESPONSE: {response.text}") 
        
        # 5. CLEANING (Kuppaiyai neekkurom)
        clean_text = response.text.strip()
        # Markdown removal
        if clean_text.startswith("```"):
            clean_text = clean_text.replace("```json", "").replace("```", "")
        
        clean_text = clean_text.strip()
        
        # 6. PARSING
        if not clean_text:
            return {"error": "AI returned empty response. Check Logs for Safety Block."}
            
        return json.loads(clean_text)

    except Exception as e:
        print(f"❌ CRITICAL ERROR: {str(e)}")
        # Error vandhalum JSON format laye badhil tharom, so App crash aagadhu
        return {
            "status": "error", 
            "message": str(e),
            "hint": "Check Render Logs to see 'RAW AI RESPONSE'"
        }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
