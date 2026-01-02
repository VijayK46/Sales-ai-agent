from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
import uvicorn
import os
import google.generativeai as genai
from dotenv import load_dotenv
import json
import pandas as pd

app = FastAPI()

# 1. SETUP
load_dotenv()
my_secret_key = os.getenv("API_KEY")
genai.configure(api_key=my_secret_key)

# 2. MODEL SETUP (Free Tier)
model = genai.GenerativeModel('gemini-flash-latest')

# Excel File Name
EXCEL_FILE = "sales_report_advanced.xlsx"

@app.get("/")
def home():
    return {"message": "Advanced Sales AI Agent is Live!", "status": "Running"}

# --- EXCEL SAVE FUNCTION ---
def save_to_excel(data):
    try:
        # Neenga ketta Columns-a inga define panrom
        new_row = {
            "PO Number": data.get("po_number", "N/A"),
            "PO Date": data.get("po_date", "N/A"),             # 🆕 Date
            "Customer Company": data.get("customer_name", "N/A"), # 🆕 Customer Name
            "Vendor Name": data.get("vendor_name", "N/A"),
            "High Value Product": data.get("highest_value_product", "N/A"), # 🆕 Costly Item Description
            "Total Amount": data.get("total_amount", "N/A")
        }
        
        # DataFrame create panrom
        df_new = pd.DataFrame([new_row])

        if os.path.exists(EXCEL_FILE):
            # File irundha, existing data-va padi
            existing_df = pd.read_excel(EXCEL_FILE)
            # Pudhu row-a seru
            updated_df = pd.concat([existing_df, df_new], ignore_index=True)
        else:
            # File illana, pudhusa uruvaakku
            updated_df = df_new
            
        # Save to Excel
        updated_df.to_excel(EXCEL_FILE, index=False)
        print("✅ Data saved with High Value Product!")
    except Exception as e:
        print(f"❌ Excel Save Error: {e}")

# --- MAIN API ---
@app.post("/analyze-order")
async def analyze_order(file: UploadFile = File(...)):
    try:
        content = await file.read()
        
        # 3. ADVANCED PROMPT (AI kitta theliva kekkurom)
        prompt = """
        Analyze this Purchase Order PDF and extract the following details into a JSON object:
        
        1. "po_number": The Purchase Order Number.
        2. "po_date": The Date of the PO (Format: DD-MM-YYYY).
        3. "customer_name": The name of the company ISSUING the PO (Buyer).
        4. "vendor_name": The name of the Seller/Vendor.
        5. "total_amount": The Grand Total amount.
        6. "highest_value_product": Look at the list of items. Identify the item with the highest total cost and provide its Description/Name.
        
        Return ONLY valid JSON. No Markdown.
        """
        
        # AI Request
        response = model.generate_content([
            {'mime_type': 'application/pdf', 'data': content},
            prompt
        ])
        
        # Cleaning Logic
        clean_text = response.text.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.replace("```json", "").replace("```", "")
        
        # Parsing
        extracted_data = json.loads(clean_text)
        
        # Save to Excel
        save_to_excel(extracted_data)
        
        return {
            "status": "success", 
            "data": extracted_data, 
            "message": "Report Updated! Download at /download-report"
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- DOWNLOAD BUTTON ---
@app.get("/download-report")
def download_report():
    if os.path.exists(EXCEL_FILE):
        return FileResponse(EXCEL_FILE, filename="Sales_Report.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        return {"error": "No report found yet."}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
