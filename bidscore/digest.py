"""Daily digest grouped by tier, plus same-day Tier 1 alerts. Slack if configured, HTML file otherwise."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from jinja2 import Environment, select_autoescape
from sqlalchemy import select

from .models import Opportunity

log = logging.getLogger(__name__)

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Bid digest {{ today }}</title>
<style>
 body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#1d2433;max-width:860px;margin:24px auto;padding:0 16px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#5b6475;margin:0 0 20px}
 h2{font-size:15px;margin:28px 0 8px;padding-bottom:4px;border-bottom:2px solid var(--c)}
 .t1{--c:#1a7f37} .t2{--c:#9a6700} .t3{--c:#8c959f}
 .card{border:1px solid #d8dee4;border-radius:8px;padding:12px 14px;margin:10px 0}
 .row{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
 .title{font-weight:600} .score{font-weight:700;font-variant-numeric:tabular-nums}
 .meta{color:#5b6475;font-size:13px;margin:2px 0 6px} .action{background:#f6f8fa;border-radius:6px;padding:6px 8px;font-size:13px}
 .why{font-size:13px;margin:6px 0} a{color:#0969da}
 .ko{font-size:12px;color:#cf222e;border:1px solid #cf222e;border-radius:10px;padding:0 8px}
 .flag{display:inline-block;font-size:11px;background:#fff8c5;border:1px solid #d4a72c;border-radius:10px;padding:0 6px;margin-left:6px}
</style></head><body>
<h1>Bid/No-Bid digest</h1>
<p class="sub">{{ today }} · {{ total }} open opportunities · rubric {{ rubric_version }} · judge {{ judges }}</p>
{% for tier, label in [(1, 'Tier 1: bid'), (2, 'Tier 2: review'), (3, 'Tier 3: no-bid')] %}
<h2 class="t{{ tier }}">{{ label }} ({{ groups[tier]|length }})</h2>
{% for o, s in groups[tier] %}
<div class="card">
 <div class="row"><span class="title">{{ o.title }}{% if o.needs_review %}<span class="flag">check merge</span>{% endif %}</span>
 <span class="score">{% if s.knockout %}<span class="ko">no-bid</span>{% else %}{{ '%.0f'|format(s.score) }}%{% endif %}</span></div>
 <div class="meta">{{ o.agency or 'Agency n/a' }} · {{ o.solicitation_number }} · due {{ o.response_deadline or 'n/a' }}
  · sources: {{ o.sources|map(attribute='source')|unique|join(', ') }}</div>
 <div class="why">{{ s.rationale }}</div>
 <div class="action">{{ s.action }}</div>
 {% if o.source_links %}<div class="meta">{% for link in o.source_links %}<a href="{{ link }}">source {{ loop.index }}</a> {% endfor %}</div>{% endif %}
</div>
{% endfor %}
{% endfor %}
<p class="sub">Scores are a triage aid. Every Tier 1 and Tier 2 item still needs a human read of the full solicitation.</p>
</body></html>"""

_env = Environment(autoescape=select_autoescape(["html"]))


def open_scored(session, today) -> list[tuple[Opportunity, object]]:
    rows = []
    for opp in session.scalars(select(Opportunity)).all():
        latest = opp.latest_score
        if latest is None:
            continue
        if opp.response_deadline and opp.response_deadline < today:
            continue
        rows.append((opp, latest))
    return sorted(rows, key=lambda r: (r[1].tier, -r[1].score, r[0].response_deadline or today))


def render_digest(session, settings, rubric) -> str:
    today = settings.today()
    rows = open_scored(session, today)
    groups = {1: [], 2: [], 3: []}
    for opp, score in rows:
        groups[score.tier].append((opp, score))
    judges = ", ".join(sorted({s.judge for _, s in rows})) or "n/a"
    return _env.from_string(TEMPLATE).render(today=today.isoformat(), total=len(rows), groups=groups,
                                             rubric_version=rubric.version, judges=judges)


def slack_text(rows) -> str:
    lines = []
    for tier, label in [(1, ":large_green_circle: *Tier 1: bid*"), (2, ":large_yellow_circle: *Tier 2: review*")]:
        items = [(o, s) for o, s in rows if s.tier == tier]
        if items:
            lines.append(f"{label} ({len(items)})")
            for o, s in items:
                lines.append(f"• {s.score:.0f}% <{(o.source_links or [''])[0]}|{o.title}> ({o.agency}, due {o.response_deadline}) — {s.action}")
    no_bid = sum(1 for _, s in rows if s.tier == 3)
    lines.append(f"Tier 3 no-bid: {no_bid}")
    return "\n".join(lines)


def send_digest(session, settings, rubric) -> str:
    html = render_digest(session, settings, rubric)
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    path = settings.out_dir / f"digest-{settings.today().isoformat()}.html"
    path.write_text(html, encoding="utf-8")
    if settings.slack_webhook_url:
        rows = open_scored(session, settings.today())
        httpx.post(settings.slack_webhook_url, json={"text": slack_text(rows)}, timeout=20).raise_for_status()
    return str(path)


def send_tier1_alerts(session, settings, scores) -> int:
    """Same-day alert for every new Tier 1 score that has not been alerted yet."""
    sent = 0
    for score in scores:
        if score.tier != 1 or score.alerted_at:
            continue
        opp = score.opportunity
        text = (f":rotating_light: Tier 1 opportunity ({score.score:.0f}%): {opp.title} — {opp.agency}, "
                f"due {opp.response_deadline}. {score.action}")
        if settings.slack_webhook_url:
            try:
                httpx.post(settings.slack_webhook_url, json={"text": text}, timeout=20).raise_for_status()
            except httpx.HTTPError as exc:
                log.error("tier 1 alert failed: %s", exc)
                continue
        else:
            settings.out_dir.mkdir(parents=True, exist_ok=True)
            with open(settings.out_dir / "alerts.log", "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        score.alerted_at = datetime.now(timezone.utc)
        sent += 1
    session.commit()
    return sent
