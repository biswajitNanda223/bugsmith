import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import config, memory, pr, service
from app.api.webhooks import router as webhooks_router
from app.schemas import (Detail, FeedbackIn, Incident, IncidentIn, Ok, PrResult, Run)
from app.store import store

DESCRIPTION = """
**BugSmith** turns a production crash into a validated draft pull request:
investigate (parallel agents) → root cause + calibrated confidence → fix → sandbox validation → draft PR.

* **Incidents** are crashes, deduplicated by `issue_id`; **runs** are investigations of an incident.
* Live progress of a run streams over **Server-Sent Events** (`GET /api/runs/{run_id}/stream`).
* Crashes can arrive from Sentry via `POST /webhooks/sentry` (HMAC-signed).
* Nothing is merged or pushed to a default branch: PRs are **drafts** on a `bugsmith/*` branch.
"""
TAGS = [
    {"name": "incidents", "description": "Crashes under investigation."},
    {"name": "runs", "description": "One investigation of an incident: timeline, RCA, fix, validation, PR."},
    {"name": "webhooks", "description": "Inbound events from monitoring tools."},
    {"name": "system", "description": "Health."},
]
app = FastAPI(title="BugSmith API", version="0.1.0", description=DESCRIPTION, openapi_tags=TAGS,
              contact={"name": "BugSmith", "url": "https://github.com/biswajitNanda223/bugsmith"})
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
app.include_router(webhooks_router)


@app.get("/api/health", tags=["system"], summary="Liveness check", response_model=Ok)
def health():
    return {"ok": True}


@app.get("/api/incidents", tags=["incidents"], summary="List incidents", response_model=list[Incident],
         description="Most recently seen first.")
def list_incidents():
    return store.list_incidents()


@app.post("/api/incidents", status_code=201, tags=["incidents"], summary="Report a crash manually", response_model=Incident,
          description="Idempotent on `issue_id`: reporting a known crash returns the existing incident and bumps `occurrences`.")
def create_incident(body: IncidentIn):
    """Manual creation; the Sentry webhook (milestone 7) will call the same path."""
    return store.create_incident(body.issue_id, body.title, body.service)


@app.post("/api/incidents/{incident_id}/runs", status_code=202, tags=["runs"], summary="Start an investigation", response_model=Run,
          responses={404: {"model": Detail, "description": "Unknown incident"}},
          description="Returns immediately; follow progress with the SSE stream.")
async def start_run(incident_id: str):
    if not store.get_incident(incident_id):
        raise HTTPException(404, "incident not found")
    return service.start_run(incident_id)


@app.get("/api/runs/{run_id}", tags=["runs"], summary="Get a run", response_model=Run,
         responses={404: {"model": Detail, "description": "Unknown run"}})
def get_run(run_id: str):
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.post("/api/runs/{run_id}/feedback", tags=["runs"], summary="Rate the root cause (👍/👎)", response_model=Ok,
          responses={404: {"model": Detail}, 409: {"model": Detail, "description": "Run has no saved RCA"}},
          description="Updates long-term memory and each agent's reliability, which feed future confidence scores.")
def feedback(run_id: str, body: FeedbackIn):
    """Engineer verdict on the RCA. Updates long-term memory and per-agent reliability."""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    rca = run["rca"]
    if not rca:
        raise HTTPException(409, "run has no saved RCA")
    if not memory.record_feedback(run_id, body.accepted, list(rca["confidence_breakdown"]["agents"])):
        raise HTTPException(409, "run has no saved RCA")
    store.set_feedback(run_id, "accepted" if body.accepted else "rejected")
    return {"ok": True}


@app.post("/api/runs/{run_id}/pr", tags=["runs"], summary="Open the draft pull request", response_model=PrResult,
          responses={400: {"model": Detail, "description": "GITHUB_TOKEN / BUGSMITH_GITHUB_REPO not configured"},
                     404: {"model": Detail}, 409: {"model": Detail, "description": "No validated fix"},
                     502: {"model": Detail, "description": "git push or GitHub API failed"}},
          description="Human-approved step. Pushes a `bugsmith/<issue>` branch (never the base branch) and opens a **draft** PR. Idempotent.")
async def open_pr(run_id: str):
    """Human-approved: push a branch and open a DRAFT PR for a validated fix."""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if not run.get("pr_preview"):
        raise HTTPException(409, "no validated fix to open a PR for")
    if run.get("pr"):
        return run["pr"]
    if not (config.GITHUB_TOKEN and config.GITHUB_REPO):
        raise HTTPException(400, "set GITHUB_TOKEN and BUGSMITH_GITHUB_REPO on the server to open real PRs")
    try:
        opened = await asyncio.to_thread(pr.open_draft_pr, run["pr_preview"], run["fix"], repo=config.GITHUB_REPO,
                                         base=config.GITHUB_BASE, token=config.GITHUB_TOKEN)
    except Exception as e:
        raise HTTPException(502, str(e))
    store.update_run(run_id, pr=opened)
    store.set_incident_status(run["incident_id"], "pr_open")
    return opened


@app.get("/api/runs/{run_id}/stream", tags=["runs"], summary="Live run progress (SSE)",
         response_class=EventSourceResponse, responses={200: {"content": {"text/event-stream": {}}}, 404: {"model": Detail}},
         description="`text/event-stream`. Event `step`: JSON timeline entry (`tool_call`, `tool_result`, `message`, `validation`, "
                     "`confidence`). Final event `done`: `{status, rca, error, fix, validation, fix_skipped, pr_preview, pr}`.")
async def stream_run(run_id: str):
    if not store.get_run(run_id):
        raise HTTPException(404, "run not found")

    async def gen():
        sent = 0
        while True:
            events, status = store.events_since(run_id, sent)
            for ev in events:
                yield {"event": "step", "data": json.dumps(ev)}
            sent += len(events)
            if status != "running":
                run = store.get_run(run_id)
                keys = ("rca", "error", "fix", "validation", "fix_skipped", "pr_preview", "pr")
                yield {"event": "done", "data": json.dumps({"status": status, **{k: run[k] for k in keys}})}
                return
            await store.wait_for_change()

    return EventSourceResponse(gen())
