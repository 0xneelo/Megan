"""Agent / dev-environment monitoring — strictly read-only.

Production hosts are never in the registry, and there is no code path that writes
to a remote host. The allowlist is the real protection; a denylist of mutating
verbs is defense in depth on top of it.
"""

from megan.monitor.ssh import AgentMonitor

__all__ = ["AgentMonitor"]
