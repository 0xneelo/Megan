"""The triage engine.

Userbots can't send Bot-API inline keyboards (only bot accounts can), so quick
answers are rendered as numbered options the owner taps/types; free-text and
voice answers are always accepted too. Switching to aiogram bot-mode later would
unlock real inline keyboards — that's the spec's documented de-risk path.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from megan.config import Settings
from megan.db.repository import Repository
from megan.llm import tools as T
from megan.llm.client import ClaudeClient
from megan.routing.linear import LinearClient
from megan.routing.obsidian import ObsidianVault

log = logging.getLogger("megan.triage")

Sender = Callable[[str], Awaitable[None]]


def format_question(question: str, suggestions: list[str] | None) -> str:
    if not suggestions:
        return question
    lines = [question, ""]
    lines += [f"{i}) {s}" for i, s in enumerate(suggestions, start=1)]
    lines.append("\n(reply with a number, or just tell me)")
    return "\n".join(lines)


def resolve_answer(text: str, suggestions: list[str] | None) -> str:
    """Map a numeric quick-reply to its suggestion; otherwise return the raw text."""
    t = (text or "").strip()
    if suggestions and t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < len(suggestions):
            return suggestions[idx]
    return t


class TriageEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        repo: Repository,
        claude: ClaudeClient,
        linear: LinearClient,
        obsidian: ObsidianVault,
        send: Sender,
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.claude = claude
        self.linear = linear
        self.obsidian = obsidian
        self.send = send
        # Serialize triage so the <=4-asks check and the ask insert can't
        # interleave (the backlog drip and an inbound message both call in).
        # Single-process correctness; multi-process needs a DB advisory lock.
        self._lock = asyncio.Lock()

    # ----------------------------------------------------- entry points
    async def maybe_advance(self) -> bool:
        """If a slot is free and an item is pending, start triaging it.

        Returns True if it started a triage exchange. Enforces the <=4 rule here,
        atomically with respect to other triage advances via the engine lock.
        """
        async with self._lock:
            if not await self.repo.has_free_ask_slot(self.settings.max_open_asks):
                return False
            item = await self.repo.next_pending_item()
            if item is None:
                return False
            await self._run_triage(item, ask=None, prior_question=None, owner_answer=None)
            return True

    async def handle_owner_answer(self, ask: dict[str, Any], answer_text: str) -> None:
        """Continue triage for the item tied to an open ask, with the owner's answer."""
        async with self._lock:
            inbox_id = ask.get("inbox_id")
            if inbox_id is None:
                await self.repo.answer_ask(ask["id"])
                return
            item = await self.repo.get_inbox(inbox_id)
            if item is None:
                await self.repo.answer_ask(ask["id"])
                return

            suggestions = ask.get("suggested_answers") or []
            resolved = resolve_answer(answer_text, suggestions)
            state = ask.get("state") or {}
            last_q = state.get("last_question") or ask.get("question")

            await self._run_triage(
                item,
                ask=ask,
                prior_question=last_q,
                owner_answer=resolved,
            )

    # ----------------------------------------------------- core loop step
    async def _run_triage(
        self,
        item: dict[str, Any],
        *,
        ask: dict[str, Any] | None,
        prior_question: str | None,
        owner_answer: str | None,
    ) -> None:
        item_text = item.get("extracted_text") or ""
        gathered: dict[str, Any] = {}
        qa: list[list[str]] = []
        if ask:
            gathered = dict(ask.get("state", {}).get("gathered", {}))
            qa = list(ask.get("state", {}).get("qa", []))
            if prior_question and owner_answer:
                qa.append([prior_question, owner_answer])
        if qa:
            gathered["qa"] = qa

        memory = await self.repo.recent_routing_memory(limit=20)
        decision = await self.claude.triage_step(
            item_text=item_text,
            routing_memory=memory,
            gathered=gathered,
            prior_question=prior_question,
            owner_answer=owner_answer,
        )
        await self._execute(item, ask, decision, qa)

    async def _execute(
        self,
        item: dict[str, Any],
        ask: dict[str, Any] | None,
        decision: dict[str, Any],
        qa: list[list[str]],
    ) -> None:
        tool = decision["tool"]
        args = decision.get("input", {})
        inbox_id = item["id"]
        summary = (item.get("extracted_text") or "")[:160]

        if tool == T.ASK_CLARIFYING_QUESTION:
            await self._ask(item, ask, args, qa)
            return

        # Any routing/terminal decision resolves the open ask (if any).
        if ask is not None:
            await self.repo.answer_ask(ask["id"])

        if tool == T.CREATE_LINEAR_TASK:
            await self._route_linear(inbox_id, summary, args)
        elif tool == T.CREATE_OBSIDIAN_NOTE:
            await self._route_obsidian(item, summary, args)
        elif tool == T.ADD_TO_READ_LATER:
            await self._route_read_later(item, summary, args)
        elif tool == T.DROP_ITEM:
            await self.repo.set_inbox_status(inbox_id, "dropped")
            log.info("dropped inbox %s: %s", inbox_id, args.get("reason"))
        elif tool == T.MARK_AMBIGUOUS:
            # Distinct status so the drip doesn't re-pick it immediately; a daily
            # job requeues ambiguous items for one more pass.
            await self.repo.set_inbox_status(inbox_id, "ambiguous")
            log.info("inbox %s marked ambiguous: %s", inbox_id, args.get("reason"))
        else:
            log.warning("unknown triage tool: %s", tool)

    # ----------------------------------------------------- tool handlers
    async def _ask(
        self,
        item: dict[str, Any],
        ask: dict[str, Any] | None,
        args: dict[str, Any],
        qa: list[list[str]],
    ) -> None:
        question = args.get("question", "What should I do with this?")
        suggestions = args.get("suggested_answers", [])
        state = {"last_question": question, "qa": qa, "gathered": {}}

        if ask is not None:
            # Continue the SAME ask slot — net open-asks unchanged.
            await self.repo.update_ask_state(ask["id"], state)
            # re-point the question by re-creating message; keep the row.
            await self._repoint_ask(ask["id"], question, suggestions)
        else:
            await self.repo.create_open_ask(
                inbox_id=item["id"],
                question=question,
                suggested_answers=suggestions,
                state=state,
            )
        await self.send(format_question(question, suggestions))

    async def _repoint_ask(
        self, ask_id: int, question: str, suggestions: list[str]
    ) -> None:
        # Update the question text + suggestions on an existing (still-open) ask,
        # so a follow-up question reuses the same slot (net open-asks unchanged).
        await self.repo.update_ask_question(ask_id, question, suggestions)

    async def _route_linear(
        self, inbox_id: int, summary: str, args: dict[str, Any]
    ) -> None:
        try:
            result = await self.linear.create_task(
                title=args.get("title", summary[:80] or "task"),
                project=args.get("project"),
                priority=args.get("priority", "none"),
                due=args.get("due"),
                description=args.get("description"),
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Linear create failed for inbox %s: %s", inbox_id, exc)
            # Reset to pending so a later drip retries it (the ask is already
            # answered, so the slot frees and the item isn't orphaned in 'asking').
            await self.repo.set_inbox_status(inbox_id, "pending")
            await self.send("Couldn't reach Linear — I'll keep this and retry.")
            return

        if not result.get("ok"):
            await self.send(
                "Linear isn't configured, so I noted the task but couldn't file it."
            )
            await self.repo.set_inbox_status(inbox_id, "routed", routed_to="linear:unfiled")
            return

        ident = result["identifier"]
        await self.repo.set_inbox_status(inbox_id, "routed", routed_to=f"linear:{ident}")
        await self.repo.add_routing_memory(
            item_summary=summary,
            decision=T.CREATE_LINEAR_TASK,
            project=args.get("project"),
            detail={"priority": args.get("priority"), "due": args.get("due")},
        )
        bits = [f"Created {ident}"]
        if args.get("project"):
            bits.append(f"in {args['project']}")
        if args.get("priority") and args["priority"] != "none":
            bits.append(args["priority"].capitalize())
        if args.get("due"):
            bits.append(f"due {args['due']}")
        await self.send(", ".join(bits) + ".")

    async def _route_obsidian(
        self, item: dict[str, Any], summary: str, args: dict[str, Any]
    ) -> None:
        body = args.get("body", "")
        if not body:
            try:
                body = await self.claude.write_note(item.get("extracted_text") or summary)
            except Exception as exc:  # noqa: BLE001
                log.warning("note write failed: %s", exc)
                body = item.get("extracted_text") or summary
        try:
            result = await self.obsidian.write_note(
                folder=args.get("folder", "Notes"),
                title=args.get("title", summary[:60] or "Note"),
                body=body,
                tags=args.get("tags"),
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Obsidian write failed: %s", exc)
            await self.repo.set_inbox_status(item["id"], "pending")
            await self.send("Couldn't write to the vault — I'll keep this and retry.")
            return

        path = result["path"]
        await self.repo.set_inbox_status(item["id"], "routed", routed_to=f"obsidian:{path}")
        await self.repo.add_routing_memory(
            item_summary=summary,
            decision=T.CREATE_OBSIDIAN_NOTE,
            detail={"folder": args.get("folder")},
        )
        await self.send(f"Done → Obsidian/{path}")

    async def _route_read_later(
        self, item: dict[str, Any], summary: str, args: dict[str, Any]
    ) -> None:
        meta = item.get("meta") or {}
        url = args.get("url") or meta.get("link_url")
        await self.repo.add_read_later(
            inbox_id=item["id"],
            url=url,
            title=args.get("title", meta.get("link_title") or summary[:80]),
            note=args.get("note"),
            topic=args.get("topic"),
        )
        await self.repo.set_inbox_status(item["id"], "routed", routed_to="readlater")
        await self.repo.add_routing_memory(
            item_summary=summary, decision=T.ADD_TO_READ_LATER, detail={"topic": args.get("topic")}
        )
        await self.send("Got it — added to your read-later.")
