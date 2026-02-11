from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import requests


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    parent_sha: str | None
    author_email: str
    committed_date: str | None
    additions: int
    deletions: int


class GitLabClient:
    def __init__(
        self, base_url: str, token: str, timeout_seconds: int = 20
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"PRIVATE-TOKEN": token})
        self._timeout = timeout_seconds

    def get_commit(self, project_id: int, sha: str) -> CommitInfo:
        url = f"{self._base_url}/api/v4/projects/{project_id}/repository/commits/{sha}"
        resp = self._session.get(
            url, params={"stats": "true"}, timeout=self._timeout
        )
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()

        parent_ids = payload.get("parent_ids") or []
        stats = payload.get("stats") or {}

        return CommitInfo(
            sha=payload["id"],
            parent_sha=parent_ids[0] if parent_ids else None,
            author_email=(
                payload.get("author_email") or "unknown@example.com"
            ).lower(),
            committed_date=payload.get("committed_date"),
            additions=int(stats.get("additions") or 0),
            deletions=int(stats.get("deletions") or 0),
        )
