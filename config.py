import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    postgres_dsn: str
    gitlab_url: str
    gitlab_token: str
    gitlab_webhook_secret: str
    request_timeout_seconds: int
    walk_max_commits: int
    resync_max_commits: int


def _read_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def load_settings() -> Settings:
    postgres_dsn = os.getenv("POSTGRES_DSN", "")
    gitlab_url = os.getenv("GITLAB_URL", "")
    gitlab_token = os.getenv("GITLAB_TOKEN", "")
    gitlab_webhook_secret = os.getenv("GITLAB_WEBHOOK_SECRET", "")

    if not postgres_dsn:
        raise RuntimeError("POSTGRES_DSN is required")
    if not gitlab_url:
        raise RuntimeError("GITLAB_URL is required")
    if not gitlab_token:
        raise RuntimeError("GITLAB_TOKEN is required")

    return Settings(
        postgres_dsn=postgres_dsn,
        gitlab_url=gitlab_url.rstrip("/"),
        gitlab_token=gitlab_token,
        gitlab_webhook_secret=gitlab_webhook_secret,
        request_timeout_seconds=_read_int("REQUEST_TIMEOUT_SECONDS", 20),
        walk_max_commits=_read_int("WALK_MAX_COMMITS", 5000),
        resync_max_commits=_read_int("RESYNC_MAX_COMMITS", 2000),
    )
