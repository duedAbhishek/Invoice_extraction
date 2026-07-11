import re
from dateutil import parser as dateparser
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class InvoiceRequest(BaseModel):
    invoice_text: str


def clean_number(raw: str):
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def find(patterns, text):
    """Try a list of regex patterns in order, return first match's group 1."""
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def detect_currency(text):
    if re.search(r"\bINR\b|Rs\.?|₹", text):
        return "INR"
    if re.search(r"\bUSD\b|\$", text):
        return "USD"
    if re.search(r"\bEUR\b|€", text):
        return "EUR"
    return None

def find_invoice_no(text):
    patterns = [
        r"Invoice\s*(?:No\.?|Number|#)[:\-\s]+([A-Za-z0-9\-\/]+)",  # "No" now required, not optional
        r"\bRef(?:erence)?[:\-\s]+([A-Za-z0-9\-\/]+)",
        r"\bBill\s*No[:\-\s]+([A-Za-z0-9\-\/]+)",
        r"\bInvoice[:\-\s]+([A-Za-z0-9\-\/]+)",  # last resort: bare "Invoice:"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            # Safety check: a real invoice number almost always has a digit.
            # If it's just a plain word (like "Invoice" or "Number"), skip it and try the next pattern.
            if re.search(r"\d", candidate):
                return candidate
    return None

@app.post("/extract")
def extract_invoice(req: InvoiceRequest):
    text = req.invoice_text

    # --- invoice_no: try several common labels ---
    invoice_no = find_invoice_no(text)

    # --- date: try several common labels ---
    date_raw = find([
        r"\bDate[:\-\s]+([0-9A-Za-z ,\-\/]+)",
        r"\bIssued(?:\s*(?:on|Date))?[:\-\s]+([0-9A-Za-z ,\-\/]+)",
        r"\bDated[:\-\s]+([0-9A-Za-z ,\-\/]+)",
    ], text)
    date_iso = None
    if date_raw:
        try:
            date_iso = dateparser.parse(date_raw.strip(), dayfirst=True, fuzzy=True).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_iso = None

    # --- vendor: try several common labels ---
    vendor = find([
        r"\bVendor[:\-\s]+(.+)",
        r"\bClient[:\-\s]+(.+)",
        r"\bSupplier[:\-\s]+(.+)",
        r"\bFrom[:\-\s]+(.+)",
    ], text)
    if vendor:
        vendor = vendor.split("\n")[0].strip()  # only take the first line after the label

    # --- amount (subtotal, before tax) ---
    # Key fix: [^\d]* means "skip over any non-digit junk" — dots, dashes, spaces, Rs, colons, all of it —
    # until we hit the actual number.
    amount_raw = find([
        r"Sub\s*[- ]?total[^\d]*([\d,]+\.?\d*)",
        r"\bAmount[^\d]*([\d,]+\.?\d*)",
    ], text)
    amount = clean_number(amount_raw)

    # --- tax ---
    tax_raw = find([
        r"\b(?:IGST|CGST|SGST|GST|VAT|Tax)\s*(?:\([\d.%]+\))?[^\d]*([\d,]+\.?\d*)",
    ], text)
    tax = clean_number(tax_raw)

    # --- currency ---
    currency_raw = find([r"\bCurrency[:\-\s]+([A-Za-z]+)"], text)
    currency = currency_raw.upper() if currency_raw else detect_currency(text)

    return {
        "invoice_no": invoice_no,
        "date": date_iso,
        "vendor": vendor,
        "amount": amount,
        "tax": tax,
        "currency": currency,
    }