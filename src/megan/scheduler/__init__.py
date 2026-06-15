"""Proactive scheduling — what makes Megan feel like an assistant, not a form.

All proactivity is rate-limited globally (the <=4-asks rule) and quiet-hours
aware. The scheduler never floods: if the owner isn't answering, the backlog
drip slows to a trickle because no ask-slot frees up.
"""

from megan.scheduler.jobs import build_scheduler

__all__ = ["build_scheduler"]
