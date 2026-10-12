import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import config, memory, pr, service
from app.api.webhooks import router as webhooks_router
from app.store import store

app = FastAPI(title="BugSmith")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
app.include_router(webhooks_router)


class IncidentIn(BaseModel):
    issue_id: str
    title: str
    service: str = ""


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/incidents")
def list_incidents():
    return store.list_incidents()


@app.post("/api/incidents", status_code=201)
def create_incident(body: IncidentIn):
    """Manual creation; the Sentry webhook (milestone 7) will call the same path."""
    return store.create_incident(body.issue_id, body.title, body.service)


@app.post("/api/incidents/{incident_id}/runs", status_code=202)
async def start_run(incident_id: str):
    if not store.get_incident(incident_id):
        raise HTTPException(404, "incident not found")
    return service.start_run(incident_id)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


class FeedbackIn(BaseModel):
    accepted: bool


@app.post("/api/runs/{run_id}/feedback")
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


@app.post("/api/runs/{run_id}/pr")
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


@app.get("/api/runs/{run_id}/stream")
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
