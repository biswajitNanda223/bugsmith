import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import memory
from app.runner import run_incident
from app.store import store

app = FastAPI(title="BugSmith")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
_tasks: set[asyncio.Task] = set()


class IncidentIn(BaseModel):
    issue_id: str
    title: str
    service: str = ""


def _start_run(incident_id: str) -> dict:
    inc = store.incidents[incident_id]
    run = store.create_run(incident_id)
    task = asyncio.create_task(run_incident(run["id"], inc["issue_id"]))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/incidents")
def list_incidents():
    return sorted(store.incidents.values(), key=lambda i: i["created_at"], reverse=True)


@app.post("/api/incidents", status_code=201)
def create_incident(body: IncidentIn):
    """Manual creation; the Sentry webhook (milestone 7) will call the same path."""
    return store.create_incident(body.issue_id, body.title, body.service)


@app.post("/api/incidents/{incident_id}/runs", status_code=202)
async def start_run(incident_id: str):
    if incident_id not in store.incidents:
        raise HTTPException(404, "incident not found")
    return _start_run(incident_id)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in store.runs:
        raise HTTPException(404, "run not found")
    return store.runs[run_id]


class FeedbackIn(BaseModel):
    accepted: bool


@app.post("/api/runs/{run_id}/feedback")
def feedback(run_id: str, body: FeedbackIn):
    """Engineer verdict on the RCA. Updates long-term memory and per-agent reliability."""
    if run_id not in store.runs:
        raise HTTPException(404, "run not found")
    rca = store.runs[run_id]["rca"]
    if not rca:
        raise HTTPException(409, "run has no saved RCA")
    if not memory.record_feedback(run_id, body.accepted, list(rca["confidence_breakdown"]["agents"])):
        raise HTTPException(409, "run has no saved RCA")
    store.runs[run_id]["feedback"] = "accepted" if body.accepted else "rejected"
    return {"ok": True}


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str):
    if run_id not in store.runs:
        raise HTTPException(404, "run not found")

    async def gen():
        sent = 0
        while True:
            run = store.runs[run_id]
            for ev in run["events"][sent:]:
                yield {"event": "step", "data": json.dumps(ev)}
            sent = len(run["events"])
            if run["status"] != "running":
                yield {"event": "done", "data": json.dumps({"status": run["status"], "rca": run["rca"], "error": run["error"]})}
                return
            await store.wait_for_change()

    return EventSourceResponse(gen())
