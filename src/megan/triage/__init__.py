"""Conversational triage — the heart of Megan.

It pulls pending items, asks Claude to route them or ask ONE question, executes
the chosen tool, and enforces the "<=4 open asks" rule in code (not in the model).
"""

from megan.triage.engine import TriageEngine, format_question, resolve_answer

__all__ = ["TriageEngine", "format_question", "resolve_answer"]
