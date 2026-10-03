"""Partner IDIQ portal: HTML email with a two-column table (label | value) per task order request."""
from __future__ import annotations

import os

from bs4 import BeautifulSoup

from .base import MailMessage, ParsedOpportunity, Parser, build

FIELD_MAP = {
    "tor number": "solicitation_number",
    "title": "title",
    "vehicle": "contract_vehicle",
    "ordering agency": "agency",
    "naics": "naics",
    "competition": "set_aside",
    "proposals due": "response_deadline",
    "place of performance": "place_of_performance",
    "scope summary": "description",
}

# The partner portal's sending domain differs per client; set it in the environment.
PORTAL_DOMAIN = os.environ.get("IDIQ_PORTAL_DOMAIN", "example-prime.com")


class IdiqPortalParser(Parser):
    source = "idiq"

    def matches(self, msg: MailMessage) -> bool:
        return PORTAL_DOMAIN in msg.sender.lower() or msg.subject.startswith("[TO Portal]")

    def parse(self, msg: MailMessage) -> list[ParsedOpportunity]:
        if not msg.html:
            return []
        soup = BeautifulSoup(msg.html, "html.parser")
        results = []
        for table in soup.find_all("table"):
            fields: dict[str, str] = {}
            for row in table.find_all("tr"):
                label, value = row.find("th"), row.find("td")
                if label and value:
                    key = label.get_text(" ", strip=True).lower().rstrip(":")
                    fields[FIELD_MAP.get(key, key)] = value.get_text(" ", strip=True)
            link = soup.find("a", href=True)
            if link:
                fields["link"] = link["href"]
            if fields.get("solicitation_number"):
                results.append(build(self.source, fields))
        return results
