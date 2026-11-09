"""Tool schemas: what the model reads to decide when to call each counterparty tool."""

CP_SCREEN = {
    "name": "cp_screen",
    "description": (
        "Screen a company, vendor or email domain before paying it, emailing it a purchase order, signing with it "
        "or buying from it. Checks the DoD Section 1260H list, the FCC Covered List, Section 889, the UFLPA Entity "
        "List, the BIOSECURE Act list and OFAC sanctions, and returns each hit with its rule, severity, plain-language "
        "meaning and legal citation, plus the organization's public-record card (legal name, ownership links). "
        "Use it whenever money, a contract or a purchase order is about to go to a company you have not screened."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Company name, brand, or a domain such as hikvision.com"},
            "description": {"type": "string", "description": "What is being bought or paid for (optional)"},
            "amount": {"type": "number", "description": "USD amount of the purchase or payment (optional)"},
            "date": {"type": "string", "description": "YYYY-MM-DD the transaction happens (default today)"},
        },
        "required": ["name"],
    },
}

CP_LOOKUP = {
    "name": "cp_lookup",
    "description": (
        "Look up who really owns and controls a company from public records: legal name, record sources (SEC, "
        "FCC licensing, USAspending, Form 5500, lobbying, FEC), parent and subsidiary links followed up the chain, "
        "sanctions links and the federal rules that apply to it. Use it for due diligence on a counterparty, "
        "a landlord, a supplier or a company mentioned in research."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Company or organization name"},
            "hops": {"type": "integer", "description": "How many parent links to follow (default 3, max 5)"},
        },
        "required": ["name"],
    },
}

CP_CREDIT = {
    "name": "cp_credit",
    "description": (
        "Financial stress check for a US company financed by private credit: what every business development "
        "company (BDC) that holds its first-lien loan says the loan is worth, the lowest and highest mark, and how "
        "far lenders disagree, from SEC filings. A loan marked well below par is a counterparty that may not be "
        "around to honor a contract. Returns not-found for companies with no BDC-held loan."
    ),
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Company name"}},
        "required": ["name"],
    },
}

CP_WHO_OWNS_ADDRESS = {
    "name": "cp_who_owns_address",
    "description": (
        "Owner of record for a US street address and, when the owner is a business entity, the control group "
        "behind it, from county property rolls and state business registries. Also says whether the owner is a "
        "large institutional investor covered by the ROAD Act purchase ban. Coverage is a set of counties; the "
        "answer says when an address is outside it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "address": {"type": "string", "description": "Street address, e.g. 123 Main St"},
            "zip": {"type": "string", "description": "5-digit ZIP code"},
        },
        "required": ["address", "zip"],
    },
}

CP_BRIEF = {
    "name": "cp_brief",
    "description": (
        "One-page counterparty brief in Markdown with a source line for every finding: identity and record sources, "
        "every banned-vendor list and sanctions hit with the rule and citation, the ownership chain, private-credit "
        "marks, and what it means for a purchase, payment or contract. Use it when the user asks who owns a company, "
        "whether a vendor is safe to buy from, or for a due-diligence summary to paste into a ticket or email."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Company, vendor, brand or domain"},
            "context": {"type": "string", "description": "What the user is about to do with it (optional)"},
        },
        "required": ["name"],
    },
}

ALL = (CP_SCREEN, CP_LOOKUP, CP_CREDIT, CP_WHO_OWNS_ADDRESS, CP_BRIEF)
