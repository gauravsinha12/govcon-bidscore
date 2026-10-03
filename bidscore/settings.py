"""Runtime settings, read from environment variables (or a .env file loaded by the shell).

Every integration degrades to a safe local mode when its credentials are absent, so the whole
pipeline runs end-to-end on a laptop with no accounts at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{ROOT / 'bidscore.db'}"))
    rubric_path: Path = field(default_factory=lambda: Path(_env("RUBRIC_PATH", str(ROOT / "config" / "rubric.yaml"))))

    # Mail intake: "local" reads .eml files from MAILBOX_DIR; "gmail" uses the Gmail API.
    mailbox: str = field(default_factory=lambda: _env("MAILBOX", "local"))
    mailbox_dir: Path = field(default_factory=lambda: Path(_env("MAILBOX_DIR", str(ROOT / "samples" / "emails"))))
    gmail_query: str = field(default_factory=lambda: _env("GMAIL_QUERY", "label:bid-alerts newer_than:3d"))
    gmail_credentials: Path = field(default_factory=lambda: Path(_env("GMAIL_CREDENTIALS", str(ROOT / "credentials.json"))))
    gmail_token: Path = field(default_factory=lambda: Path(_env("GMAIL_TOKEN", str(ROOT / "token.json"))))

    # LLM judge: "auto" uses Claude when credentials exist and falls back to the offline heuristic.
    judge: str = field(default_factory=lambda: _env("JUDGE", "auto"))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5-5"))
    claude_effort: str = field(default_factory=lambda: _env("CLAUDE_EFFORT", "low"))

    # Monday.com one-way push. No token -> payloads are written to out/monday_outbox.jsonl.
    monday_token: str | None = field(default_factory=lambda: _env("MONDAY_API_TOKEN"))
    monday_board_id: str | None = field(default_factory=lambda: _env("MONDAY_BOARD_ID"))

    # Digest / alerts. No webhook -> HTML is written to out/.
    slack_webhook_url: str | None = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL"))
    timezone: str = field(default_factory=lambda: _env("TZ_NAME", "America/New_York"))

    # Freeze "today" for reproducible demos and backtests (YYYY-MM-DD).
    as_of: str | None = field(default_factory=lambda: _env("AS_OF"))

    out_dir: Path = field(default_factory=lambda: Path(_env("OUT_DIR", str(ROOT / "out"))))

    def today(self) -> date:
        return date.fromisoformat(self.as_of) if self.as_of else date.today()


def get_settings() -> Settings:
    return Settings()
