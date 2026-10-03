"""One-way push to a Monday.com board. Postgres stays the system of record.

Idempotent: the Monday item id is stored on the opportunity, so a re-push updates the same item
instead of creating a duplicate. Without MONDAY_API_TOKEN, payloads go to out/monday_outbox.jsonl
so the mapping can be reviewed before connecting a real board.
"""
from __future__ import annotations

import json
import logging

import httpx

from .models import Opportunity

log = logging.getLogger(__name__)
API_URL = "https://api.monday.com/v2"

# Board column ids differ per board: map them here (or via env) once the board exists.
COLUMNS = {
    "solicitation": "text_sol",
    "agency": "text_agency",
    "deadline": "date_due",
    "tier": "status_tier",
    "score": "numbers_score",
    "link": "link_source",
}

CREATE = """mutation ($board: ID!, $name: String!, $values: JSON!) {
  create_item (board_id: $board, item_name: $name, column_values: $values) { id }
}"""
UPDATE = """mutation ($board: ID!, $item: ID!, $values: JSON!) {
  change_multiple_column_values (board_id: $board, item_id: $item, column_values: $values) { id }
}"""


def column_values(opp: Opportunity) -> dict:
    latest = opp.latest_score
    values = {
        COLUMNS["solicitation"]: opp.solicitation_number or "",
        COLUMNS["agency"]: opp.agency or "",
        COLUMNS["tier"]: {"label": f"Tier {latest.tier}" if latest else "Unscored"},
        COLUMNS["score"]: str(latest.score) if latest else "",
    }
    if opp.response_deadline:
        values[COLUMNS["deadline"]] = {"date": opp.response_deadline.isoformat()}
    if opp.source_links:
        values[COLUMNS["link"]] = {"url": opp.source_links[0], "text": "Source"}
    return values


def push(session, opportunities: list[Opportunity], settings) -> dict:
    stats = {"created": 0, "updated": 0, "outbox": 0, "errors": 0}
    if not (settings.monday_token and settings.monday_board_id):
        settings.out_dir.mkdir(parents=True, exist_ok=True)
        with open(settings.out_dir / "monday_outbox.jsonl", "a", encoding="utf-8") as fh:
            for opp in opportunities:
                fh.write(json.dumps({"item_name": opp.title, "item_id": opp.monday_item_id,
                                     "column_values": column_values(opp)}) + "\n")
                stats["outbox"] += 1
        return stats

    headers = {"Authorization": settings.monday_token, "API-Version": "2024-10"}
    with httpx.Client(timeout=30, headers=headers) as http:
        for opp in opportunities:
            values = json.dumps(column_values(opp))
            if opp.monday_item_id:
                query, variables = UPDATE, {"board": settings.monday_board_id, "item": opp.monday_item_id, "values": values}
            else:
                query, variables = CREATE, {"board": settings.monday_board_id, "name": opp.title[:255], "values": values}
            try:
                resp = http.post(API_URL, json={"query": query, "variables": variables})
                resp.raise_for_status()
                body = resp.json()
                if body.get("errors"):
                    raise RuntimeError(body["errors"])
                if not opp.monday_item_id:
                    opp.monday_item_id = str(body["data"]["create_item"]["id"])
                    stats["created"] += 1
                else:
                    stats["updated"] += 1
            except Exception as exc:
                log.error("monday push failed for %s: %s", opp.solicitation_number, exc)
                stats["errors"] += 1
    session.commit()
    return stats
