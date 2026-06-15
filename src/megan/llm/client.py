"""Anthropic client wrapper: all Claude calls, with cost accounting.

Cheap Haiku for first-pass classification; Opus for triage reasoning, vision,
note-writing, and agent summaries — exactly the split the spec calls for.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from megan.config import Settings
from megan.db.repository import Repository
from megan.llm import tools as tool_defs
from megan.persona import (
    classification_system,
    monitor_system,
    note_writer_system,
    triage_system,
    vision_system,
)

log = logging.getLogger("megan.llm")

# USD per 1M tokens (input, output). Used only for the soft monthly cost cap.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
}

_CLASSIFY_TYPES = ["task", "note", "read_later", "question", "ambiguous"]


def _cost(model: str, in_tok: int, out_tok: int) -> float:
    in_price, out_price = _PRICING.get(model, (5.0, 25.0))
    return (in_tok * in_price + out_tok * out_price) / 1_000_000


class ClaudeClient:
    def __init__(self, settings: Settings, repo: Repository) -> None:
        self.settings = settings
        self.repo = repo
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def _record(self, model: str, purpose: str, usage: Any) -> None:
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        await self.repo.record_usage(
            model=model,
            purpose=purpose,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=_cost(model, in_tok, out_tok),
        )

    # ----------------------------------------------------------- classify
    async def classify(self, text: str) -> str:
        """Fast first-pass type. Cheap model, structured output."""
        model = self.settings.megan_classify_model
        resp = await self._client.messages.create(
            model=model,
            max_tokens=256,
            system=classification_system(),
            messages=[{"role": "user", "content": text[:6000]}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": _CLASSIFY_TYPES},
                            "one_line": {"type": "string"},
                        },
                        "required": ["type"],
                        "additionalProperties": False,
                    },
                }
            },
        )
        await self._record(model, "classify", resp.usage)
        data = self._first_json(resp)
        kind = data.get("type", "ambiguous")
        return kind if kind in _CLASSIFY_TYPES else "ambiguous"

    # ------------------------------------------------------------- vision
    async def read_image(self, image_b64: str, media_type: str = "image/png") -> dict[str, Any]:
        """OCR + semantic understanding of a screenshot/image."""
        model = self.settings.megan_reasoning_model
        resp = await self._client.messages.create(
            model=model,
            max_tokens=2000,
            system=vision_system(),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": "Read this image."},
                    ],
                }
            ],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "All extracted text."},
                            "kind": {"type": "string", "description": "What the image is."},
                            "intent": {"type": "string", "description": "Likely owner intent."},
                        },
                        "required": ["text", "kind"],
                        "additionalProperties": False,
                    },
                }
            },
        )
        await self._record(model, "vision", resp.usage)
        return self._first_json(resp)

    # ------------------------------------------------------------- triage
    async def triage_step(
        self,
        *,
        item_text: str,
        routing_memory: list[dict[str, Any]],
        gathered: dict[str, Any],
        prior_question: str | None,
        owner_answer: str | None,
    ) -> dict[str, Any]:
        """Ask Claude to either route the item (call an action tool) or ask ONE question.

        Returns {"tool": name, "input": {...}}.
        """
        model = self.settings.megan_reasoning_model

        context_lines = [f"INBOX ITEM:\n{item_text[:8000]}"]
        if routing_memory:
            mem = "\n".join(
                f"- {m['item_summary'][:120]} -> {m['decision']}"
                + (f" ({m['project']})" if m.get("project") else "")
                for m in routing_memory[:15]
            )
            context_lines.append(f"\nOWNER'S RECENT ROUTING PATTERNS:\n{mem}")
        if gathered:
            context_lines.append(
                "\nALREADY GATHERED THIS SESSION:\n"
                + json.dumps(gathered, ensure_ascii=False, indent=2)
            )
        if prior_question and owner_answer:
            context_lines.append(
                f'\nYou asked: "{prior_question}"\nThe owner answered: "{owner_answer}"'
            )

        resp = await self._client.messages.create(
            model=model,
            max_tokens=1500,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=triage_system(),
            tools=tool_defs.TRIAGE_TOOLS,
            tool_choice={"type": "any"},  # force exactly one tool call
            messages=[{"role": "user", "content": "\n".join(context_lines)}],
        )
        await self._record(model, "triage", resp.usage)

        for block in resp.content:
            if block.type == "tool_use":
                return {"tool": block.name, "input": block.input}
        # Fallback: if no tool came back, ask a generic question rather than crash.
        return {
            "tool": tool_defs.ASK_CLARIFYING_QUESTION,
            "input": {
                "question": "Is this a task, a note, or something to read later?",
                "suggested_answers": ["Task", "Note", "Read later", "Drop it"],
            },
        }

    # ------------------------------------------------------- note writing
    async def write_note(self, item_text: str, hint: str = "") -> str:
        """Turn an item into a clean Obsidian markdown note body."""
        model = self.settings.megan_reasoning_model
        prompt = item_text[:12000]
        if hint:
            prompt = f"{hint}\n\n---\n\n{prompt}"
        resp = await self._client.messages.create(
            model=model,
            max_tokens=4000,
            system=note_writer_system(),
            messages=[{"role": "user", "content": prompt}],
        )
        await self._record(model, "summary", resp.usage)
        return self._first_text(resp).strip()

    # --------------------------------------------------- agent summaries
    async def summarize_agent_output(self, host: str, raw_output: str) -> str:
        model = self.settings.megan_reasoning_model
        resp = await self._client.messages.create(
            model=model,
            max_tokens=1000,
            system=monitor_system(),
            messages=[
                {
                    "role": "user",
                    "content": f"Host: {host}\n\nRaw read-only output:\n```\n{raw_output[:14000]}\n```",
                }
            ],
        )
        await self._record(model, "monitor", resp.usage)
        return self._first_text(resp).strip()

    # --------------------------------------------------- read-later digest
    async def digest_read_later(self, items: list[dict[str, Any]]) -> str:
        model = self.settings.megan_reasoning_model
        listing = "\n".join(
            f"- {it.get('title') or it.get('url') or 'untitled'}"
            + (f" — {it['note']}" if it.get("note") else "")
            for it in items
        )
        resp = await self._client.messages.create(
            model=model,
            max_tokens=1200,
            system=note_writer_system(),
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Group these read-later items by topic and give the owner a tight digest "
                        "of the top picks, one line each. Plain text for Telegram, not a file.\n\n"
                        + listing
                    ),
                }
            ],
        )
        await self._record(model, "summary", resp.usage)
        return self._first_text(resp).strip()

    # --------------------------------------------------------- helpers
    @staticmethod
    def _first_text(resp: Any) -> str:
        for block in resp.content:
            if block.type == "text":
                return block.text
        return ""

    def _first_json(self, resp: Any) -> dict[str, Any]:
        text = self._first_text(resp)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            log.warning("could not parse JSON from model output: %r", text[:200])
            return {}
