import json

FIELDS = (
    "automatic_renewal",
    "customer_data_training",
    "third_party_sharing",
    "minimum_commitment_days",
    "cancellation_notice_days",
    "data_retention_days",
)

TERMS = (
    "Acme Cloud Terms of Service.\n"
    "1. Term. The initial subscription term is 365 days.\n"
    "2. Renewal. The subscription does not renew automatically; renewal requires written opt-in.\n"
    "3. Cancellation. You may cancel with 30 days written notice.\n"
    "4. Training. We do not use customer content to train models.\n"
    "5. Sharing. We never share customer data with third parties.\n"
    "6. Retention. Customer data is deleted 30 days after termination.\n"
)

Q = {
    "automatic_renewal": "The subscription does not renew automatically",
    "customer_data_training": "We do not use customer content to train models",
    "third_party_sharing": "We never share customer data with third parties",
    "minimum_commitment_days": "The initial subscription term is 365 days",
    "cancellation_notice_days": "You may cancel with 30 days written notice",
    "data_retention_days": "Customer data is deleted 30 days after termination",
}

# field -> (status, value, ambiguity_code)
GOOD = {
    "automatic_renewal": ("FOUND", False, "NONE"),
    "customer_data_training": ("FOUND", False, "NONE"),
    "third_party_sharing": ("FOUND", False, "NONE"),
    "minimum_commitment_days": ("FOUND", 365, "NONE"),
    "cancellation_notice_days": ("FOUND", 30, "NONE"),
    "data_retention_days": ("FOUND", 30, "NONE"),
}


def fence(obj):
    """Mocked model text. genlayer-test auto-parses bare-JSON mock strings into Python
    objects (a real node returns text in text mode), so we return fenced prose instead."""
    return "Here is the result:\n```json\n" + json.dumps(obj) + "\n```"


def rows(spec=None, quotes=None, terms_q=None):
    spec = dict(GOOD, **(spec or {}))
    quotes = quotes or {}
    q = terms_q or Q
    out = []
    for i, f in enumerate(FIELDS):
        status, val, code = spec[f]
        bv = val if i < 3 else None
        dv = val if i >= 3 else None
        if status == "NOT_FOUND":
            bv = dv = None
        if status == "AMBIGUOUS":
            bv = dv = None
        qs = quotes.get(f, [q[f]] if status in ("FOUND", "AMBIGUOUS") else [])
        out.append([f, status, bv, dv, code, qs])
    return out


def model(spec=None, quotes=None, terms_q=None):
    """Mock LLM extraction answer in the exact fixed-position format."""
    return fence([1, rows(spec, quotes, terms_q)])


def audit(flags=None):
    return fence(flags if flags is not None else [True] * 6)


def ambiguous(code="UNDEFINED_PERIOD"):
    return ("AMBIGUOUS", None, code)


NOT_FOUND = ("NOT_FOUND", None, "NONE")
