"""Read-only SSH agent monitor.

SSHes into allowlisted dev hosts with a separate, low-privilege keypair and runs
only commands from that host's allowlist (and never a mutating one). Output is fed
to Claude for a summary by the caller.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncssh

from megan.config import Settings
from megan.db.repository import Repository
from megan.monitor.registry import is_read_only

log = logging.getLogger("megan.monitor")


class MonitorError(RuntimeError):
    pass


def _parse_alias(alias: str) -> tuple[str, str | None, int]:
    """Parse 'user@host:port' into (host, user, port)."""
    user: str | None = None
    port = 22
    rest = alias
    if "@" in rest:
        user, rest = rest.split("@", 1)
    if ":" in rest:
        rest, port_s = rest.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            port = 22
    return rest, user, port


class AgentMonitor:
    def __init__(self, settings: Settings, repo: Repository) -> None:
        self.settings = settings
        self.repo = repo

    async def collect(self, host_name: str) -> dict[str, Any]:
        """Run the host's allowlisted read-only commands and return raw output.

        Returns {host, ok, raw, error?}.
        """
        host = await self.repo.get_host(host_name)
        if host is None:
            return {"host": host_name, "ok": False, "error": "unknown host"}
        if host.get("is_production"):
            # Belt-and-suspenders; the DB CHECK already forbids this.
            return {"host": host_name, "ok": False, "error": "production host refused"}

        allowed = host.get("allowed_commands") or []
        ssh_host, user, port = _parse_alias(host["ssh_alias"])

        connect_kwargs: dict[str, Any] = {"port": port}
        if user:
            connect_kwargs["username"] = user
        if self.settings.monitor_ssh_key_path:
            connect_kwargs["client_keys"] = [self.settings.monitor_ssh_key_path]
        if self.settings.monitor_ssh_known_hosts:
            connect_kwargs["known_hosts"] = self.settings.monitor_ssh_known_hosts

        chunks: list[str] = []
        try:
            async with asyncssh.connect(ssh_host, **connect_kwargs) as conn:
                for cmd in allowed:
                    if not is_read_only(cmd):
                        log.warning("refusing non-read-only command on %s: %s", host_name, cmd)
                        continue
                    result = await conn.run(cmd, check=False, timeout=20)
                    out = (result.stdout or "") + (result.stderr or "")
                    chunks.append(f"$ {cmd}\n{out.strip()}\n")
        except Exception as exc:  # noqa: BLE001
            log.error("SSH collect failed for %s: %s", host_name, exc)
            return {"host": host_name, "ok": False, "error": str(exc)}

        return {"host": host_name, "ok": True, "raw": "\n".join(chunks)}
