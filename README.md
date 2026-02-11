# SecGit Receiver + Metrics

SecGit is a FastAPI service that receives GitLab push webhooks, tracks
default-branch first-parent commit history, stores commit stats in PostgreSQL,
and exposes Prometheus metrics.

## Configuration

Set these environment variables:

- `POSTGRES_DSN`
- `GITLAB_URL`
- `GITLAB_TOKEN`
- `GITLAB_WEBHOOK_SECRET`
- `REQUEST_TIMEOUT_SECONDS` (default `20`)
- `WALK_MAX_COMMITS` (default `5000`)
- `RESYNC_MAX_COMMITS` (default `2000`)

Example `.env`:

```bash
POSTGRES_DSN='postgresql://postgres:<password>@[2001:db8::10]:5432/secgit?sslmode=disable'
GITLAB_URL='https://gitlab.example.com'
GITLAB_TOKEN='glpat-...'
GITLAB_WEBHOOK_SECRET=''
REQUEST_TIMEOUT_SECONDS=20
WALK_MAX_COMMITS=5000
RESYNC_MAX_COMMITS=2000
```

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a; source .env; set +a
uvicorn main:app --host "" --port 8080
```

## HTTP Endpoints

- `POST /` receives GitLab push events
- `GET /metrics` exposes Prometheus metrics
- `GET /healthz` returns service health
- `GET /` returns endpoint summary

## Processing Rules

- Only default-branch push events are processed.
  - Required condition: `ref == "refs/heads/<project.default_branch>"`
- First-parent model only:
  - For merge commits, only the first parent is stored.
- Idempotency:
  - If `db_head == after`, event is treated as replay and ignored.
- Fast-forward path:
  - If `db_head == before`, walk from `after` back to `before` (exclusive), upsert commits, update head.
- Force-push/mismatch path:
  - If `db_head != before` and `db_head != after`, increment force-push counters, bounded resync from `after`, recompute aggregates.

## Labels Contract

All metrics use only `{path, repo, user}`.

- `path`: namespace path without repo name
- `repo`: repository name
- `user`: email identity

User source rules:

- Commit gauges: `user = author_email`
- Force-push counter: `user = pusher_email`

## Prometheus Metrics

- `secgit_force_push_total{path,repo,user}` (counter)
- `secgit_commits_count{path,repo,user}` (gauge)
- `secgit_commits_additions{path,repo,user}` (gauge)
- `secgit_commits_deletions{path,repo,user}` (gauge)

## PostgreSQL Schema

Core tables:

- `repo`
- `commit`
- `force_push_counter`

Schema used by the service:

```sql
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
```
