"""Small FastAPI surface for the BD team and for Monday.com/Slack buttons to call."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from .backtest import run_backtest
from .digest import render_digest
from .models import Opportunity
from .pipeline import Pipeline

app = FastAPI(title="Govcon Bid/No-Bid", version="0.1.0")
pipeline = Pipeline()


def _serialize(opp: Opportunity) -> dict:
    latest = opp.latest_score
    return {
        "id": opp.id, "solicitation_number": opp.solicitation_number, "title": opp.title, "agency": opp.agency,
        "naics": opp.naics, "set_aside": opp.set_aside, "contract_vehicle": opp.contract_vehicle,
        "response_deadline": opp.response_deadline.isoformat() if opp.response_deadline else None,
        "place_of_performance": opp.place_of_performance, "sources": [s.source for s in opp.sources],
        "source_links": opp.source_links, "conflicts": opp.conflicts, "merge_method": opp.merge_method,
        "needs_review": opp.needs_review,
        "score": None if latest is None else {
            "score": latest.score, "tier": latest.tier, "knockout": latest.knockout, "rationale": latest.rationale,
            "action": latest.action, "criteria": latest.criteria, "judge": latest.judge,
            "rubric_version": latest.rubric_version,
        },
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/opportunities")
def list_opportunities(tier: int | None = None) -> list[dict]:
    with pipeline.Session() as session:
        rows = [_serialize(o) for o in session.scalars(select(Opportunity)).all()]
    if tier is not None:
        rows = [r for r in rows if r["score"] and r["score"]["tier"] == tier]
    return sorted(rows, key=lambda r: (r["score"]["tier"] if r["score"] else 9, -(r["score"]["score"] if r["score"] else 0)))


@app.get("/opportunities/{opp_id}")
def get_opportunity(opp_id: int) -> dict:
    with pipeline.Session() as session:
        opp = session.get(Opportunity, opp_id)
        if not opp:
            raise HTTPException(404, "not found")
        return _serialize(opp)


@app.post("/run/intake")
def run_intake() -> dict:
    return pipeline.intake_and_score()


@app.get("/digest/preview", response_class=HTMLResponse)
def digest_preview() -> str:
    with pipeline.Session() as session:
        return render_digest(session, pipeline.settings, pipeline.rubric())


@app.get("/backtest")
def backtest() -> dict:
    return run_backtest(pipeline.settings)
