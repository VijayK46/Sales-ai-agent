"""Tests for the purchase-order pipeline and its helpers."""

import json

import main


# --- HELPERS ---
def test_clean_float_strips_currency_and_separators():
    assert main.clean_float("$1,234.56") == 1234.56
    assert main.clean_float("₹ 500") == 500.0
    assert main.clean_float("1234") == 1234.0


def test_clean_float_never_raises():
    for value in (None, "", "n/a", [], {}):
        assert main.clean_float(value) == 0.0


def test_high_value_item_picks_the_biggest_line_total():
    items = json.dumps([
        {"name": "Cheap widget", "qty": "1", "price": "10"},
        {"name": "Pricey pump assembly unit extra words", "qty": "2", "price": "500"},
    ])
    # Quantity counts, and long names are trimmed to four words.
    assert main.get_high_value_item_name(items) == "Pricey pump assembly unit"


def test_high_value_item_handles_junk():
    assert main.get_high_value_item_name(None) == "-"
    assert main.get_high_value_item_name("") == "-"
    assert main.get_high_value_item_name("not json") == "-"
    assert main.get_high_value_item_name("[]") == "-"


# --- UPLOAD RESPONSE ---
def test_js_alert_escapes_quotes_and_newlines():
    """A message with an apostrophe used to break the whole script tag."""
    page = main.js_alert("Error: can't parse\nline 2")
    assert "can't parse" in page              # kept readable inside the JS string
    assert "\\n" in page                      # newline escaped, not literal
    assert page.count("<script>") == 1
    assert page.endswith("</script>")


def test_js_alert_cannot_close_the_script_tag():
    page = main.js_alert("</script><img src=x onerror=alert(1)>")
    assert "</script><img" not in page
    assert "\\u003c" in page


def test_upload_without_a_file_just_redirects(client):
    response = client.post("/upload", data={})
    assert response.status_code == 200
    assert "window.location.href='/'" in response.get_data(as_text=True)


def test_upload_reports_the_processing_result(client, monkeypatch):
    monkeypatch.setattr(main, "process_document", lambda data: "✅ PO Created")
    response = client.post("/upload", data={"file": (__import__("io").BytesIO(b"%PDF-"),
                                                     "po.pdf")})
    assert "✅ PO Created" in response.get_data(as_text=True)


# --- DOCUMENT PROCESSING ---
class FakeResponse:
    def __init__(self, text):
        self.text = text


def fake_model(payload):
    """Stand in for genai.GenerativeModel returning a canned JSON payload."""
    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def generate_content(self, *args, **kwargs):
            return FakeResponse(json.dumps(payload))
    return Model


def test_customer_po_is_stored(client, monkeypatch):
    monkeypatch.setattr(main.genai, "GenerativeModel", fake_model({
        "type": "CUSTOMER_PO", "po_number": "PO-1", "customer_name": "Acme",
        "currency_symbol": "$", "total_amount": "1,000.50",
        "items": [{"name": "Pump", "qty": "1", "price": "1000.50"}],
    }))

    assert main.process_document(b"%PDF-") == "✅ PO Created"
    with main.app.app_context():
        order = main.Order.query.filter_by(po_number="PO-1").first()
        assert order.customer_name == "Acme"
        assert order.total_amount == 1000.50


def test_duplicate_po_is_rejected(client, monkeypatch):
    monkeypatch.setattr(main.genai, "GenerativeModel", fake_model({
        "type": "CUSTOMER_PO", "po_number": "PO-2", "customer_name": "Acme",
        "total_amount": "5",
    }))
    assert main.process_document(b"%PDF-") == "✅ PO Created"
    assert main.process_document(b"%PDF-") == "Duplicate PO"


def test_oa_updates_the_matching_order(client, monkeypatch):
    monkeypatch.setattr(main.genai, "GenerativeModel", fake_model({
        "type": "CUSTOMER_PO", "po_number": "PO-3", "customer_name": "Acme",
        "total_amount": "5",
    }))
    main.process_document(b"%PDF-")

    monkeypatch.setattr(main.genai, "GenerativeModel", fake_model({
        "type": "OA", "po_number": "PO-3",
    }))
    assert main.process_document(b"%PDF-") == "✅ Updated: OA Received"


def test_shipping_for_an_unknown_po_is_reported(client, monkeypatch):
    monkeypatch.setattr(main.genai, "GenerativeModel", fake_model({
        "type": "SHIPPING", "po_number": "NOPE",
    }))
    assert main.process_document(b"%PDF-") == "❌ PO Not Found"


def test_document_without_a_po_number_is_skipped(client, monkeypatch):
    monkeypatch.setattr(main.genai, "GenerativeModel", fake_model({
        "type": "CUSTOMER_PO", "po_number": "",
    }))
    assert main.process_document(b"%PDF-") == "Skipped: No PO Number"


def test_unknown_document_type_reports_instead_of_returning_none(client, monkeypatch):
    """Regression: an unrecognised PDF used to alert the user with 'None'."""
    monkeypatch.setattr(main.genai, "GenerativeModel", fake_model({
        "type": "INVOICE", "po_number": "INV-9",
    }))
    result = main.process_document(b"%PDF-")
    assert result is not None
    assert "INVOICE" in result


def test_model_errors_are_returned_as_text(client, monkeypatch):
    class Exploding:
        def __init__(self, *a, **k):
            pass

        def generate_content(self, *a, **k):
            raise RuntimeError("quota exceeded")

    monkeypatch.setattr(main.genai, "GenerativeModel", Exploding)
    assert main.process_document(b"%PDF-") == "Error: quota exceeded"


def test_home_page_lists_orders(client, monkeypatch):
    monkeypatch.setattr(main.genai, "GenerativeModel", fake_model({
        "type": "CUSTOMER_PO", "po_number": "PO-77", "customer_name": "Globex",
        "currency_symbol": "$", "total_amount": "42",
        "items": [{"name": "Valve kit", "qty": "1", "price": "42"}],
    }))
    main.process_document(b"%PDF-")

    html = client.get("/").get_data(as_text=True)
    assert "PO-77" in html and "Globex" in html and "Valve kit" in html


# --- EMAIL WATCHER ---
def test_only_one_process_wins_the_watcher_lock(tmp_path):
    """gunicorn runs 4 workers; only one may poll the inbox."""
    path = str(tmp_path / "watcher.lock")
    first = main.acquire_watcher_lock(path)
    assert first is not None
    assert main.acquire_watcher_lock(path) is None      # second worker backs off
    first.close()
    assert main.acquire_watcher_lock(path) is not None  # freed on exit
