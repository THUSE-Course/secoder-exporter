from __future__ import annotations

from psycopg import Connection
from psycopg.rows import dict_row


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render_metrics(conn: Connection) -> str:
    lines: list[str] = []

    lines.append(
        "# HELP secgit_force_push_total Force push count for default-branch mismatch events"
    )
    lines.append("# TYPE secgit_force_push_total counter")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT path, repo, user_email, value
            FROM force_push_counter
            ORDER BY path, repo, user_email
            """
        )
        for row in cur.fetchall():
            lines.append(
                'secgit_force_push_total{path="%s",repo="%s",user="%s"} %s'
                % (
                    _escape_label(row["path"]),
                    _escape_label(row["repo"]),
                    _escape_label(row["user_email"]),
                    row["value"],
                )
            )

    lines.append("# HELP secgit_commits Stored first-parent commits per author")
    lines.append("# TYPE secgit_commits gauge")
    lines.append(
        "# HELP secgit_commits_additions Stored first-parent additions per author"
    )
    lines.append("# TYPE secgit_commits_additions gauge")
    lines.append(
        "# HELP secgit_commits_deletions Stored first-parent deletions per author"
    )
    lines.append("# TYPE secgit_commits_deletions gauge")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              path,
              repo,
              author_email,
              COUNT(*)::BIGINT AS c,
              COALESCE(SUM(additions), 0)::BIGINT AS a,
              COALESCE(SUM(deletions), 0)::BIGINT AS d
            FROM commit
            WHERE is_head_chain = TRUE
            GROUP BY path, repo, author_email
            ORDER BY path, repo, author_email
            """
        )
        for row in cur.fetchall():
            path = _escape_label(row["path"])
            repo = _escape_label(row["repo"])
            user = _escape_label(row["author_email"])
            labels = f'path="{path}",repo="{repo}",user="{user}"'
            lines.append(f"secgit_commits{{{labels}}} {row['c']}")
            lines.append(f"secgit_commits_additions{{{labels}}} {row['a']}")
            lines.append(f"secgit_commits_deletions{{{labels}}} {row['d']}")

    lines.append("")
    return "\n".join(lines)
