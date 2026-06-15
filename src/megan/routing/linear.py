"""Linear GraphQL client — create issues from triage answers.

Maps Megan's triage fields (project, priority, due) onto Linear's model. The
project name is resolved to a team (and a Linear project, if one matches by name).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from megan.config import Settings
from megan.routing.dates import parse_due

log = logging.getLogger("megan.routing.linear")

_API = "https://api.linear.app/graphql"

# Megan priority -> Linear priority int (0 none, 1 urgent, 2 high, 3 medium, 4 low)
_PRIORITY = {"urgent": 1, "high": 2, "medium": 3, "low": 4, "none": 0}


class LinearError(RuntimeError):
    pass


class LinearClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._teams: dict[str, str] | None = None  # name/key(lower) -> id

    @property
    def configured(self) -> bool:
        return bool(self.settings.linear_api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.settings.linear_api_key or "",
            "Content-Type": "application/json",
        }

    async def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                _API, json={"query": query, "variables": variables}, headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
        if "errors" in data:
            raise LinearError(str(data["errors"]))
        return data["data"]

    async def _load_teams(self) -> dict[str, str]:
        if self._teams is not None:
            return self._teams
        data = await self._gql("{ teams { nodes { id name key } } }", {})
        teams: dict[str, str] = {}
        for node in data["teams"]["nodes"]:
            teams[node["name"].lower()] = node["id"]
            teams[node["key"].lower()] = node["id"]
        self._teams = teams
        return teams

    async def _resolve_team(self, project: str | None) -> str:
        teams = await self._load_teams()
        if project and project.lower() in teams:
            return teams[project.lower()]
        if self.settings.linear_default_team and self.settings.linear_default_team.lower() in teams:
            return teams[self.settings.linear_default_team.lower()]
        if teams:
            return next(iter(teams.values()))
        raise LinearError("no Linear teams available")

    async def create_task(
        self,
        *,
        title: str,
        project: str | None = None,
        priority: str = "none",
        due: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a Linear issue. Returns {identifier, url, ok}."""
        if not self.configured:
            log.warning("Linear not configured; skipping task creation")
            return {"ok": False, "identifier": None, "url": None, "error": "not_configured"}

        team_id = await self._resolve_team(project)
        variables: dict[str, Any] = {
            "input": {
                "teamId": team_id,
                "title": title,
                "priority": _PRIORITY.get(priority, 0),
            }
        }
        if description:
            variables["input"]["description"] = description
        iso_due = parse_due(due)
        if iso_due:
            variables["input"]["dueDate"] = iso_due

        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { identifier url }
          }
        }
        """
        data = await self._gql(mutation, variables)
        result = data["issueCreate"]
        if not result["success"]:
            raise LinearError("issueCreate returned success=false")
        issue = result["issue"]
        return {"ok": True, "identifier": issue["identifier"], "url": issue["url"]}

    async def issues_due_through_today(self) -> list[dict[str, Any]]:
        """The viewer's incomplete issues due today or overdue. Used by reminders
        and the morning brief. Returns [] if Linear isn't configured/reachable."""
        if not self.configured:
            return []
        from datetime import date

        query = """
        query {
          viewer {
            assignedIssues(filter: { completedAt: { null: true } }, first: 100) {
              nodes { identifier title dueDate url }
            }
          }
        }
        """
        try:
            data = await self._gql(query, {})
        except Exception as exc:  # noqa: BLE001
            log.warning("Linear due-issues query failed: %s", exc)
            return []
        today = date.today().isoformat()
        out: list[dict[str, Any]] = []
        for node in data["viewer"]["assignedIssues"]["nodes"]:
            due = node.get("dueDate")
            if due and due <= today:
                out.append(node)
        return out
