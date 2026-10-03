"""Hourly intake during business hours; digest before 8:00 AM Eastern."""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .pipeline import Pipeline

log = logging.getLogger(__name__)


def build_scheduler(pipeline: Pipeline) -> BlockingScheduler:
    tz = pipeline.settings.timezone
    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(pipeline.intake_and_score, CronTrigger(day_of_week="mon-fri", hour="8-18", minute=5, timezone=tz),
                      id="intake", max_instances=1, coalesce=True, misfire_grace_time=900)
    # Run intake once more at 07:20 so the 07:30 digest includes overnight alerts.
    scheduler.add_job(pipeline.intake_and_score, CronTrigger(hour=7, minute=20, timezone=tz),
                      id="pre_digest_intake", max_instances=1, coalesce=True, misfire_grace_time=900)
    scheduler.add_job(pipeline.digest, CronTrigger(hour=7, minute=30, timezone=tz),
                      id="digest", max_instances=1, coalesce=True, misfire_grace_time=1200)
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    scheduler = build_scheduler(Pipeline())
    log.info("scheduler started: %s", [job.id for job in scheduler.get_jobs()])
    scheduler.start()
