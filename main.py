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


def safe_extract(func, *args, **kwargs):
    """Never let one field's bug crash the whole response."""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        print(f"Extraction error in {func.__name__}: {e}")
        return None


def clean_number(raw):
    if not raw:
        return None
    cleaned = str(raw).replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def find(patterns, text, flags=re.IGNORECASE):
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip()
    return None


# ---------------- invoice_no ----------------

def find_invoice_no(text):
    labeled_patterns = [
        r"Invoice\s*(?:No\.?|Number|#)[:\-\s]+([A-Za-z0-9\-\/]+)",
        r"\bRef(?:erence)?\.?\s*(?:No\.?|#)?[:\-\s]+([A-Za-z0-9\-\/]+)",
        r"\bBill\s*No\.?[:\-\s]+([A-Za-z0-9\-\/]+)",
        r"\bDoc(?:ument)?\s*(?:No\.?|#)?[:\-\s]+([A-Za-z0-9\-\/]+)",
        r"\bVoucher\s*(?:No\.?|#)?[:\-\s]+([A-Za-z0-9\-\/]+)",
        r"\bOrder\s*(?:ID|No\.?|#)?[:\-\s]+([A-Za-z0-9\-\/]+)",
        r"\bTxn\.?\s*(?:ID|No\.?)?[:\-\s]+([A-Za-z0-9\-\/]+)",
        r"\bInvoice[:\-\s]+([A-Za-z0-9\-\/]+)",
    ]
    for pattern in labeled_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip().rstrip(".,;:")
            if re.search(r"\d", candidate):
                return candidate

    header = "\n".join(text.split("\n")[:5])
    match = re.search(r"\b([A-Za-z]{2,5}[\-\/][A-Za-z0-9\-\/]{2,15})\b", header)
    if match:
        candidate = match.group(1).strip().rstrip(".,;:")
        if re.search(r"\d", candidate):
            return candidate

    return None


# ---------------- date ----------------

def find_date(text):
    date_raw = find([
        r"\bInvoice\s*Date[:\-\s]+([0-9A-Za-z ,\-\/]+)",
        r"\bDate[:\-\s]+([0-9A-Za-z ,\-\/]+)",
        r"\bIssued(?:\s*(?:on|Date))?[:\-\s]+([0-9A-Za-z ,\-\/]+)",
        r"\bDated[:\-\s]+([0-9A-Za-z ,\-\/]+)",
    ], text)

    if not date_raw:
        return None

    date_raw = date_raw.split("\n")[0].strip()
    # trim trailing junk words that might get swept in (e.g. "Vendor" from next line)
    date_raw = re.sub(r"[A-Za-z]{4,}$", "", date_raw).strip()
    date_raw = date_raw.rstrip(".,;:")
    if not date_raw:
        return None

    try:
        parsed = dateparser.parse(date_raw, dayfirst=True, fuzzy=True)
        if parsed is None:
            return None
        return parsed.strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError):
        return None


# ---------------- vendor ----------------

STOPWORDS_AFTER_VENDOR = {"invoice", "bill", "date", "gst", "tax", "total", "subtotal"}

def find_vendor(text):
    vendor = find([
        r"\bVendor(?:\s*Name)?[:\-\s]+(.+)",
        r"\bSupplier(?:\s*Name)?[:\-\s]+(.+)",
        r"\bSeller[:\-\s]+(.+)",
        r"\bClient[:\-\s]+(.+)",
        r"\bFrom[:\-\s]+(.+)",
        r"\bBilled\s*By[:\-\s]+(.+)",
    ], text)

    if not vendor:
        return None

    vendor = vendor.split("\n")[0].strip().rstrip(".,;:—-")
    if not vendor:
        return None
    # guard against grabbing a label word instead of a real name
    if vendor.lower() in STOPWORDS_AFTER_VENDOR:
        return None
    return vendor


# ---------------- amount (strictly subtotal / pre-tax) ----------------

def find_amount(text):
    labeled_patterns = [
        # First, look for strict Subtotal/Pre-tax words
        r"Sub\s*[- ]?total[^\d]*([\d,]+(?:\.\d{1,2})?)",
        r"\bNet\s*Amount[^\d]*([\d,]+(?:\.\d{1,2})?)",
        r"\bTaxable\s*(?:Value|Amount)[^\d]*([\d,]+(?:\.\d{1,2})?)",
        r"\bBase\s*(?:Price|Amount|Value)[^\d]*([\d,]+(?:\.\d{1,2})?)",
        r"\bPre[- ]?tax\s*(?:Amount|Value)?[^\d]*([\d,]+(?:\.\d{1,2})?)",
        r"\bAmount\s*Before\s*Tax[^\d]*([\d,]+(?:\.\d{1,2})?)",
        
        # Next fallback: Look for general "Amount" or "Price"
        r"\bAmount[^\d]*([\d,]+(?:\.\d{1,2})?)",
        r"\bPrice[^\d]*([\d,]+(?:\.\d{1,2})?)",
        
        # Last resort fallback: Look for "Total" or "Grand Total"
        r"\bGrand\s*Total[^\d]*([\d,]+(?:\.\d{1,2})?)",
        r"\bTotal[^\d]*([\d,]+(?:\.\d{1,2})?)",
    ]
    
    for pattern in labeled_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = clean_number(match.group(1))
            if value is not None:
                return value
    return None


# ---------------- tax ----------------

def find_tax(text):
    tax_amounts = []
    # Split the text line by line to search for tax keywords
    for line in text.split("\n"):
        if re.search(r"\b(?:IGST|CGST|SGST|GST|VAT|Tax)\b", line, re.IGNORECASE):
            # FIXED: Made the decimal part optional so it captures whole numbers like "500"
            match = re.search(r"([\d,]+(?:\.\d{1,2})?)(?!\s*%)", line)
            if match:
                value = clean_number(match.group(1))
                if value is not None:
                    tax_amounts.append(value)

    if not tax_amounts:
        return None
    
    # Sum up all tax lines found (e.g., if there is both CGST and SGST)
    return round(sum(tax_amounts), 2)


# ---------------- currency ----------------

def find_currency(text):
    currency_raw = find([r"\bCurrency[:\-\s]+([A-Za-z]{3})"], text)
    if currency_raw:
        return currency_raw.upper()

    if re.search(r"\bINR\b|Rs\.?\s|₹", text):
        return "INR"
    if re.search(r"\bUSD\b|\$", text):
        return "USD"
    if re.search(r"\bEUR\b|€", text):
        return "EUR"
    if re.search(r"\bGBP\b|£", text):
        return "GBP"
    return None


@app.post("/extract")
def extract_invoice(req: InvoiceRequest):
    text = req.invoice_text or ""
    
    return {
        "invoice_no": safe_extract(find_invoice_no, text),
        "date": safe_extract(find_date, text),
        "vendor": safe_extract(find_vendor, text),
        "amount": safe_extract(find_amount, text),
        "tax": safe_extract(find_tax, text),
        "currency": safe_extract(find_currency, text),
    }