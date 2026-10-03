"""CLI: python -m bidscore <command>

  run         intake + score + Monday push + Tier 1 alerts (one pass)
  digest      write today's digest (and post to Slack if configured)
  backtest    agreement with past human decisions
  schedule    start the hourly/daily scheduler
  serve       start the API on :8000
  gmail-auth  one-time Gmail OAuth consent (creates token.json)
"""
from __future__ import annotations

import json
import logging
import sys


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    command = argv[0] if argv else "run"
    if command == "run":
        from .pipeline import Pipeline
        print(json.dumps(Pipeline().intake_and_score(), indent=2, default=str))
    elif command == "digest":
        from .pipeline import Pipeline
        print(Pipeline().digest())
    elif command == "backtest":
        from .backtest import run_backtest
        from .settings import get_settings
        result = run_backtest(get_settings())
        print(f"judge={result['judge']} rubric={result['rubric_version']} n={result['n']} "
              f"threshold={result['threshold']} agreement={result['agreement_pct']}%")
        print("confusion:", json.dumps(result["confusion"]))
        print("threshold sweep (score cut-off -> agreement %):", json.dumps(result["threshold_sweep"]))
        for row in result["rows"]:
            mark = "  " if row["agree"] else "XX"
            print(f"{mark} {row['id']} {row['score']:5.1f} T{row['tier']} human={row['human']:<6} model={row['predicted']:<6} {row['title']}")
        for row in result["disagreements"]:
            print(f"\n{row['id']} {row['title']}\n   {row['rationale']}")
    elif command == "schedule":
        from .scheduler import main as schedule_main
        schedule_main()
    elif command == "serve":
        import uvicorn
        uvicorn.run("bidscore.api:app", host="127.0.0.1", port=8000)
    elif command == "gmail-auth":
        from .mailbox import GmailMailbox
        from .settings import get_settings
        GmailMailbox(get_settings()).authorize()
        print("token.json written")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
