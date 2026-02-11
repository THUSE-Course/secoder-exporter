from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repo (
  path TEXT NOT NULL,
  repo TEXT NOT NULL,
  head_sha TEXT NOT NULL,
  head_commit_ts TIMESTAMPTZ,
  force_push_count BIGINT NOT NULL DEFAULT 0,
  fp_commits_count BIGINT NOT NULL DEFAULT 0,
  fp_additions_sum BIGINT NOT NULL DEFAULT 0,
  fp_deletions_sum BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (path, repo)
);

CREATE TABLE IF NOT EXISTS commit (
  path TEXT NOT NULL,
  repo TEXT NOT NULL,
  sha TEXT NOT NULL,
  parent_sha TEXT,
  committed_at TIMESTAMPTZ,
  author_email TEXT NOT NULL,
  additions INT,
  deletions INT,
  stats_fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_head_chain BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (path, repo, sha)
);

CREATE TABLE IF NOT EXISTS force_push_counter (
  path TEXT NOT NULL,
  repo TEXT NOT NULL,
  user_email TEXT NOT NULL,
  value BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (path, repo, user_email)
);

CREATE INDEX IF NOT EXISTS idx_commit_parent
  ON commit(path, repo, parent_sha);

CREATE INDEX IF NOT EXISTS idx_commit_author
  ON commit(path, repo, author_email);

CREATE INDEX IF NOT EXISTS idx_commit_time
  ON commit(path, repo, committed_at DESC);

CREATE INDEX IF NOT EXISTS idx_commit_head_chain
  ON commit(path, repo, is_head_chain, author_email);
"""


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def init_schema(self) -> None:
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self._dsn) as conn:
            yield conn
