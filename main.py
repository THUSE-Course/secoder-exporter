from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response

from config import Settings, load_settings
from db import Database
from gitlab_client import GitLabClient
from metrics import render_metrics
from receiver import ReceiverService, parse_push_event


app = FastAPI(title="secgit-receiver", version="0.1.0")

settings: Settings = load_settings()
database = Database(settings.postgres_dsn)
gitlab = GitLabClient(
    settings.gitlab_url, settings.gitlab_token, settings.request_timeout_seconds
)
receiver = ReceiverService(
    gitlab, settings.walk_max_commits, settings.resync_max_commits
)


def _is_signature_valid(secret: str, provided: str | None) -> bool:
    if not secret:
        return True
    if not provided:
        return False
    return secret == provided


@app.on_event("startup")
def startup() -> None:
    database.init_schema()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "status": "ok",
        "webhook_endpoint": "/",
        "metrics_endpoint": "/metrics",
    }


@app.get("/metrics")
def metrics() -> Response:
    with database.connection() as conn:
        payload = render_metrics(conn)
    return Response(content=payload, media_type="text/plain; version=0.0.4")


@app.post("/")
async def gitlab_webhook(request: Request) -> dict[str, str]:
    signature = request.headers.get("X-Gitlab-Token")
    if settings.gitlab_webhook_secret and not _is_signature_valid(
        settings.gitlab_webhook_secret, signature
    ):
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    payload = await request.json()
    if payload.get("object_kind") != "push":
        return {"status": "ignored_non_push"}

    try:
        event = parse_push_event(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        with database.connection() as conn:
            status = receiver.process_push(conn, event)
            conn.commit()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"sync failed: {exc}"
        ) from exc

    return {"status": status}
