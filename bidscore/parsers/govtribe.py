"""GovTribe saved-search alerts: several opportunities per email, separated by a rule line.

Each block starts with the opportunity title on its own line, followed by 'Label: value' lines.
"""
from __future__ import annotations

import re

from .base import MailMessage, ParsedOpportunity, Parser, build, parse_kv_block

FIELD_MAP = {
    "solicitation number": "solicitation_number",
    "agency": "agency",
    "set aside": "set_aside",
    "naics": "naics",
    "contract vehicle": "contract_vehicle",
    "response due": "response_deadline",
    "place of performance": "place_of_performance",
    "summary": "description",
    "view opportunity": "link",
}


class GovTribeParser(Parser):
    source = "govtribe"

    def matches(self, msg: MailMessage) -> bool:
        return "govtribe" in msg.sender.lower() or msg.subject.lower().startswith("govtribe")

    def parse(self, msg: MailMessage) -> list[ParsedOpportunity]:
        body = msg.body_text()
        blocks = re.split(r"\n\s*-{5,}\s*\n", body)
        results = []
        for block in blocks:
            if "Solicitation Number:" not in block:
                continue
            lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
            # Title = the last non-field line before "Solicitation Number:".
            sol_index = next(i for i, line in enumerate(lines) if line.startswith("Solicitation Number:"))
            title_lines = [line for line in lines[:sol_index] if ":" not in line]
            title = title_lines[-1] if title_lines else None
            fields = parse_kv_block("\n".join(lines[sol_index:]), FIELD_MAP)
            if fields.get("agency"):
                fields["agency"] = fields["agency"].split(">")[0].strip()  # "Dept > Office" -> "Dept"
            results.append(build(self.source, fields, title=title))
        return results
