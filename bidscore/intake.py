"""Module A: email intake -> parse -> merge duplicates -> Postgres (system of record)."""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .extract import llm_extract
from .models import Opportunity, ProcessedMessage, SourceRef
from .normalize import set_aside_code, sol_key
from .parsers import MailMessage, ParsedOpportunity, parser_for

log = logging.getLogger(__name__)

# When two sources disagree on a field, the higher-priority source wins. eBuy and the IDIQ portal
# are the contracting office's own systems; aggregators re-key the data and lag amendments.
SOURCE_PRIORITY = {"ebuy": 3, "idiq": 3, "govtribe": 2, "bidspeed": 1, "llm": 0}
MERGE_FIELDS = ["title", "agency", "set_aside_raw", "contract_vehicle", "response_deadline",
                "place_of_performance", "description"]
FUZZY_TITLE_THRESHOLD = 0.88


@dataclass
class IntakeReport:
    messages_seen: int = 0
    messages_new: int = 0
    opportunities_parsed: int = 0
    created: int = 0
    merged: int = 0
    needs_review: int = 0
    errors: list[str] = field(default_factory=list)
    touched_ids: set[int] = field(default_factory=set)


def _clean_title(title: str | None) -> str | None:
    if not title:
        return title
    return re.sub(r"\s*\((amendment|amd|mod)[^)]*\)\s*$", "", title, flags=re.I).strip()


def find_existing(session: Session, parsed: ParsedOpportunity) -> tuple[Opportunity | None, str]:
    """Match on normalized solicitation number first; fall back to title + agency + deadline."""
    key = sol_key(parsed.solicitation_number)
    if key:
        found = session.scalar(select(Opportunity).where(Opportunity.sol_key == key))
        if found:
            return found, "sol_number"
        # eBuy RFQ IDs and agency numbers can both appear: check every alias recorded on source refs.
        ref = session.scalar(select(SourceRef).where(SourceRef.source_id == parsed.solicitation_number))
        if ref:
            return ref.opportunity, "sol_number"
    if parsed.title and parsed.response_deadline:
        candidates = session.scalars(
            select(Opportunity).where(Opportunity.response_deadline == parsed.response_deadline)).all()
        for candidate in candidates:
            ratio = difflib.SequenceMatcher(None, _clean_title(candidate.title).lower(),
                                            _clean_title(parsed.title).lower()).ratio()
            same_agency = not candidate.agency or not parsed.agency or candidate.agency == parsed.agency
            if ratio >= FUZZY_TITLE_THRESHOLD and same_agency:
                return candidate, "fuzzy"
    return None, "new"


def _ts(dt) -> float:
    if dt is None:
        return 0.0
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _apply(opp: Opportunity, parsed: ParsedOpportunity, is_new: bool, newer: bool = True) -> None:
    """Fill or overwrite fields according to source priority; record disagreements."""
    best_priority = max((SOURCE_PRIORITY.get(s.source, 0) for s in opp.sources), default=-1)
    incoming_priority = SOURCE_PRIORITY.get(parsed.source, 0)
    conflicts = dict(opp.conflicts or {})
    for name in MERGE_FIELDS:
        incoming = getattr(parsed, name)
        if name == "title":
            incoming = _clean_title(incoming)
        if incoming in (None, ""):
            continue
        current = getattr(opp, name)
        if current in (None, "") or is_new:
            setattr(opp, name, incoming)
            continue
        if current != incoming:
            entry = conflicts.setdefault(name, {})
            entry.setdefault("kept_from_first_source", str(current))
            entry[parsed.source] = str(incoming)
            # Amendments (a newer alert from the same or a better source) move deadlines: take the newest.
            if incoming_priority >= best_priority or (name == "response_deadline" and newer):
                setattr(opp, name, incoming)
    opp.conflicts = conflicts
    opp.naics = sorted(set(opp.naics or []) | set(parsed.naics))
    opp.set_aside = set_aside_code(opp.set_aside_raw)
    if parsed.link and parsed.link not in (opp.source_links or []):
        opp.source_links = [*(opp.source_links or []), parsed.link]
    if not opp.solicitation_number and parsed.solicitation_number:
        opp.solicitation_number = parsed.solicitation_number
        opp.sol_key = sol_key(parsed.solicitation_number)


def upsert(session: Session, parsed: ParsedOpportunity, msg: MailMessage, report: IntakeReport) -> Opportunity:
    opp, method = find_existing(session, parsed)
    is_new = opp is None
    if is_new:
        opp = Opportunity(solicitation_number=parsed.solicitation_number, sol_key=sol_key(parsed.solicitation_number),
                          title=_clean_title(parsed.title) or "(untitled)", naics=[], source_links=[], conflicts={},
                          merge_method="new")
        session.add(opp)
        report.created += 1
    else:
        report.merged += 1
        if method == "fuzzy":
            opp.merge_method = "fuzzy"
            opp.needs_review = True  # a human confirms fuzzy merges
        elif opp.merge_method == "new":
            opp.merge_method = "sol_number"
    newer = all(_ts(msg.received_at) >= _ts(s.received_at) for s in opp.sources)
    _apply(opp, parsed, is_new, newer)
    if parsed.missing_required():
        opp.needs_review = True
    opp.sources.append(SourceRef(source=parsed.source, source_id=parsed.source_id, message_id=msg.message_id,
                                 received_at=msg.received_at, link=parsed.link,
                                 raw_fields={k: str(v) for k, v in parsed.raw_fields.items()}))
    session.flush()
    report.touched_ids.add(opp.id)
    if opp.needs_review:
        report.needs_review += 1
    return opp


def run_intake(session: Session, mailbox, settings=None) -> IntakeReport:
    report = IntakeReport()
    for msg in mailbox.fetch():
        report.messages_seen += 1
        if session.get(ProcessedMessage, msg.message_id):
            continue  # idempotent: overlapping hourly runs never double-count
        report.messages_new += 1
        parser = parser_for(msg)
        try:
            parsed_list = parser.parse(msg) if parser else []
            if not parsed_list or any(p.missing_required() for p in parsed_list):
                # Unknown or changed alert format: let the LLM extract it, or flag it for a human.
                fallback = llm_extract(msg, settings, session) if settings else []
                parsed_list = fallback or parsed_list
            for parsed in parsed_list:
                upsert(session, parsed, msg, report)
            report.opportunities_parsed += len(parsed_list)
            session.add(ProcessedMessage(message_id=msg.message_id, source=parser.source if parser else None,
                                         opportunities_found=len(parsed_list)))
            session.commit()
        except Exception as exc:  # one bad email must not stop the batch
            session.rollback()
            log.exception("intake failed for %s", msg.message_id)
            report.errors.append(f"{msg.message_id}: {exc}")
            session.add(ProcessedMessage(message_id=msg.message_id, error=str(exc)[:2000]))
            session.commit()
    return report
