@app.route("/analyze-order", methods=["POST"])
def analyze_order():
    try:
        # Check API Key
        if not api_key:
            return "❌ Error: GENAI_API_KEY is missing in Render Environment!", 500

        if "file" not in request.files:
            return "❌ Error: No file part", 400
        
        file = request.files["file"]
        if file.filename == "":
            return "❌ Error: No selected file", 400

        # --- FIX: Changing to 'gemini-pro' (Most Stable Version) ---
        model = genai.GenerativeModel("gemini-pro") 
        
        file_data = file.read()
        
        prompt = """
        Extract the following details from the PDF:
        1. PO Number
        2. Vendor Name
        3. Total Amount
        4. List of items (name, quantity, price)
        
        Return ONLY valid JSON. Format:
        {
            "po_number": "PO-123",
            "vendor_name": "ABC Corp",
            "total_amount": 1000.50,
            "items": [{"name": "Widget", "qty": 10, "price": 100}]
        }
        """

        response = model.generate_content([
            {"mime_type": "application/pdf", "data": file_data},
            prompt
        ])
        
        # Clean JSON
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        data = json.loads(raw_text)

        # Save to Database
        new_order = Order(
            po_number=data.get("po_number", "UNKNOWN"),
            vendor_name=data.get("vendor_name", "UNKNOWN"),
            total_amount=float(data.get("total_amount", 0.0)),
            items=json.dumps(data.get("items", []))
        )
        
        db.session.add(new_order)
        db.session.commit()

        # Create Excel
        df = pd.DataFrame([data])
        excel_filename = "po_data.xlsx"
        df.to_excel(excel_filename, index=False)

        return send_file(excel_filename, as_attachment=True)

    except Exception as e:
        error_message = f"❌ BIG ERROR: {str(e)}"
        print(traceback.format_exc())
        return error_message, 500