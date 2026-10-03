"""Normalization shared by every parser, so records from different sources compare cleanly."""
from __future__ import annotations

import re
from datetime import date, datetime

_AMENDMENT = re.compile(r"[\s\-_/]*(AMENDMENT|AMEND|AMD|MOD)[\s#\-_]*\d+\s*$", re.I)
_LABEL = re.compile(r"^(SOLICITATION|SOL|NOTICE ID|NOTICE|REFERENCE|REF|RFQ|RFP|NO|NUMBER|#|:|\.|\s)+", re.I)


def sol_key(raw: str | None) -> str | None:
    """Canonical solicitation key.

    "70RTAC-26-R-00000012", "70RTAC26R00000012" and "Sol #: 70rtac26r00000012 (Amendment 0002)"
    all map to "70RTAC26R00000012". Amendments keep the base number, so they merge into the
    original record instead of creating a new one.
    """
    if not raw:
        return None
    text = re.sub(r"\(.*?\)", " ", raw).strip()
    text = _AMENDMENT.sub("", text)
    text = _LABEL.sub("", text)
    key = re.sub(r"[^A-Za-z0-9]", "", text).upper()
    return key or None


_SET_ASIDES = [
    # Order matters: most specific first.
    (re.compile(r"economically disadvantaged|edwosb", re.I), "EDWOSB"),
    (re.compile(r"women[- ]owned|wosb", re.I), "WOSB"),
    (re.compile(r"service[- ]disabled|sdvosb", re.I), "SDVOSB"),
    (re.compile(r"hubzone", re.I), "HUBZONE"),
    (re.compile(r"8\s*\(\s*a\s*\)|\b8a\b", re.I), "8A"),
    (re.compile(r"full and open|unrestricted|none|n/a|not applicable|no set[- ]?aside|fair opportunity", re.I), "NONE"),
    (re.compile(r"small business|\bsb\b|total small|partial small", re.I), "SB"),
]


def set_aside_code(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    for pattern, code in _SET_ASIDES:
        if pattern.search(raw):
            return code
    return "OTHER"


def naics_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return sorted(set(re.findall(r"\b\d{6}\b", raw)))


_DATE_FORMATS = [
    "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%m/%d/%y",
]


def parse_date(raw: str | None) -> date | None:
    """Pull the date out of strings like 'Oct 24, 2026 2:00 PM EDT' or '10/17/2026 04:00 PM'."""
    if not raw:
        return None
    candidates = re.findall(
        r"[A-Za-z]{3,9}\.? \d{1,2}, \d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2} [A-Za-z]{3,9} \d{4}", raw)
    for candidate in candidates:
        candidate = candidate.replace(".", "")
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}


def region_tokens(place: str | None) -> set[str]:
    """'Washington, DC 20528' -> {'DC'}; 'Remote / Austin, TX' -> {'REMOTE', 'TX'}."""
    if not place:
        return set()
    tokens = set()
    if re.search(r"\bremote\b|telework|virtual", place, re.I):
        tokens.add("REMOTE")
    for state in re.findall(r"\b([A-Z]{2})\b", place):
        if state in _US_STATES:
            tokens.add(state)
    if re.search(r"washington,? d\.?c", place, re.I):
        tokens.add("DC")
    return tokens


def is_us_place(place: str | None) -> bool:
    return bool(region_tokens(place) - {"REMOTE"}) or bool(place and re.search(r"remote", place, re.I))


def clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
