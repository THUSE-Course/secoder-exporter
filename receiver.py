from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row

from gitlab_client import CommitInfo, GitLabClient


ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class PushEvent:
    project_id: int
    path: str
    repo: str
    ref: str
    default_branch: str
    before: str
    after: str
    pusher_email: str


class ReceiverService:
    def __init__(
        self,
        gitlab: GitLabClient,
        walk_max_commits: int,
        resync_max_commits: int,
    ) -> None:
        self._gitlab = gitlab
        self._walk_max_commits = walk_max_commits
        self._resync_max_commits = resync_max_commits

    def process_push(self, conn: Connection, event: PushEvent) -> str:
        if event.ref != f"refs/heads/{event.default_branch}":
            return "ignored_non_default_branch"

        if event.after == ZERO_SHA:
            return "ignored_branch_deletion"

        with conn.transaction():
            state = self._get_repo_state_for_update(
                conn, event.path, event.repo
            )
            if state is None:
                return self._initial_sync(conn, event)

            db_head = state["head_sha"]

            if db_head == event.after:
                return "noop_already_applied"

            if db_head == event.before:
                return self._fast_forward_sync(conn, event)

            return self._force_resync(conn, event)

    def _initial_sync(self, conn: Connection, event: PushEvent) -> str:
        commits = self._walk_chain(
            event.project_id, event.after, None, self._resync_max_commits
        )

        self._upsert_repo_row(
            conn,
            event.path,
            event.repo,
            event.after,
            commits[0].committed_date if commits else None,
        )
        self._set_chain_flags(conn, event.path, event.repo, reset=True)
        self._upsert_commits(
            conn, event.path, event.repo, commits, in_head_chain=True
        )
        self._recompute_repo_aggregates(
            conn, event.path, event.repo, event.after
        )
        return "synced_initial"

    def _fast_forward_sync(self, conn: Connection, event: PushEvent) -> str:
        commits = self._walk_chain(
            event.project_id, event.after, event.before, self._walk_max_commits
        )
        if not commits:
            self._touch_repo_head(
                conn, event.path, event.repo, event.after, None
            )
            return "synced_fast_forward_empty"

        self._upsert_commits(
            conn, event.path, event.repo, commits, in_head_chain=True
        )
        self._recompute_repo_aggregates(
            conn, event.path, event.repo, event.after
        )
        self._touch_repo_head(
            conn, event.path, event.repo, event.after, commits[0].committed_date
        )
        return "synced_fast_forward"

    def _force_resync(self, conn: Connection, event: PushEvent) -> str:
        commits = self._walk_chain(
            event.project_id, event.after, None, self._resync_max_commits
        )

        self._increment_force_push(conn, event.path, event.repo)
        self._increment_force_push_user(
            conn, event.path, event.repo, event.pusher_email
        )

        self._set_chain_flags(conn, event.path, event.repo, reset=True)
        self._upsert_commits(
            conn, event.path, event.repo, commits, in_head_chain=True
        )
        self._touch_repo_head(
            conn,
            event.path,
            event.repo,
            event.after,
            commits[0].committed_date if commits else None,
        )
        self._recompute_repo_aggregates(
            conn, event.path, event.repo, event.after
        )
        return "synced_force_resync"

    def _walk_chain(
        self,
        project_id: int,
        start_sha: str,
        stop_sha: str | None,
        max_commits: int,
    ) -> list[CommitInfo]:
        result: list[CommitInfo] = []
        cursor = start_sha
        seen: set[str] = set()

        while cursor and cursor != ZERO_SHA:
            if cursor == stop_sha:
                break
            if cursor in seen:
                raise ValueError(
                    f"detected cycle while walking first-parent chain at {cursor}"
                )

            seen.add(cursor)
            info = self._gitlab.get_commit(project_id, cursor)
            result.append(info)

            if len(result) >= max_commits:
                raise ValueError(
                    f"chain walk exceeded limit={max_commits}; increase WALK/RESYNC max"
                )

            cursor = info.parent_sha

        return result

    def _get_repo_state_for_update(
        self, conn: Connection, path: str, repo: str
    ) -> dict[str, Any] | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT head_sha FROM repo WHERE path = %s AND repo = %s FOR UPDATE",
                (path, repo),
            )
            return cur.fetchone()

    def _upsert_repo_row(
        self,
        conn: Connection,
        path: str,
        repo: str,
        head_sha: str,
        head_commit_ts: str | None,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repo(path, repo, head_sha, head_commit_ts, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (path, repo)
                DO UPDATE SET
                  head_sha = EXCLUDED.head_sha,
                  head_commit_ts = EXCLUDED.head_commit_ts,
                  updated_at = now()
                """,
                (path, repo, head_sha, _parse_ts(head_commit_ts)),
            )

    def _touch_repo_head(
        self,
        conn: Connection,
        path: str,
        repo: str,
        head_sha: str,
        head_commit_ts: str | None,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE repo
                   SET head_sha = %s,
                       head_commit_ts = %s,
                       updated_at = now()
                 WHERE path = %s AND repo = %s
                """,
                (head_sha, _parse_ts(head_commit_ts), path, repo),
            )

    def _set_chain_flags(
        self, conn: Connection, path: str, repo: str, reset: bool
    ) -> None:
        if not reset:
            return
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE commit SET is_head_chain = FALSE WHERE path = %s AND repo = %s",
                (path, repo),
            )

    def _upsert_commits(
        self,
        conn: Connection,
        path: str,
        repo: str,
        commits: list[CommitInfo],
        in_head_chain: bool,
    ) -> None:
        if not commits:
            return

        with conn.cursor() as cur:
            for info in commits:
                cur.execute(
                    """
                    INSERT INTO commit(
                      path,
                      repo,
                      sha,
                      parent_sha,
                      committed_at,
                      author_email,
                      additions,
                      deletions,
                      stats_fetched_at,
                      is_head_chain
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s)
                    ON CONFLICT (path, repo, sha)
                    DO UPDATE SET
                      parent_sha = EXCLUDED.parent_sha,
                      committed_at = EXCLUDED.committed_at,
                      author_email = EXCLUDED.author_email,
                      additions = EXCLUDED.additions,
                      deletions = EXCLUDED.deletions,
                      stats_fetched_at = now(),
                      is_head_chain = EXCLUDED.is_head_chain
                    """,
                    (
                        path,
                        repo,
                        info.sha,
                        info.parent_sha,
                        _parse_ts(info.committed_date),
                        info.author_email,
                        info.additions,
                        info.deletions,
                        in_head_chain,
                    ),
                )

    def _recompute_repo_aggregates(
        self, conn: Connection, path: str, repo: str, head_sha: str
    ) -> None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                  COUNT(*)::BIGINT AS c,
                  COALESCE(SUM(additions), 0)::BIGINT AS a,
                  COALESCE(SUM(deletions), 0)::BIGINT AS d
                FROM commit
                WHERE path = %s AND repo = %s AND is_head_chain = TRUE
                """,
                (path, repo),
            )
            row = cur.fetchone() or {"c": 0, "a": 0, "d": 0}

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE repo
                   SET head_sha = %s,
                       fp_commits_count = %s,
                       fp_additions_sum = %s,
                       fp_deletions_sum = %s,
                       updated_at = now()
                 WHERE path = %s AND repo = %s
                """,
                (head_sha, row["c"], row["a"], row["d"], path, repo),
            )

    def _increment_force_push(
        self, conn: Connection, path: str, repo: str
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE repo
                   SET force_push_count = force_push_count + 1,
                       updated_at = now()
                 WHERE path = %s AND repo = %s
                """,
                (path, repo),
            )

    def _increment_force_push_user(
        self, conn: Connection, path: str, repo: str, user_email: str
    ) -> None:
        user = (user_email or "unknown@example.com").lower()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO force_push_counter(path, repo, user_email, value)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT(path, repo, user_email)
                DO UPDATE SET value = force_push_counter.value + 1
                """,
                (path, repo, user),
            )


