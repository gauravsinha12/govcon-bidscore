from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from bs4 import BeautifulSoup

from ..normalize import clean, naics_codes, parse_date, set_aside_code


@dataclass
class MailMessage:
    message_id: str
    sender: str
    subject: str
    received_at: datetime | None
    text: str
    html: str | None = None

    def body_text(self) -> str:
        """Plain text if the alert has it, otherwise text rendered from the HTML part."""
        if self.text and self.text.strip():
            return self.text
        if self.html:
            return BeautifulSoup(self.html, "html.parser").get_text("\n")
        return ""


@dataclass
class ParsedOpportunity:
    source: str
    source_id: str | None
    solicitation_number: str | None
    title: str | None
    agency: str | None = None
    naics: list[str] = field(default_factory=list)
    set_aside_raw: str | None = None
    contract_vehicle: str | None = None
    response_deadline: date | None = None
    place_of_performance: str | None = None
    description: str | None = None
    link: str | None = None
    raw_fields: dict = field(default_factory=dict)

    @property
    def set_aside(self) -> str | None:
        return set_aside_code(self.set_aside_raw)

    def missing_required(self) -> list[str]:
        required = {"title": self.title, "solicitation_number": self.solicitation_number,
                    "response_deadline": self.response_deadline}
        return [name for name, value in required.items() if not value]


# label (lowercased, no trailing colon) -> ParsedOpportunity field name
FieldMap = dict[str, str]


def parse_kv_block(block: str, field_map: FieldMap) -> dict[str, str]:
    """Read 'Label: value' lines. Unknown labels are kept in the result under their own name."""
    fields: dict[str, str] = {}
    current: str | None = None
    for line in block.splitlines():
        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9 #()/&.\-]{1,40}?)\s*:\s*(.*)$", line)
        if match and not match.group(1).lower().startswith("http"):
            label = match.group(1).strip().lower()
            current = field_map.get(label, label)
            fields[current] = match.group(2).strip()
        elif current and line.strip() and current == "description":
            fields[current] += " " + line.strip()  # descriptions wrap onto following lines
    return fields


def build(source: str, fields: dict[str, str], *, title: str | None = None,
          source_id_field: str | None = None, extra: Callable[[dict], dict] | None = None) -> ParsedOpportunity:
    if extra:
        fields = {**fields, **extra(fields)}
    return ParsedOpportunity(
        source=source,
        source_id=clean(fields.get(source_id_field)) if source_id_field else clean(fields.get("solicitation_number")),
        solicitation_number=clean(fields.get("solicitation_number")),
        title=clean(title or fields.get("title")),
        agency=clean(fields.get("agency")),
        naics=naics_codes(fields.get("naics")),
        set_aside_raw=clean(fields.get("set_aside")),
        contract_vehicle=clean(fields.get("contract_vehicle")),
        response_deadline=parse_date(fields.get("response_deadline")),
        place_of_performance=clean(fields.get("place_of_performance")),
        description=clean(fields.get("description")),
        link=_first_url(fields.get("link")),
        raw_fields=fields,
    )


def _first_url(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"https?://\S+", text)
    return match.group(0).rstrip(").,>") if match else None


class Parser:
    source: str = ""

    def matches(self, msg: MailMessage) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def parse(self, msg: MailMessage) -> list[ParsedOpportunity]:  # pragma: no cover - interface
        raise NotImplementedError
