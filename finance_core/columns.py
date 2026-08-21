"""Hebrew source headers -> internal field names, per source layout.

Bank (ILS and FX accounts share the same layout) and credit-card sheets use
different Hebrew headers for the same concepts. These maps translate a source
row into a common set of internal keys used by schema.py.
"""

from __future__ import annotations

# Internal keys (not all are emitted to the output; see schema.OUTPUT_COLUMNS):
#   date_serial_raw, time_raw, billing_serial_raw, billing_month,
#   amount, amount_orig, currency_orig, description, detail1, detail2,
#   merchant_country, category, installment_no, installment_total,
#   reference, inst_id

BANK_MAP: dict[str, str] = {
    "תאריך הפעולה": "date_serial_raw",
    "תאריך ערך": "billing_serial_raw",
    "סכום": "amount",
    "תיאור הפעולה": "description",
    "פרטים נוספים": "detail1",
    "הערה שלי": "detail2",
    "אסמכתה": "reference",
    "ענף": "category",
    "מזהה תנועה במוסד הפיננסי": "inst_id",
    "מטבע": "account_currency",  # present only on some sheets (e.g. cash wallet)
}

CC_MAP: dict[str, str] = {
    "תאריך עסקה": "date_serial_raw",
    "שעת הרכישה": "time_raw",
    "תאריך חיוב": "billing_serial_raw",
    "חודש חיוב": "billing_month",
    "סכום חיוב": "amount",  # signed ILS billing amount
    "מטבע החיוב": "billing_currency",
    "סכום עסקה": "amount_orig",
    "מטבע עסקה": "currency_orig",
    "שם בית עסק": "description",
    "פרטים נוספים": "detail1",
    "הערה שלי": "detail2",
    "כתובת בית העסק": "merchant_country",
    "ענף": "category",
    "מספר תשלום נוכחי": "installment_no",
    "סך הכל תשלומים": "installment_total",
    "מזהה תנועה במוסד הפיננסי": "inst_id",
}

# Header signatures used by the classifier.
BANK_SIGNATURE = {"תאריך הפעולה", "סכום", "תיאור הפעולה"}
CC_SIGNATURE = {"תאריך עסקה", "סכום חיוב", "שם בית עסק"}

# Source header names (kept as constants for the taxonomy generator; the ledger
# itself does NOT carry category_group - it is reconstructed later from category
# via category_map.yaml).
CATEGORY_HEADER = "ענף"
CATEGORY_GROUP_HEADER = "קבוצת ענפים"

# Category (ענף) markers used by reconciliation.
CC_CHARGE_CATEGORY = "העברה לחברת כרטיסי אשראי"
CASH_TRANSFER_CATEGORY = "העברת מזומן"
UNCLASSIFIED = "ללא סיווג"

# Internal-transfer categories (ענף) considered for mirrored-pair annihilation.
INTERNAL_TRANSFER_CATEGORIES = {
    "העברת מזומן",
    "העברות פנימיות - שונות",
    "העברת מט\"ח",
    "העברת חיסכון",
}


def translate(row: dict[str, str], source_type: str) -> dict[str, str]:
    """Return a dict of internal keys for a raw source row."""
    mapping = CC_MAP if source_type == "credit-card" else BANK_MAP
    out: dict[str, str] = {}
    for header, value in row.items():
        key = mapping.get(header.strip())
        if key:
            out[key] = (value or "").strip()
    return out
