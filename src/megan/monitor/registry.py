"""Default read-only command sets for dev-host monitoring.

These are the only shapes of commands Megan ever runs on a dev box. The host's
own `allowed_commands` list (in Postgres) is authoritative; these are sensible
defaults to seed it with.
"""

from __future__ import annotations

import re

# Mutating commands that must NEVER run, even if someone mistakenly allowlists
# one. Matched as whole tokens (so "apt" never trips on "capture-pane").
FORBIDDEN_TOKENS = {
    "rm",
    "rmdir",
    "mv",
    "cp",
    "dd",
    "chmod",
    "chown",
    "kill",
    "pkill",
    "killall",
    "reboot",
    "shutdown",
    "halt",
    "systemctl",
    "service",
    "apt",
    "apt-get",
    "yum",
    "dnf",
    "pip",
    "pip3",
    "npm",
    "yarn",
    "pnpm",
    "tee",
    "truncate",
    "mkfs",
    "wget",
    "curl",
}

# git subcommands that change state.
FORBIDDEN_GIT_SUBCOMMANDS = {"push", "commit", "reset", "checkout", "clean", "merge", "rebase"}

DEFAULT_ALLOWED_COMMANDS = [
    "tmux capture-pane -p -t agent",
    "git -C ~/proj status --short",
    "tail -n 50 ~/proj/.agent/log",
    "ps -eo pid,comm,etime --sort=-etime | head -n 20",
]


def is_read_only(command: str) -> bool:
    """True only if the command contains no mutating verbs and no redirection.

    Token-based so package-manager names can't trip on innocent substrings
    (e.g. "apt" must not match inside "capture-pane").
    """
    low = command.lower()
    if ">" in low:  # any output redirection writes to disk
        return False

    # Strip any leading path so "/usr/bin/rm" is caught as "rm".
    bases = {tok.rsplit("/", 1)[-1] for tok in re.split(r"\s+", low) if tok}
    if bases & FORBIDDEN_TOKENS:
        return False
    if "git" in bases and (bases & FORBIDDEN_GIT_SUBCOMMANDS):
        return False
    return True
