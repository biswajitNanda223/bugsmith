import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import config, main, runner, service
from app.agents.pipeline import build_full_pipeline
from app.store import store
from tests.fake_llm import FakeLlm

SECRET = "s3cret"


def event_alert(issue_id="4711", title="TypeError: unsupported operand type(s) for -: 'int' and 'NoneType'"):
    return {"action": "triggered", "data": {"event": {
        "issue_id": issue_id, "title": title, "level": "error", "release": "2026.10.11-1", "web_url": "https://sentry.io/x",
        "tags": [["service", "demo-shop"]], "request": {"url": "https://shop/cart/c2/total"},
        "exception": {"values": [{"type": "TypeError", "stacktrace": {"frames": [
            {"filename": "demo-app/app.py", "function": "get_total", "lineno": 24},
            {"filename": "demo-app/app.py", "function": "cart_total", "lineno": 19, "context_line": "total += ..."}]}}]}}}}


def wait_started(c, n, timeout=5.0):
    """The investigation starts in a background task right after the response; wait for it."""
    import time
    end = time.time() + timeout
    while len(c.started) < n and time.time() < end:
        time.sleep(0.02)
    return c.started


def post(c, payload, resource="event_alert", secret=SECRET, request_id=None, sign=True):
    body = json.dumps(payload).encode()
    headers = {"sentry-hook-resource": resource, "content-type": "application/json"}
    if sign:
        headers["sentry-hook-signature"] = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if request_id:
        headers["request-id"] = request_id
    return c.post("/webhooks/sentry", content=body, headers=headers)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "SENTRY_WEBHOOK_SECRET", SECRET)
    started = []

    async def fake_run(run_id, issue_id):
        started.append(issue_id)
        await runner.run_incident(run_id, issue_id, agent=build_full_pipeline(model=FakeLlm()))

    monkeypatch.setattr(service, "run_incident", fake_run)
    with TestClient(main.app) as c:
        c.started = started
        yield c


def test_rejects_missing_secret_config(monkeypatch):
    monkeypatch.setattr(config, "SENTRY_WEBHOOK_SECRET", "")
    with TestClient(main.app) as c:
        assert post(c, event_alert()).status_code == 503


def test_rejects_bad_or_missing_signature(client):
    assert post(client, event_alert(), sign=False).status_code == 401
    assert post(client, event_alert(), secret="wrong").status_code == 401


def test_creates_incident_and_crash_report(client):
    r = post(client, event_alert()).json()
    assert r["status"] == "created"
    inc = store.get_incident(r["incident_id"])
    assert inc["issue_id"] == "sentry-4711" and inc["source"] == "sentry" and inc["service"] == "demo-shop"
    report = store.get_crash_report("sentry-4711")
    assert report["stacktrace"][-1]["function"] == "cart_total" and report["release"] == "2026.10.11-1"
    from app.tools import local
    assert local.get_crash_report("sentry-4711")["id"] == "sentry-4711"  # agents read what the webhook stored
    assert client.started == []  # AUTO_RUN is off by default


def test_duplicate_delivery_and_repeat_occurrence(client):
    assert post(client, event_alert(), request_id="req-1").json()["status"] == "created"
    assert post(client, event_alert(), request_id="req-1").json()["status"] == "duplicate_delivery"  # provider retry
    r = post(client, event_alert(), request_id="req-2").json()  # same crash, new event
    assert r["status"] == "occurrence_recorded"
    inc = store.get_incident(r["incident_id"])
    assert inc["occurrences"] == 2 and len(store.list_incidents()) == 1


def test_unhandled_resource_is_ignored(client):
    assert post(client, {"action": "resolved", "data": {}}, resource="issue").json()["status"] == "ignored"
    assert store.list_incidents() == []


def test_auto_run_dedupes_and_rate_limits(client, monkeypatch):
    monkeypatch.setattr(config, "AUTO_RUN", True)
    first = post(client, event_alert(), request_id="a").json()
    assert first["status"] == "investigating" and wait_started(client, 1) == ["sentry-4711"]
    again = post(client, event_alert(), request_id="b").json()  # same crash again, within cooldown
    assert again["status"] in {"recently_investigated", "investigation_in_progress"} and len(client.started) == 1

    monkeypatch.setattr(config, "MAX_RUNS_PER_HOUR", 1)
    other = post(client, event_alert("9999", "ValueError: boom"), request_id="c").json()
    assert other["status"] == "rate_limited" and len(client.started) == 1