def parse_push_event(payload: dict[str, Any]) -> PushEvent:
    project = payload.get("project") or {}
    default_branch = project.get("default_branch")
    ref = payload.get("ref")
    before = payload.get("before")
    after = payload.get("after")

    if not isinstance(default_branch, str) or not default_branch:
        raise ValueError("missing project.default_branch")
    if not isinstance(ref, str) or not ref:
        raise ValueError("missing ref")
    if not isinstance(before, str) or not before:
        raise ValueError("missing before")
    if not isinstance(after, str) or not after:
        raise ValueError("missing after")

    project_id = project.get("id")
    if not isinstance(project_id, int):
        raise ValueError("missing project.id")

    path_with_namespace = project.get("path_with_namespace")
    if (
        not isinstance(path_with_namespace, str)
        or "/" not in path_with_namespace
    ):
        raise ValueError("missing project.path_with_namespace")

    path, repo = path_with_namespace.rsplit("/", 1)
    if not path or not repo:
        raise ValueError("invalid project.path_with_namespace")

    pusher_email = payload.get("user_email")
    if not isinstance(pusher_email, str) or not pusher_email:
        pusher_email = "unknown@example.com"

    return PushEvent(
        project_id=project_id,
        path=path,
        repo=repo,
        ref=ref,
        default_branch=default_branch,
        before=before,
        after=after,
        pusher_email=pusher_email.lower(),
    )


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
