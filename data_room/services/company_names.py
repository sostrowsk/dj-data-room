"""
Company-name parsing and similarity scoring.

Pure text functions used for matching extracted client entities against
existing ``ClientCompany`` records (find_similar_client, confirm-clients
forms). Moved here verbatim from ``ai_agents.services.financial_formulas``
(which re-exports them) for the dj-data-room extraction.
"""

import re
from difflib import SequenceMatcher

LEGAL_FORMS = [
    ("gmbh & co. kg", "GmbH & Co. KG"),
    ("gmbh & co kg", "GmbH & Co. KG"),
    ("gmbh & co. ohg", "GmbH & Co. OHG"),
    ("gmbh & co ohg", "GmbH & Co. OHG"),
    ("gmbh + co. kg", "GmbH & Co. KG"),
    ("gmbh + co kg", "GmbH & Co. KG"),
    ("ag & co. kg", "AG & Co. KG"),
    ("ag & co kg", "AG & Co. KG"),
    ("e.kfm.", "e.Kfm."),
    ("e.kfr.", "e.Kfr."),
    ("e. k.", "e.K."),
    ("e.k.", "e.K."),
    ("ek", "e.K."),
    ("gbr", "GbR"),
    ("ohg", "OHG"),
    ("kg", "KG"),
    ("gmbh", "GmbH"),
    ("ag", "AG"),
    ("se", "SE"),
    ("ug", "UG"),
    ("ev", "e.V."),
    ("e.v.", "e.V."),
    ("mbh", "mbH"),
    ("inc.", "Inc."),
    ("inc", "Inc."),
    ("llc", "LLC"),
    ("ltd.", "Ltd."),
    ("ltd", "Ltd."),
    ("plc", "PLC"),
    ("corp.", "Corp."),
    ("corp", "Corp."),
]

CONSOLIDATION_MARKERS = ["konzern", "consolidated"]

CONSOLIDATION_PARENTHETICAL = [
    "(konzern)",
    "(consolidated)",
    "(group)",
]

OTHER_PARENTHETICAL = [
    "(parent)",
    "(einzelabschluss)",
    "(standalone)",
]


def parse_company_name(name: str) -> dict:
    if not name:
        return {"normalized_name": "", "legal_form": "", "is_consolidated": False}

    working = name.lower().strip()
    extracted_legal_form = ""
    is_consolidated = False

    for marker in CONSOLIDATION_PARENTHETICAL:
        if marker in working:
            is_consolidated = True
            working = working.replace(marker, " ")

    for marker in OTHER_PARENTHETICAL:
        working = working.replace(marker, " ")

    for marker in CONSOLIDATION_MARKERS:
        if re.search(rf"\b{re.escape(marker)}\b", working):
            is_consolidated = True
            working = re.sub(rf"\b{re.escape(marker)}\b", " ", working)

    # Clean trailing separators left after marker removal
    working = re.sub(r"[\s\-–—/|,;:]+$", "", working)

    # Collapse dotted abbreviations: "g.m.b.h." -> "gmbh", "m.p.f." -> "mpf"
    working = re.sub(r"\b((?:[a-z]\.){2,})", lambda m: m.group(1).replace(".", ""), working)

    # Normalize spacing for legal form detection:
    # "Co.KG" -> "Co. KG", "GmbH&Co." -> "GmbH & Co."
    working = re.sub(r"\.(?=[a-zA-Z])", ". ", working)
    working = re.sub(r"&(?=\S)", "& ", working)
    working = re.sub(r"(?<=\S)&", " &", working)
    working = " ".join(working.split())

    for pattern, canonical_form in LEGAL_FORMS:
        regex = rf"\s*{re.escape(pattern)}\s*$"
        if re.search(regex, working, flags=re.IGNORECASE):
            working = re.sub(regex, "", working, flags=re.IGNORECASE)
            extracted_legal_form = canonical_form
            break

    normalized_name = " ".join(working.split()).strip()

    return {
        "normalized_name": normalized_name,
        "legal_form": extracted_legal_form,
        "is_consolidated": is_consolidated,
    }


def company_names_match(name1: str, name2: str) -> bool:
    parsed1 = parse_company_name(name1)
    parsed2 = parse_company_name(name2)

    if parsed1["is_consolidated"] != parsed2["is_consolidated"]:
        return False

    if parsed1["legal_form"] and parsed2["legal_form"]:
        if parsed1["legal_form"] != parsed2["legal_form"]:
            return False

    norm1 = parsed1["normalized_name"]
    norm2 = parsed2["normalized_name"]

    if not norm1 or not norm2:
        return False

    if norm1 == norm2:
        return True

    if norm1 in norm2 or norm2 in norm1:
        len_diff = abs(len(norm1) - len(norm2))
        if len_diff <= 3:
            return True

    similarity = SequenceMatcher(None, norm1, norm2).ratio()
    return similarity >= 0.9


def company_name_similarity(name1: str, name2: str) -> float:
    parsed1 = parse_company_name(name1)
    parsed2 = parse_company_name(name2)

    if parsed1["is_consolidated"] != parsed2["is_consolidated"]:
        return 0.0

    if parsed1["legal_form"] and parsed2["legal_form"]:
        if parsed1["legal_form"] != parsed2["legal_form"]:
            return 0.0

    norm1 = parsed1["normalized_name"]
    norm2 = parsed2["normalized_name"]

    if not norm1 or not norm2:
        return 0.0

    if norm1 == norm2:
        return 1.0

    if norm1 in norm2 or norm2 in norm1:
        len_diff = abs(len(norm1) - len(norm2))
        if len_diff <= 3:
            return 0.95
        return 0.8

    return SequenceMatcher(None, norm1, norm2).ratio()
