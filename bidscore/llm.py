"""Thin Claude wrapper: structured JSON output, refusal fallback, and a result cache.

Current Claude models no longer accept `temperature`, so run-to-run consistency comes from
three things instead: the model only picks from discrete anchored levels (never a free-form
number), every answer is cached by (model, rubric version, exact prompt), and all arithmetic
happens in Python.
"""
from __future__ import annotations

import json
import logging
import os

from .models import LlmCache

log = logging.getLogger(__name__)


def claude_client(settings):
    """Anthropic client if credentials are available, else None (offline mode)."""
    if settings.judge == "heuristic":
        return None
    has_credentials = any(os.environ.get(v) for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))
    if not has_credentials and settings.judge != "claude":
        return None
    try:
        import anthropic

        return anthropic.Anthropic()
    except Exception as exc:  # missing package or unusable credentials
        log.warning("Claude unavailable, using offline judge: %s", exc)
        return None


def structured_call(client, settings, session, cache_key: str, prompt: str, schema: dict,
                    system: str | None = None) -> dict | None:
    cached = session.get(LlmCache, cache_key)
    if cached:
        return cached.response

    import anthropic

    try:
        response = client.beta.messages.create(
            model=settings.claude_model,
            max_tokens=4000,
            system=system or "You are a careful government-contracting analyst. Follow the schema exactly.",
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": settings.claude_effort,
                           "format": {"type": "json_schema", "schema": schema}},
            # If a safety classifier declines, the API retries on a fallback model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError:
        log.warning("rate limited; will retry on the next scheduled run")
        return None
    except anthropic.APIStatusError as exc:
        log.error("Claude API error %s: %s", exc.status_code, exc.message)
        return None
    except anthropic.APIConnectionError as exc:
        log.error("Claude connection error: %s", exc)
        return None

    if response.stop_reason == "refusal":
        log.warning("request declined (%s)", getattr(response, "stop_details", None))
        return None
    text = next((block.text for block in response.content if block.type == "text"), None)
    if not text:
        return None
    data = json.loads(text)
    session.add(LlmCache(key=cache_key, model=response.model, response=data))
    session.flush()
    return data
