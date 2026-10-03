"""Milestone 3 harness: score past opportunities as of their decision date, compare with the human call.

Prediction rule: no knockout and score >= the Tier 2 threshold -> "bid". The report also sweeps
the threshold so the cut-off can be set from the client's own history instead of guessed.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .models import Opportunity, make_session_factory
from .normalize import set_aside_code
from .scoring import Rubric, get_judge, score_opportunity
from .settings import ROOT, Settings

DEFAULT_PATH = ROOT / "samples" / "past_decisions.json"


def _to_opportunity(item: dict) -> Opportunity:
    return Opportunity(
        solicitation_number=item["id"], title=item["title"], agency=item.get("agency"), naics=item.get("naics", []),
        set_aside_raw=item.get("set_aside_raw"), set_aside=set_aside_code(item.get("set_aside_raw")),
        contract_vehicle=item.get("contract_vehicle"),
        response_deadline=date.fromisoformat(item["response_deadline"]) if item.get("response_deadline") else None,
        place_of_performance=item.get("place_of_performance"), description=item.get("description"),
    )


def run_backtest(settings: Settings, path: Path | None = None) -> dict:
    data = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    rubric = Rubric.load(settings.rubric_path)
    Session = make_session_factory(settings.database_url)
    rows = []
    with Session() as session:
        judge = get_judge(settings, session)
        for item in data["items"]:
            opp = _to_opportunity(item)
            result = score_opportunity(opp, rubric, judge, date.fromisoformat(item["decided_on"]))
            rows.append({"id": item["id"], "title": item["title"], "human": item["decision"], "score": result.score,
                         "tier": result.tier, "knockout": result.knockout, "rationale": result.rationale})
        session.commit()

    def predict(row, threshold):
        return "bid" if not row["knockout"] and row["score"] >= threshold else "no-bid"

    threshold = rubric.tiers["tier2"]
    for row in rows:
        row["predicted"] = predict(row, threshold)
        row["agree"] = row["predicted"] == row["human"]
    agree = sum(r["agree"] for r in rows)
    matrix = {f"human_{h}__model_{p}": sum(1 for r in rows if r["human"] == h and r["predicted"] == p)
              for h in ("bid", "no-bid") for p in ("bid", "no-bid")}
    sweep = {t: round(sum(predict(r, t) == r["human"] for r in rows) / len(rows) * 100, 1) for t in range(50, 96, 5)}
    return {
        "judge": judge.name, "rubric_version": rubric.version, "n": len(rows), "threshold": threshold,
        "agreement_pct": round(agree / len(rows) * 100, 1), "confusion": matrix, "threshold_sweep": sweep,
        "disagreements": [r for r in rows if not r["agree"]], "rows": rows,
    }
