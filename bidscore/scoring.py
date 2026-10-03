"""Module B: bid/no-bid scoring against the YAML rubric.

Rule criteria (NAICS, set-aside, vehicle, runway, agency, place) are computed in Python from the
structured fields. Judgment criteria (scope fit, past-performance relevance) need reading
comprehension: Claude picks one anchored level per criterion and quotes its evidence; offline,
a transparent keyword judge does the same job. The final score, tier and knockouts are always
plain arithmetic over those levels, so they are reproducible and explainable line by line.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .llm import claude_client, structured_call
from .models import Opportunity, Score
from .normalize import is_us_place, region_tokens


@dataclass
class Rubric:
    version: str
    company: dict
    tiers: dict
    criteria: list[dict]
    actions: dict

    @classmethod
    def load(cls, path: Path) -> "Rubric":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        total = sum(c["weight"] for c in data["criteria"])
        if total != 100:
            raise ValueError(f"rubric weights must sum to 100, got {total}")
        return cls(version=str(data["version"]), company=data["company"], tiers=data["tiers"],
                   criteria=data["criteria"], actions=data["actions"])

    def judgment_criteria(self) -> list[dict]:
        return [c for c in self.criteria if c["type"] == "judgment"]


@dataclass
class CriterionResult:
    level: int            # 0-100
    evidence: str
    points: float = 0.0


@dataclass
class ScoreResult:
    score: float
    tier: int
    knockout: str | None
    criteria: dict[str, CriterionResult]
    rationale: str
    action: str
    judge: str
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- rule criteria

def _text(opp: Opportunity) -> str:
    return f"{opp.title or ''}. {opp.description or ''}".lower()


def rule_naics_fit(opp, company, today) -> CriterionResult:
    core, secondary = set(company["naics"]["core"]), set(company["naics"]["secondary"])
    codes = set(opp.naics or [])
    if codes & core:
        return CriterionResult(100, f"NAICS {', '.join(sorted(codes & core))} is a core code")
    if codes & secondary:
        return CriterionResult(60, f"NAICS {', '.join(sorted(codes & secondary))} is a secondary code")
    if not codes:
        return CriterionResult(50, "No NAICS code in the alert; verify in the solicitation")
    return CriterionResult(0, f"NAICS {', '.join(sorted(codes))} is outside the company's codes")


def rule_set_aside(opp, company, today) -> CriterionResult:
    code = opp.set_aside
    certs = set(company["certifications"])
    if code is None:
        return CriterionResult(50, "Set-aside not stated in the alert; verify")
    if code == "NONE":
        return CriterionResult(70, "Full and open: eligible, but competing with large businesses")
    if code in certs:
        return CriterionResult(100, f"{code} set-aside and the company is {code}-eligible")
    if code == "OTHER":
        return CriterionResult(50, f"Unrecognized set-aside '{opp.set_aside_raw}'; verify eligibility")
    if code == "SB" and certs & {"WOSB", "EDWOSB", "SDVOSB", "8A", "HUBZONE"}:
        return CriterionResult(100, "Small-business set-aside; company is a small business")
    return CriterionResult(0, f"{code} set-aside; company lacks {code} certification")


def rule_vehicle(opp, company, today) -> CriterionResult:
    vehicle = (opp.contract_vehicle or "OPEN MARKET").upper()
    held = [v.upper() for v in company["vehicles_held"]]
    if any(h in vehicle or vehicle in h for h in held):
        return CriterionResult(100, f"Vehicle '{opp.contract_vehicle or 'Open Market'}' is accessible")
    return CriterionResult(0, f"Requires vehicle '{opp.contract_vehicle}', which the company does not hold")


def rule_runway(opp, company, today: date) -> CriterionResult:
    if not opp.response_deadline:
        return CriterionResult(50, "No response date in the alert; verify")
    days = (opp.response_deadline - today).days
    if days < 0:
        return CriterionResult(0, f"Response date passed {-days} days ago")
    level = 100 if days >= 21 else 80 if days >= 14 else 60 if days >= 10 else 30 if days >= 5 else 0
    return CriterionResult(level, f"{days} days to respond (due {opp.response_deadline.isoformat()})")


def rule_agency(opp, company, today) -> CriterionResult:
    preferred = [a.lower() for a in company["preferred_agencies"]]
    agency = (opp.agency or "").lower()
    if agency and any(p in agency or agency in p for p in preferred):
        return CriterionResult(100, f"{opp.agency} is a target agency")
    return CriterionResult(50, f"{opp.agency or 'Unknown agency'} is not a current target agency")


def rule_place(opp, company, today) -> CriterionResult:
    tokens = region_tokens(opp.place_of_performance)
    regions = set(company["regions"])
    if tokens & regions:
        return CriterionResult(100, f"{opp.place_of_performance} is within staffed regions")
    if is_us_place(opp.place_of_performance):
        return CriterionResult(60, f"{opp.place_of_performance} needs travel or new hires")
    return CriterionResult(0, f"{opp.place_of_performance or 'Unknown location'} is outside CONUS or unstated")


RULES = {
    "naics_fit": rule_naics_fit,
    "set_aside_eligibility": rule_set_aside,
    "vehicle_access": rule_vehicle,
    "response_runway": rule_runway,
    "agency_relationship": rule_agency,
    "place_of_performance": rule_place,
}


# ---------------------------------------------------------------- judgment criteria

_STOP = {"and", "the", "for", "with", "of", "to", "a", "an", "in", "on", "support", "services", "users",
         "legacy", "existing", "new", "plus", "years"}


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9/]+", text.lower()) if w not in _STOP and len(w) > 2}


class HeuristicJudge:
    """Offline judge: keyword overlap with the capability list and past-performance entries.

    Deliberately simple and transparent. It exists so the pipeline runs and is testable without
    an API key; Claude replaces it in production because keywords miss paraphrase and context.
    """

    name = "heuristic"

    def judge(self, opp: Opportunity, rubric: Rubric) -> dict[str, CriterionResult]:
        text = _text(opp)
        hits = [cap for cap in rubric.company["capabilities"] if cap.lower() in text]
        scope_level = 100 if len(hits) >= 2 else 50 if hits else 0
        scope = CriterionResult(scope_level, f"Capability matches: {', '.join(hits)}" if hits
                                else "No listed capability appears in the title or description")

        opp_terms = _terms(text)
        best_overlap, best_entry = set(), None
        for entry in rubric.company["past_performance"]:
            overlap = opp_terms & _terms(entry)
            if len(overlap) > len(best_overlap):
                best_overlap, best_entry = overlap, entry
        pp_level = 100 if len(best_overlap) >= 3 else 50 if len(best_overlap) >= 1 else 0
        past = CriterionResult(pp_level, f"Closest past performance: '{best_entry}' (shared: {', '.join(sorted(best_overlap))})"
                               if best_entry else "No past performance shares key terms")
        return {"scope_fit": scope, "past_performance_relevance": past}


class ClaudeJudge:
    """Claude picks one anchored level per judgment criterion and quotes the evidence."""

    def __init__(self, client, settings, session):
        self.client, self.settings, self.session = client, settings, session
        self.name = settings.claude_model

    def _schema(self, rubric: Rubric) -> dict:
        properties = {}
        for c in rubric.judgment_criteria():
            properties[c["id"]] = {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "enum": sorted(int(k) for k in c["anchors"])},
                    "evidence": {"type": "string", "description": "Quote or close paraphrase from the opportunity "
                                                                  "and the company profile that justifies the level."},
                },
                "required": ["level", "evidence"],
                "additionalProperties": False,
            }
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}

    def _prompt(self, opp: Opportunity, rubric: Rubric) -> str:
        anchors = "\n".join(
            f"- {c['id']} ({c['label']}):\n" + "\n".join(f"    {lvl}: {txt}" for lvl, txt in sorted(c["anchors"].items(), reverse=True))
            for c in rubric.judgment_criteria())
        company = rubric.company
        record = {
            "title": opp.title, "agency": opp.agency, "naics": opp.naics, "set_aside": opp.set_aside_raw,
            "vehicle": opp.contract_vehicle, "place_of_performance": opp.place_of_performance,
            "description": opp.description,
        }
        return (
            "Rate this government contract opportunity for the company below on each criterion. "
            "Choose exactly one anchored level per criterion. If the evidence sits between two anchors, "
            "choose the lower one. Judge only from what is written here.\n\n"
            f"CRITERIA AND ANCHORS\n{anchors}\n\n"
            f"COMPANY CAPABILITIES\n{json.dumps(company['capabilities'])}\n\n"
            f"COMPANY PAST PERFORMANCE\n" + "\n".join(f"- {p}" for p in company["past_performance"]) + "\n\n"
            f"OPPORTUNITY\n{json.dumps(record, sort_keys=True)}"
        )

    def judge(self, opp: Opportunity, rubric: Rubric) -> dict[str, CriterionResult] | None:
        prompt = self._prompt(opp, rubric)
        key = hashlib.sha256(f"score|{self.settings.claude_model}|{rubric.version}|{prompt}".encode()).hexdigest()
        data = structured_call(self.client, self.settings, self.session, key, prompt, self._schema(rubric))
        if not data:
            return None
        return {cid: CriterionResult(int(v["level"]), v["evidence"]) for cid, v in data.items()}


def get_judge(settings, session):
    client = claude_client(settings)
    return ClaudeJudge(client, settings, session) if client else HeuristicJudge()


# ---------------------------------------------------------------- engine

def tier_for(score: float, tiers: dict) -> int:
    return 1 if score >= tiers["tier1"] else 2 if score >= tiers["tier2"] else 3


def _exclusion(opp: Opportunity, company: dict) -> str | None:
    text = _text(opp)
    for term in company.get("exclusions", []):
        if term.lower() in text:
            return f"excluded work type '{term}'"
    return None


def score_opportunity(opp: Opportunity, rubric: Rubric, judge, today: date) -> ScoreResult:
    results: dict[str, CriterionResult] = {}
    for c in rubric.criteria:
        if c["type"] == "rule":
            results[c["id"]] = RULES[c["id"]](opp, rubric.company, today)

    judge_used = judge
    judged = judge.judge(opp, rubric)
    if judged is None:  # Claude unavailable mid-run: degrade, don't fail
        judge_used = HeuristicJudge()
        judged = judge_used.judge(opp, rubric)
    results.update(judged)

    total = 0.0
    knockout = _exclusion(opp, rubric.company)
    for c in rubric.criteria:
        r = results[c["id"]]
        r.points = round(c["weight"] * r.level / 100, 2)
        total += r.points
        if c.get("knockout_if_zero") and r.level == 0 and not knockout:
            knockout = r.evidence
    if opp.response_deadline and opp.response_deadline < today and not knockout:
        knockout = "response date has passed"

    score = round(total, 1)
    tier = 3 if knockout else tier_for(score, rubric.tiers)
    if knockout:
        action = rubric.actions["knockout"].format(reason=knockout)
    else:
        action = rubric.actions[f"tier{tier}"]
        gaps = [c["label"] for c in rubric.criteria if results[c["id"]].level < 60]
        if tier == 2 and gaps:
            action += f" Gaps: {', '.join(gaps)}."

    return ScoreResult(score=score, tier=tier, knockout=knockout, criteria=results,
                       rationale=build_rationale(results, rubric, knockout), action=action, judge=judge_used.name)


def build_rationale(results: dict[str, CriterionResult], rubric: Rubric, knockout: str | None) -> str:
    labels = {c["id"]: c["label"] for c in rubric.criteria}
    weights = {c["id"]: c["weight"] for c in rubric.criteria}
    strengths = [f"{labels[k]}: {r.evidence}" for k, r in results.items() if r.level >= 80]
    gaps = sorted(((weights[k] - r.points, k, r) for k, r in results.items() if r.level < 80), reverse=True)
    parts = []
    if knockout:
        parts.append(f"Knockout: {knockout}.")
    if strengths:
        parts.append("Strengths: " + "; ".join(strengths[:3]) + ".")
    if gaps:
        parts.append("Lost points: " + "; ".join(f"{labels[k]} (-{lost:g}) {r.evidence}" for lost, k, r in gaps[:3]) + ".")
    return " ".join(parts)


def save_score(session, opp: Opportunity, result: ScoreResult, rubric: Rubric) -> Score:
    row = Score(rubric_version=rubric.version, judge=result.judge, score=result.score, tier=result.tier,
                knockout=result.knockout, rationale=result.rationale, action=result.action,
                criteria={k: {"level": v.level, "points": v.points, "evidence": v.evidence}
                          for k, v in result.criteria.items()})
    opp.scores.append(row)
    session.flush()
    return row


def needs_scoring(opp: Opportunity, rubric: Rubric) -> bool:
    """Score new records, records whose rubric changed, and records updated since the last score."""
    latest = opp.latest_score
    if latest is None or latest.rubric_version != rubric.version:
        return True
    updated, scored = opp.updated_at, latest.created_at
    if updated and scored:
        updated = updated.replace(tzinfo=None)
        scored = scored.replace(tzinfo=None)
        return updated > scored
    return False
