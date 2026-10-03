from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from bidscore.backtest import run_backtest
from bidscore.intake import run_intake
from bidscore.mailbox import LocalMailbox
from bidscore.models import Opportunity, make_session_factory
from bidscore.normalize import parse_date, set_aside_code, sol_key
from bidscore.parsers import parser_for
from bidscore.scoring import HeuristicJudge, Rubric, score_opportunity
from bidscore.settings import ROOT, Settings

SAMPLES = ROOT / "samples" / "emails"
TODAY = date(2026, 10, 3)


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch) -> Settings:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("JUDGE", "heuristic")
    monkeypatch.setenv("AS_OF", TODAY.isoformat())
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    return Settings()


@pytest.fixture()
def session(settings):
    Session = make_session_factory(settings.database_url)
    with Session() as s:
        run_intake(s, LocalMailbox(SAMPLES), settings)
        yield s


# ---------------------------------------------------------------- normalization

@pytest.mark.parametrize("raw", ["70RTAC26R00000012", "70RTAC-26-R-00000012", "Sol #: 70rtac26r00000012",
                                 "70RTAC26R00000012 Amendment 0002", "70RTAC26R00000012 (Amendment 0001)"])
def test_sol_key_variants_collapse(raw):
    assert sol_key(raw) == "70RTAC26R00000012"


@pytest.mark.parametrize("raw,code", [
    ("Women-Owned Small Business (WOSB) Program Set-Aside", "WOSB"),
    ("Total Small Business Set-Aside (FAR 19.5)", "SB"),
    ("Service-Disabled Veteran-Owned Small Business (SDVOSB)", "SDVOSB"),
    ("None (Full and Open Competition)", "NONE"),
    ("Fair opportunity among EITS IDIQ holders (no set-aside)", "NONE"),
    ("8(a) Set-Aside", "8A"),
])
def test_set_aside_codes(raw, code):
    assert set_aside_code(raw) == code


def test_parse_date_formats():
    assert parse_date("Oct 27, 2026 2:00 PM EDT") == date(2026, 10, 27)
    assert parse_date("10/17/2026 04:00 PM EDT") == date(2026, 10, 17)
    assert parse_date("October 24, 2026 12:00 PM ET") == date(2026, 10, 24)


# ---------------------------------------------------------------- parsers

def test_every_sample_has_a_parser_and_required_fields():
    for msg in LocalMailbox(SAMPLES).fetch():
        parser = parser_for(msg)
        assert parser is not None, msg.subject
        parsed = parser.parse(msg)
        assert parsed, msg.subject
        for p in parsed:
            assert not p.missing_required(), (msg.subject, p.missing_required())


def test_ebuy_uses_agency_reference_as_solicitation_and_keeps_rfq_id():
    msg = next(m for m in LocalMailbox(SAMPLES).fetch() if "RFQ1798234" in m.subject)
    [p] = parser_for(msg).parse(msg)
    assert p.solicitation_number == "36C10B26Q0412"
    assert p.source_id == "RFQ1798234"
    assert p.contract_vehicle == "GSA MAS"


# ---------------------------------------------------------------- intake / merge

def test_duplicates_merge_across_sources(session):
    opps = session.scalars(select(Opportunity)).all()
    assert len(opps) == 10
    cloud = session.scalar(select(Opportunity).where(Opportunity.sol_key == "70RTAC26R00000012"))
    assert sorted(s.source for s in cloud.sources) == ["bidspeed", "govtribe"]
    assert cloud.naics == ["541512", "541519"]          # union of both sources
    assert len(cloud.source_links) == 2


def test_amendment_updates_deadline_and_keeps_history(session):
    opp = session.scalar(select(Opportunity).where(Opportunity.sol_key == "75N98026Q00145"))
    assert opp.response_deadline == date(2026, 10, 24)   # amendment moved it from Oct 20
    assert opp.title == "Enterprise Data Analytics Platform Support"
    assert opp.conflicts["response_deadline"]["kept_from_first_source"] == "2026-10-20"


def test_intake_is_idempotent(session, settings):
    report = run_intake(session, LocalMailbox(SAMPLES), settings)
    assert report.messages_new == 0
    assert len(session.scalars(select(Opportunity)).all()) == 10


# ---------------------------------------------------------------- scoring

def _score(session, key):
    rubric = Rubric.load(ROOT / "config" / "rubric.yaml")
    opp = session.scalar(select(Opportunity).where(Opportunity.sol_key == key))
    return score_opportunity(opp, rubric, HeuristicJudge(), TODAY)


def test_strong_fit_is_tier_1(session):
    result = _score(session, "70RTAC26R00000012")
    assert result.tier == 1 and result.knockout is None


def test_ineligible_set_aside_is_knocked_out(session):
    result = _score(session, "70QS0126R00000088")
    assert result.tier == 3 and "SDVOSB" in result.knockout


def test_excluded_work_and_clearance_are_knocked_out(session):
    assert _score(session, "W912DY26R0031").knockout
    assert "facility clearance" in _score(session, "FA251726R0009").knockout


def test_scoring_is_deterministic(session):
    first, second = _score(session, "70Z0G326QPS0019"), _score(session, "70Z0G326QPS0019")
    assert (first.score, first.tier, first.rationale) == (second.score, second.tier, second.rationale)


def test_rubric_weights_must_sum_to_100(tmp_path):
    bad = (ROOT / "config" / "rubric.yaml").read_text(encoding="utf-8").replace("weight: 20\n", "weight: 25\n", 1)
    path = tmp_path / "rubric.yaml"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError):
        Rubric.load(path)


def test_backtest_runs(settings):
    result = run_backtest(settings)
    assert result["n"] == 20
    assert 0 <= result["agreement_pct"] <= 100
