"""The routing contract: tools Claude can call during triage.

Claude calls exactly one of these per turn; the orchestrator executes whichever
one it picked. This is the clean seam between understanding and doing.
"""

from __future__ import annotations

from typing import Any

CREATE_LINEAR_TASK = "create_linear_task"
CREATE_OBSIDIAN_NOTE = "create_obsidian_note"
ADD_TO_READ_LATER = "add_to_read_later"
ASK_CLARIFYING_QUESTION = "ask_clarifying_question"
DROP_ITEM = "drop_item"
MARK_AMBIGUOUS = "mark_ambiguous_for_later"

TRIAGE_TOOLS: list[dict[str, Any]] = [
    {
        "name": CREATE_LINEAR_TASK,
        "description": "File the item as a task in Linear. Use when it's something to DO.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short imperative task title."},
                "project": {
                    "type": "string",
                    "description": "Linear project / team the task belongs to.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["urgent", "high", "medium", "low", "none"],
                },
                "due": {
                    "type": "string",
                    "description": "Natural-language or ISO date, e.g. 'this week', 'Fri', '2026-06-20', or empty.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional longer description / context for the task.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": CREATE_OBSIDIAN_NOTE,
        "description": "Save the item as a markdown note/doc in Obsidian. Use for things to KEEP.",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "Vault subfolder, e.g. 'Notes', 'ReadLater', 'Ideas'.",
                },
                "title": {"type": "string"},
                "body": {
                    "type": "string",
                    "description": "Markdown body. Leave empty to have Megan write it from the item.",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title"],
        },
    },
    {
        "name": ADD_TO_READ_LATER,
        "description": "Park the item in the read-later list. Use for things to READ later.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "title": {"type": "string"},
                "note": {"type": "string"},
                "topic": {"type": "string", "description": "Coarse topic for later digesting."},
            },
            "required": ["title"],
        },
    },
    {
        "name": ASK_CLARIFYING_QUESTION,
        "description": (
            "Ask the owner exactly ONE short question needed to route this item. "
            "Only use when you genuinely cannot route confidently."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "suggested_answers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 tappable quick answers, rendered as numbered options.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": DROP_ITEM,
        "description": "Discard the item (noise, duplicate, or not worth keeping).",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": MARK_AMBIGUOUS,
        "description": "Set the item aside as genuinely unclear, to revisit later.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]
