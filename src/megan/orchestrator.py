"""The orchestrator — Megan's brain.

Owns the event loop wiring: every inbound Telegram event is classified into
"answer to an open question" vs "new item to ingest", routed accordingly, and the
proactive scheduler jobs hang off the same object. It also owns the global
quiet-hours / cost-cap guards so no single subsystem can flood the owner.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from megan.config import Settings, get_settings
from megan.db.migrate import apply_schema
from megan.db.pool import close_pool
from megan.db.repository import Repository
from megan.ingest.pipeline import IngestPipeline, RawItem
from megan.ingest.transcribe import Transcriber
from megan.llm.client import ClaudeClient
from megan.monitor.ssh import AgentMonitor
from megan.routing.linear import LinearClient
from megan.routing.obsidian import ObsidianVault
from megan.scheduler.jobs import build_scheduler
from megan.telegram.userbot import InboundMessage, TelegramUserbot
from megan.triage.engine import TriageEngine

log = logging.getLogger("megan.orchestrator")

_HELP = """\
I'm Megan. Send me anything — links, voice notes, screenshots, forwards — and I'll
file it into Linear (tasks) or Obsidian (notes), asking only what I need to.

Commands:
/status   — what's in flight
/brief    — today's brief now
/agents   — summarize all dev hosts
/agent X  — summarize dev host X
/hosts    — list monitored hosts
/help     — this"""


class Orchestrator:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.repo = Repository()
        self.claude = ClaudeClient(self.settings, self.repo)
        self.transcriber = Transcriber(self.settings)
        self.pipeline = IngestPipeline(self.repo, self.claude, self.transcriber)
        self.linear = LinearClient(self.settings)
        self.obsidian = ObsidianVault(self.settings)
        self.monitor = AgentMonitor(self.settings, self.repo)
        self.userbot = TelegramUserbot(self.settings, on_message=self.on_inbound)
        self.triage = TriageEngine(
            settings=self.settings,
            repo=self.repo,
            claude=self.claude,
            linear=self.linear,
            obsidian=self.obsidian,
            send=self.userbot.send,
        )
        self.scheduler = build_scheduler(self)
        self._tz = ZoneInfo(self.settings.timezone)

    # --------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await apply_schema()
        self.obsidian.ensure_vault()
        await self._check_cost_cap()
        await self.userbot.start()
        self.scheduler.start()
        log.info("Megan is up. Backlog drip every %s min.", self.settings.backlog_drip_minutes)
        await self.userbot.send(f"{self.settings.megan_name} is online. Send me things.")
        await self.userbot.run_forever()

    async def shutdown(self) -> None:
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        await self.userbot.stop()
        await close_pool()

    # --------------------------------------------------- inbound handling
    async def on_inbound(self, msg: InboundMessage) -> None:
        # Commands first — they start with "/".
        if msg.raw_type == "text" and (msg.text or "").startswith("/"):
            await self._handle_command(msg.text or "")
            return

        # Answer to an open question? (text or transcribed voice)
        if msg.is_answerable:
            ask = await self.repo.oldest_unanswered_ask()
            if ask is not None and ask.get("inbox_id") is not None:
                answer = await self._answer_text(msg)
                if answer:
                    await self.triage.handle_owner_answer(ask, answer)
                    return

        # Otherwise: ingest as a new item.
        await self._ingest_inbound(msg)

    async def _answer_text(self, msg: InboundMessage) -> str:
        if msg.raw_type == "voice" and msg.file_path:
            return (await self.transcriber.transcribe(msg.file_path)).strip()
        return (msg.text or "").strip()

    async def _ingest_inbound(self, msg: InboundMessage) -> None:
        item = RawItem(
            source=msg.source,
            raw_type=msg.raw_type,
            text=msg.text,
            file_path=msg.file_path,
            raw_ref=msg.raw_ref,
            meta=msg.meta,
        )
        row = await self.pipeline.ingest(item)
        if row is None:
            return  # dedup hit — silently ignore

        # Acknowledge new direct items so the owner knows it landed.
        if msg.source in ("dm", "forward"):
            kind = row.get("classify_type") or "item"
            await self.userbot.send(f"Got it ({kind}). Let me sort it.")
        # Start a triage exchange if a slot is free.
        await self.triage.maybe_advance()

    # ----------------------------------------------------------- commands
    async def _handle_command(self, text: str) -> None:
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/help", "/start"):
            await self.userbot.send(_HELP)
        elif cmd == "/status":
            await self.userbot.send(await self._status_text())
        elif cmd == "/brief":
            await self.run_morning_brief(force=True)
        elif cmd == "/hosts":
            hosts = await self.repo.list_hosts()
            if not hosts:
                await self.userbot.send("No dev hosts registered yet.")
            else:
                await self.userbot.send(
                    "Monitored hosts:\n" + "\n".join(f"- {h['name']} ({h['ssh_alias']})" for h in hosts)
                )
        elif cmd == "/agent":
            if not arg:
                await self.userbot.send("Usage: /agent <host-name>")
            else:
                await self._report_agent(arg)
        elif cmd == "/agents":
            await self.run_agent_status(force=True)
        else:
            await self.userbot.send("Unknown command. /help for options.")

    async def _status_text(self) -> str:
        pending = await self.repo.count_pending()
        open_asks = await self.repo.count_open_asks()
        read_later = await self.repo.count_read_later_undigested()
        needs_attn = await self.repo.count_needs_attention()
        cost = await self.repo.month_cost_usd()
        lines = [
            f"Pending to triage: {pending}",
            f"Open questions: {open_asks}/{self.settings.max_open_asks}",
            f"Read-later queue: {read_later}",
        ]
        if needs_attn:
            lines.append(f"Couldn't read (needs attention): {needs_attn}")
        lines.append(f"Anthropic spend this month: ${cost:.2f}")
        return "\n".join(lines)

    async def _report_agent(self, host_name: str) -> None:
        result = await self.monitor.collect(host_name)
        if not result.get("ok"):
            await self.userbot.send(f"Couldn't check {host_name}: {result.get('error')}")
            return
        summary = await self.claude.summarize_agent_output(host_name, result["raw"])
        await self.userbot.send(f"{host_name}: {summary}")

    # ----------------------------------------------- scheduled behaviors
    def _now(self) -> datetime:
        return datetime.now(self._tz)

    def _in_quiet_hours(self) -> bool:
        hour = self._now().hour
        start, end = self.settings.quiet_hours_start, self.settings.quiet_hours_end
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end  # wraps midnight

    def _in_work_hours(self) -> bool:
        hour = self._now().hour
        return self.settings.work_hours_start <= hour < self.settings.work_hours_end

    async def run_backlog_drip(self) -> None:
        if self._in_quiet_hours() or not self._in_work_hours():
            return
        if await self._over_cost_cap():
            return
        started = await self.triage.maybe_advance()
        if started:
            log.info("backlog drip surfaced an item")

    async def run_saved_sweep(self) -> None:
        if await self._over_cost_cap():
            return
        last_id = int(await self.repo.kv_get("last_saved_id") or 0)
        try:
            items, new_max = await self.userbot.fetch_saved_since(last_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("saved sweep failed: %s", exc)
            return
        for msg in items:
            try:
                await self._ingest_inbound(msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("sweep ingest failed: %s", exc)
        if new_max != last_id:
            await self.repo.kv_set("last_saved_id", new_max)
        if items:
            log.info("swept %d saved messages", len(items))

    async def run_reminders(self) -> None:
        if self._in_quiet_hours():
            return
        due = await self.linear.issues_due_through_today()
        if not due:
            return
        # De-dup: only nag about a given issue once per day, so the hourly check
        # doesn't re-send the same overdue list every hour.
        today = self._now().date().isoformat()
        sent = await self.repo.kv_get("reminders_sent") or {}
        if sent.get("date") != today:
            sent = {"date": today, "ids": []}
        already = set(sent.get("ids", []))
        fresh = [i for i in due if i["identifier"] not in already]
        if not fresh:
            return
        lines = [f"- {i['identifier']} {i['title']} (due {i.get('dueDate')})" for i in fresh[:5]]
        await self.userbot.send("Due today / overdue:\n" + "\n".join(lines))
        sent["ids"] = list(already | {i["identifier"] for i in fresh})
        await self.repo.kv_set("reminders_sent", sent)

    async def run_requeue_ambiguous(self) -> None:
        """Give items previously parked as ambiguous one more triage pass."""
        n = await self.repo.requeue_ambiguous(older_than_hours=12)
        if n:
            log.info("requeued %d ambiguous item(s) for another pass", n)

    async def run_morning_brief(self, force: bool = False) -> None:
        if not force and self._in_quiet_hours():
            return
        due = await self.linear.issues_due_through_today()
        pending = await self.repo.count_pending()
        read_later = await self.repo.count_read_later_undigested()
        needs_attn = await self.repo.count_needs_attention()
        lines = ["Morning. Here's today:"]
        lines.append(f"- {len(due)} task(s) due/overdue")
        lines.append(f"- {pending} item(s) waiting to be sorted")
        lines.append(f"- {read_later} unread saved item(s)")
        if needs_attn:
            lines.append(f"- {needs_attn} item(s) I couldn't read — resend?")
        if due:
            lines.append("")
            lines += [f"  {i['identifier']} {i['title']}" for i in due[:5]]
        lines.append("\nWhere do you want to start?")
        await self.userbot.send("\n".join(lines))

    async def run_read_later_nudge(self) -> None:
        if self._in_quiet_hours():
            return
        count = await self.repo.count_read_later_undigested()
        if count < 10:
            return
        items = await self.repo.top_read_later(limit=5)
        try:
            digest = await self.claude.digest_read_later(items)
        except Exception as exc:  # noqa: BLE001
            log.warning("digest failed: %s", exc)
            digest = "\n".join(f"- {i.get('title') or i.get('url')}" for i in items)
        await self.userbot.send(
            f"You've got {count} read-later items. Top picks:\n\n{digest}"
        )

    async def run_agent_status(self, force: bool = False) -> None:
        if not force and self._in_quiet_hours():
            return
        hosts = await self.repo.list_hosts()
        if not hosts:
            if force:
                await self.userbot.send("No dev hosts registered.")
            return
        for host in hosts:
            await self._report_agent(host["name"])

    async def run_vault_sync(self) -> None:
        try:
            await self.obsidian.git_sync()
        except Exception as exc:  # noqa: BLE001
            log.debug("vault sync skipped: %s", exc)

    # ------------------------------------------------------- cost guard
    async def _over_cost_cap(self) -> bool:
        cap = self.settings.megan_monthly_cost_cap_usd
        if cap <= 0:
            return False
        return (await self.repo.month_cost_usd()) >= cap

    async def _check_cost_cap(self) -> None:
        if await self._over_cost_cap():
            log.warning("monthly Anthropic cost cap reached; proactivity paused")
