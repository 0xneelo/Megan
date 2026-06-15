"""APScheduler job wiring.

Jobs are thin: they call methods on the orchestrator, which owns the actual
behavior and the quiet-hours / ask-slot guards.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from megan.orchestrator import Orchestrator

log = logging.getLogger("megan.scheduler")


def build_scheduler(orch: Orchestrator) -> AsyncIOScheduler:
    settings = orch.settings
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    # Backlog drip — paces to the owner's throughput via the ask-slot guard.
    scheduler.add_job(
        orch.run_backlog_drip,
        IntervalTrigger(minutes=settings.backlog_drip_minutes),
        id="backlog_drip",
        max_instances=1,
        coalesce=True,
    )

    # Saved Messages sweep — conservative cadence (Phase 3).
    scheduler.add_job(
        orch.run_saved_sweep,
        IntervalTrigger(minutes=30),
        id="saved_sweep",
        max_instances=1,
        coalesce=True,
    )

    # Reminders — due/overdue Linear tasks. Frequent check, but rate-limited.
    scheduler.add_job(
        orch.run_reminders,
        IntervalTrigger(minutes=60),
        id="reminders",
        max_instances=1,
        coalesce=True,
    )

    # Morning brief — once, around work-hours start.
    scheduler.add_job(
        orch.run_morning_brief,
        CronTrigger(hour=settings.work_hours_start, minute=0),
        id="morning_brief",
        max_instances=1,
        coalesce=True,
    )

    # Read-later nudge — daily, low priority, mid-afternoon.
    scheduler.add_job(
        orch.run_read_later_nudge,
        CronTrigger(hour=15, minute=0),
        id="read_later_nudge",
        max_instances=1,
        coalesce=True,
    )

    # Obsidian vault git sync — keep cross-device history fresh.
    scheduler.add_job(
        orch.run_vault_sync,
        IntervalTrigger(minutes=20),
        id="vault_sync",
        max_instances=1,
        coalesce=True,
    )

    return scheduler
