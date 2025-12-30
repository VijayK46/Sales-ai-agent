import streamlit as st
import requests

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Edmund Optics AI", page_icon="🤖", layout="wide")

# --- 2. THE UI DESIGN ---
st.title("🤖 AI Sales Agent Dashboard")
st.markdown("### Upload Purchase Order (PDF)")
st.markdown("Your AI agent will validate the order and draft an email instantly.")

# --- 3. FILE UPLOADER ---
uploaded_file = st.file_uploader("Drop your PDF here...", type=['pdf'])

# --- 4. ACTION BUTTON ---
if uploaded_file is not None:
    if st.button("🚀 Analyze Order"):
        
        with st.spinner("AI Agent is reading the file..."):
            try:
                # Namma FastAPI Server-ku anupurom
                files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")}
                
                # NOTE: Unga Server Run aaganum (http://127.0.0.1:8000)
                response = requests.post("https://sales-ai-agent-he5w.onrender.com/analyze-order", files=files)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # --- 5. DISPLAY RESULTS (Modern Look) ---
                    st.success("Analysis Complete!")
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.info("📄 File Details")
                        st.write(f"**Filename:** {data.get('filename')}")
                        st.write(f"**Status:** {data.get('status')}")
                    
                    with col2:
                        st.warning("🤖 AI Output")
                        # JSON result-a azhaga kaatum
                        st.json(data.get("ai_result"))
                        
                    # Email Draft Section
                    st.divider()
                    st.subheader("📧 Email Draft")
                    # AI Result-la irunthu text-a eduthu kaatuvom
                    # (Simple display for now)
                    st.code(str(data.get("ai_result")), language="markdown")
                    
                else:
                    st.error(f"Server Error: {response.text}")
                    
            except Exception as e:

                st.error(f"Connection Failed! Is the Backend Server running? Error: {e}")
