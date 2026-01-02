from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
import uvicorn
import os
import google.generativeai as genai
from dotenv import load_dotenv
import json
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# 1. SETUP API KEY
load_dotenv()
my_secret_key = os.getenv("API_KEY")
genai.configure(api_key=my_secret_key)

# 2. SETUP GOOGLE SHEETS
# Neenga copy panna Sheet ID-a inga podunga 👇
SHEET_ID ="1geB31JE7RrrEC56s23oD5zyL-PeYRRqnZ1mKEjJMDJA"

def get_google_sheet():
    # GitHub-la upload panna JSON file peyar
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
    client = gspread.authorize(creds)
    # Open the sheet by ID
    sheet = client.open_by_key(SHEET_ID).sheet1
    return sheet

# 3. MODEL SETUP
model = genai.GenerativeModel('gemini-flash-latest')

@app.get("/")
def home():
    return {"message": "Hybrid Sales Agent (Google Sheets DB + Excel Output) is Live!"}

# --- SAVE TO GOOGLE SHEET ---
def save_to_sheet(data):
    try:
        sheet = get_google_sheet()
        
        # Prepare the Row
        row = [
            data.get("po_number", "N/A"),
            data.get("po_date", "N/A"),
            data.get("customer_name", "N/A"),
            data.get("vendor_name", "N/A"),
            data.get("highest_value_product", "N/A"),
            data.get("total_amount", "N/A")
        ]
        
        # Headers illana, First time podu
        if len(sheet.get_all_values()) == 0:
            headers = ["PO Number", "PO Date", "Customer Company", "Vendor Name", "High Value Product", "Total Amount"]
            sheet.append_row(headers)
            
        # Add Data
        sheet.append_row(row)
        print("✅ Data saved to Google Sheets!")
        
    except Exception as e:
        print(f"❌ Google Sheet Error: {e}")

# --- ANALYZE API ---
@app.post("/analyze-order")
async def analyze_order(file: UploadFile = File(...)):
    try:
        content = await file.read()
        
        prompt = """
        Analyze this Purchase Order PDF and extract:
        1. "po_number"
        2. "po_date" (Format: DD-MM-YYYY)
        3. "customer_name" (Buyer Company)
        4. "vendor_name" (Seller)
        5. "total_amount"
        6. "highest_value_product" (Name of the most expensive item)
        
        Return ONLY valid JSON.
        """
        
        response = model.generate_content([{'mime_type': 'application/pdf', 'data': content}, prompt])
        
        # Clean JSON
        clean_text = response.text.strip().replace("```json", "").replace("```", "")
        extracted_data = json.loads(clean_text)
        
        # Save to Google Sheet (Permanent Storage)
        save_to_sheet(extracted_data)
        
        return {"status": "success", "message": "Data saved to Database securely!", "data": extracted_data}

    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- DOWNLOAD EXCEL API ---
@app.get("/download-report")
def download_report():
    try:
        # 1. Google Sheet-la irundhu data edu
        sheet = get_google_sheet()
        all_data = sheet.get_all_records()
        
        # 2. DataFrame ah maathu
        df = pd.DataFrame(all_data)
        
        # 3. Excel ah convert pannu
        excel_filename = "Full_Sales_Report.xlsx"
        df.to_excel(excel_filename, index=False)
        
        # 4. Download kudu
        return FileResponse(excel_filename, filename=excel_filename, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
    except Exception as e:
        return {"error": f"Failed to download: {str(e)}"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)

