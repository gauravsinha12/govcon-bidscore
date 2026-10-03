"""GSA eBuy RFQ notifications: one RFQ per email.

eBuy has its own RFQ ID, but buying agencies also print their own solicitation number
("Reference #"). That reference number is the cross-source merge key; the RFQ ID is kept as
the source id so the eBuy link stays traceable.
"""
from __future__ import annotations

from .base import MailMessage, ParsedOpportunity, Parser, build, parse_kv_block

FIELD_MAP = {
    "rfq id": "rfq_id",
    "rfq title": "title",
    "buyer agency": "agency",
    "reference #": "solicitation_number",
    "contract vehicle": "contract_vehicle",
    "sin": "sin",
    "set-aside": "set_aside",
    "naics": "naics",
    "close date": "response_deadline",
    "delivery/place of performance": "place_of_performance",
    "description": "description",
    "log in to ebuy to view the full rfq": "link",
}


class EBuyParser(Parser):
    source = "ebuy"

    def matches(self, msg: MailMessage) -> bool:
        return "ebuy" in msg.sender.lower() or msg.subject.lower().startswith("gsa ebuy")

    def parse(self, msg: MailMessage) -> list[ParsedOpportunity]:
        fields = parse_kv_block(msg.body_text(), FIELD_MAP)
        if not fields.get("solicitation_number"):
            fields["solicitation_number"] = fields.get("rfq_id")  # no agency reference: fall back to RFQ ID
        if fields.get("agency"):
            fields["agency"] = fields["agency"].split(",")[0].strip()
        vehicle = fields.get("contract_vehicle", "")
        if vehicle.upper() in {"MAS", "SCHEDULE", "GSA SCHEDULE"}:
            fields["contract_vehicle"] = "GSA MAS"
        return [build(self.source, fields, source_id_field="rfq_id")]
