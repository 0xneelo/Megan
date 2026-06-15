"""Megan entrypoint.

    python -m megan        # or `megan` once installed

Runs the orchestrator: connects the userbot, applies the schema, starts the
scheduler, and serves until interrupted.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from megan.config import get_settings
from megan.logging_setup import configure_logging
from megan.orchestrator import Orchestrator

log = logging.getLogger("megan")


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.telegram_configured:
        log.error("Telegram is not configured. Set TELEGRAM_API_ID / TELEGRAM_API_HASH in .env")
        return
    if not settings.anthropic_configured:
        log.error("Anthropic is not configured. Set ANTHROPIC_API_KEY in .env")
        return

    orch = Orchestrator(settings)

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    runner = asyncio.create_task(orch.start())
    try:
        await asyncio.wait({runner, asyncio.create_task(stop.wait())}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        log.info("shutting down…")
        await orch.shutdown()
        runner.cancel()


def run() -> None:
    """Console-script entrypoint."""
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
