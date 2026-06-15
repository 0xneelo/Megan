"""LLM layer — all Claude calls go through here.

Understanding (Claude) is cleanly separated from doing (the orchestrator's code):
the triage model calls *tools*, and the orchestrator executes whichever tool it
picked. See llm/tools.py for that routing contract.
"""

from megan.llm.client import ClaudeClient

__all__ = ["ClaudeClient"]
