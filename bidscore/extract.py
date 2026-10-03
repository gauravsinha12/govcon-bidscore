"""Fallback extraction with Claude for alert formats the deterministic parsers don't recognize.

Deterministic parsers stay the primary path (free, fast, testable). The LLM only runs when a
source changes its template or a new source appears, and its output goes through the same
normalization and merge code as everything else. Without credentials, the email is skipped
and the reason recorded, so nothing is silently dropped.
"""
from __future__ import annotations

import hashlib
import json
import logging

from .llm import claude_client, structured_call
from .normalize import naics_codes, parse_date
from .parsers import MailMessage, ParsedOpportunity

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "solicitation_number": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "agency": {"type": ["string", "null"]},
                    "naics": {"type": "array", "items": {"type": "string"}},
                    "set_aside": {"type": ["string", "null"]},
                    "contract_vehicle": {"type": ["string", "null"]},
                    "response_deadline": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                    "place_of_performance": {"type": ["string", "null"]},
                    "description": {"type": ["string", "null"]},
                    "link": {"type": ["string", "null"]},
                },
                "required": ["solicitation_number", "title", "agency", "naics", "set_aside", "contract_vehicle",
                             "response_deadline", "place_of_performance", "description", "link"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["opportunities"],
    "additionalProperties": False,
}

PROMPT = """Extract every government contract opportunity announced in this alert email.
Copy values exactly as printed. Use null for anything the email does not state; never infer a
solicitation number or a deadline that is not written in the email.

From: {sender}
Subject: {subject}

{body}"""


def llm_extract(msg: MailMessage, settings, session) -> list[ParsedOpportunity]:
    client = claude_client(settings)
    if client is None:
        log.warning("no parser and no LLM credentials for %s (%s)", msg.message_id, msg.subject)
        return []
    prompt = PROMPT.format(sender=msg.sender, subject=msg.subject, body=msg.body_text()[:60_000])
    cache_key = hashlib.sha256(f"extract|{settings.claude_model}|{prompt}".encode()).hexdigest()
    data = structured_call(client, settings, session, cache_key, prompt, SCHEMA)
    if not data:
        return []
    results = []
    for item in data.get("opportunities", []):
        results.append(ParsedOpportunity(
            source="llm", source_id=item.get("solicitation_number"),
            solicitation_number=item.get("solicitation_number"), title=item.get("title"),
            agency=item.get("agency"), naics=naics_codes(" ".join(item.get("naics") or [])),
            set_aside_raw=item.get("set_aside"), contract_vehicle=item.get("contract_vehicle"),
            response_deadline=parse_date(item.get("response_deadline")),
            place_of_performance=item.get("place_of_performance"), description=item.get("description"),
            link=item.get("link"), raw_fields={"llm": json.dumps(item)},
        ))
    return results
