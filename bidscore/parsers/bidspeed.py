"""BidSpeed bid alerts: numbered 'BID #n' sections with 'Label: value' lines."""
from __future__ import annotations

import re

from .base import MailMessage, ParsedOpportunity, Parser, build, parse_kv_block

FIELD_MAP = {
    "title": "title",
    "sol #": "solicitation_number",
    "solicitation #": "solicitation_number",
    "issuing agency": "agency",
    "naics code(s)": "naics",
    "set-aside": "set_aside",
    "due date": "response_deadline",
    "location": "place_of_performance",
    "vehicle": "contract_vehicle",
    "description": "description",
    "link": "link",
}

AGENCY_ALIASES = {
    "DHS": "Department of Homeland Security",
    "VA": "Department of Veterans Affairs",
    "HHS": "Department of Health and Human Services",
    "DOD": "Department of Defense",
    "USCG": "Department of Homeland Security",
}


class BidSpeedParser(Parser):
    source = "bidspeed"

    def matches(self, msg: MailMessage) -> bool:
        return "bidspeed" in msg.sender.lower() or "bidspeed" in msg.subject.lower()

    def parse(self, msg: MailMessage) -> list[ParsedOpportunity]:
        sections = re.split(r"^\s*BID #\d+\s*$", msg.body_text(), flags=re.M)[1:]
        results = []
        for section in sections:
            fields = parse_kv_block(section, FIELD_MAP)
            fields.setdefault("contract_vehicle", "Open Market")
            if fields.get("agency"):
                head = re.split(r"\s+-\s+|,", fields["agency"])[0].strip()
                fields["agency"] = AGENCY_ALIASES.get(head.upper(), fields["agency"])
            results.append(build(self.source, fields))
        return results
