"""Sentry webhook: verify -> dedupe -> store crash -> (optionally) start an investigation."""
import datetime as dt
import hashlib
import hmac
import json
import re

from fastapi import APIRouter, Header, HTTPException, Request

from app import config, service
from app.schemas import Detail, WebhookResult
from app.store import store

router = APIRouter()


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _frames(event: dict) -> list[dict]:
    out = []
    for exc in (event.get("exception") or {}).get("values", []):
        for f in (exc.get("stacktrace") or {}).get("frames", []):
            out.append({"file": f.get("filename") or f.get("abs_path", ""), "function": f.get("function", ""),
                        "line": f.get("lineno"), "context": f.get("context_line", "")})
    return out  # Sentry orders frames oldest-first, like a Python traceback (innermost call last)


def normalize(resource: str, payload: dict) -> dict | None:
    """Sentry payload -> our crash report (same shape as fixtures/sentry_issue_*.json). None = not something we handle."""
    data = payload.get("data") or {}
    if resource == "event_alert":
        ev = data.get("event") or {}
        issue_id = ev.get("issue_id")
        tags = {k: v for k, v in (ev.get("tags") or []) if isinstance(k, str)}
        report = {"title": ev.get("title") or ev.get("message", ""), "release": ev.get("release") or tags.get("release", ""),
                  "service": tags.get("service") or ev.get("project_name") or str(ev.get("project", "")),
                  "environment": ev.get("environment", ""), "request": (ev.get("request") or {}).get("url", ev.get("culprit", "")),
                  "stacktrace": _frames(ev), "level": ev.get("level", ""), "web_url": ev.get("web_url", ""),
                  "first_seen": ev.get("datetime", "")}
    elif resource == "issue" and payload.get("action") == "created":
        iss = data.get("issue") or {}
        issue_id = iss.get("id")
        report = {"title": iss.get("title", ""), "service": (iss.get("project") or {}).get("slug", ""), "request": iss.get("culprit", ""),
                  "stacktrace": [], "level": iss.get("level", ""), "web_url": iss.get("permalink", ""), "first_seen": iss.get("firstSeen", "")}
    else:
        return None
    if not issue_id or not report["title"]:
        return None
    report["id"] = f"sentry-{re.sub(r'[^A-Za-z0-9_-]', '', str(issue_id))}"
    return report


@router.post("/webhooks/sentry", tags=["webhooks"], summary="Receive a Sentry alert", response_model=WebhookResult,
             responses={401: {"model": Detail, "description": "Bad or missing signature"},
                        400: {"model": Detail, "description": "Invalid JSON"},
                        503: {"model": Detail, "description": "SENTRY_WEBHOOK_SECRET not configured"}},
             description="Configure as the webhook URL of a Sentry internal integration (issue alert / `issue.created`). "
                         "The body is verified with HMAC-SHA256 using the integration's client secret. A repeated `Request-ID` is a "
                         "retry and is ignored; a repeat of a known crash only increments its occurrence count. With "
                         "`BUGSMITH_AUTO_RUN=1` a new crash also starts an investigation, subject to a cooldown and an hourly cap.")
async def sentry_webhook(
        request: Request,
        sentry_hook_signature: str | None = Header(None, alias="Sentry-Hook-Signature", description="Hex HMAC-SHA256 of the raw body"),
        sentry_hook_resource: str | None = Header(None, alias="Sentry-Hook-Resource", description="`event_alert` or `issue`"),
        request_id: str | None = Header(None, alias="Request-ID", description="Delivery id, used for idempotency")):
    if not config.SENTRY_WEBHOOK_SECRET:
        raise HTTPException(503, "SENTRY_WEBHOOK_SECRET is not configured; refusing unsigned webhooks")
    body = await request.body()
    if not verify_signature(body, sentry_hook_signature or "", config.SENTRY_WEBHOOK_SECRET):
        raise HTTPException(401, "bad signature")

    delivery = request_id or hashlib.sha256(body).hexdigest()
    if not store.claim_delivery(f"sentry:{delivery}"):
        return {"status": "duplicate_delivery"}

    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    report = normalize(sentry_hook_resource or "", payload)
    if report is None:
        return {"status": "ignored"}

    inc, created = store.upsert_incident(report["id"], report["title"], report["service"], source="sentry")
    store.save_crash_report(report["id"], report)
    if not config.AUTO_RUN:
        return {"status": "created" if created else "occurrence_recorded", "incident_id": inc["id"]}

    last = store.latest_run(inc["id"])
    if last and last["status"] == "running":
        return {"status": "investigation_in_progress", "incident_id": inc["id"], "run_id": last["id"]}
    if last and dt.datetime.fromisoformat(last["started_at"]) > dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=config.RUN_COOLDOWN_MIN):
        return {"status": "recently_investigated", "incident_id": inc["id"], "run_id": last["id"]}
    if store.runs_started_since(60) >= config.MAX_RUNS_PER_HOUR:
        return {"status": "rate_limited", "incident_id": inc["id"]}
    run = service.start_run(inc["id"])
    return {"status": "investigating", "incident_id": inc["id"], "run_id": run["id"]}
