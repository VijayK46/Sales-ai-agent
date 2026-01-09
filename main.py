import os
import google.generativeai as genai
from flask import Flask

app = Flask(__name__)

# API Key Setup
api_key = os.environ.get("GENAI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

@app.route("/")
def scan_models():
    try:
        if not api_key:
            return "<h1>❌ Error: API Key Kaanom! (Check Render Env Vars)</h1>"

        # List all available models
        output = "<h1>🤖 Unga Account-ku Available-a irukkura Models:</h1><ul>"
        
        found_any = False
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                output += f"<li>✅ <b>{m.name}</b></li>"
                found_any = True
        
        output += "</ul>"
        
        if not found_any:
            output += "<p>⚠️ Models list aagudhu, aana 'generateContent' support panra models edhuvum illa.</p>"
            
        return output

    except Exception as e:
        return f"<h1>❌ Big Error:</h1><p>{str(e)}</p>"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
