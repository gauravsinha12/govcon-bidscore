"""The two scheduled jobs, composed from the modules. Used by the CLI, the scheduler and the API."""
from __future__ import annotations

import logging

from sqlalchemy import select

from . import monday
from .digest import send_digest, send_tier1_alerts
from .intake import run_intake
from .mailbox import get_mailbox
from .models import Opportunity, make_session_factory
from .scoring import Rubric, get_judge, needs_scoring, save_score, score_opportunity
from .settings import Settings, get_settings

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.Session = make_session_factory(self.settings.database_url)

    def rubric(self) -> Rubric:
        return Rubric.load(self.settings.rubric_path)  # re-read every run: edits apply without a restart

    def intake_and_score(self) -> dict:
        with self.Session() as session:
            intake = run_intake(session, get_mailbox(self.settings), self.settings)
            rubric = self.rubric()
            judge = get_judge(self.settings, session)
            today = self.settings.today()
            new_scores = []
            for opp in session.scalars(select(Opportunity)).all():
                if needs_scoring(opp, rubric):
                    new_scores.append(save_score(session, opp, score_opportunity(opp, rubric, judge, today), rubric))
            session.commit()
            touched = session.scalars(select(Opportunity).where(Opportunity.id.in_(intake.touched_ids))).all() \
                if intake.touched_ids else []
            pushed = monday.push(session, list({*touched, *(s.opportunity for s in new_scores)}), self.settings)
            alerts = send_tier1_alerts(session, self.settings, new_scores)
            result = {
                "messages_seen": intake.messages_seen, "messages_new": intake.messages_new,
                "opportunities_parsed": intake.opportunities_parsed, "created": intake.created,
                "merged": intake.merged, "needs_review": intake.needs_review, "errors": intake.errors,
                "scored": len(new_scores), "judge": judge.name, "monday": pushed, "tier1_alerts": alerts,
            }
            log.info("intake+score: %s", result)
            return result

    def digest(self) -> str:
        with self.Session() as session:
            return send_digest(session, self.settings, self.rubric())
