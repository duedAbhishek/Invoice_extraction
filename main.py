import re
from datetime import datetime
from dateutil import parser as dateparser
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# ---- Step 4 (CORS) is handled right here ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # allow any website (like the Cloudflare Worker) to call us
    allow_methods=["*"],
    allow_headers=["*"],
)

class InvoiceRequest(BaseModel):
    invoice_text: str


def clean_number(raw: str):
    """Turns '2,199.00' into 2199.0"""
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_field(pattern, text, flags=re.IGNORECASE):
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def detect_currency(text):
    if re.search(r"Rs\.?|INR|₹", text):
        return "INR"
    if re.search(r"\$|USD", text):
        return "USD"
    if re.search(r"€|EUR", text):
        return "EUR"
    return None


@app.post("/extract")
def extract_invoice(req: InvoiceRequest):
    text = req.invoice_text

    # 1. Invoice number
    invoice_no = extract_field(r"Invoice\s*(?:No|Number)?[:\s]+([A-Za-z0-9\-\/]+)", text)

    # 2. Date -> convert to YYYY-MM-DD
    date_raw = extract_field(r"Date[:\s]+([0-9A-Za-z ,\-\/]+)", text)
    date_iso = None
    if date_raw:
        try:
            date_iso = dateparser.parse(date_raw, dayfirst=True, fuzzy=True).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_iso = None

    # 3. Vendor
    vendor = extract_field(r"Vendor[:\s]+(.+)", text)

    # 4. Amount = Subtotal (before tax)
    amount_raw = extract_field(r"Sub\s*[- ]?total[:\s]+Rs\.?\s*([\d,]+\.?\d*)", text)
    amount = clean_number(amount_raw)

    # 5. Tax = GST/Tax line
    tax_raw = extract_field(r"(?:GST|Tax)[^\d:]*[:\s]+Rs\.?\s*([\d,]+\.?\d*)", text)
    tax = clean_number(tax_raw)

    # 6. Currency
    currency = detect_currency(text)

    return {
        "invoice_no": invoice_no,
        "date": date_iso,
        "vendor": vendor,
        "amount": amount,
        "tax": tax,
        "currency": currency,
    }