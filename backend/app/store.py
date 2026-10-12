"""In-memory incident/run store (swap for Postgres in a later milestone)."""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self) -> None:
        self.incidents: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self._cond = asyncio.Condition()

    def create_incident(self, issue_id: str, title: str, service: str = "") -> dict:
        for inc in self.incidents.values():  # dedupe on issue id
            if inc["issue_id"] == issue_id:
                return inc
        inc = {"id": uuid.uuid4().hex[:8], "issue_id": issue_id, "title": title,
               "service": service, "status": "new", "created_at": _now(), "run_ids": []}
        self.incidents[inc["id"]] = inc
        return inc

    def create_run(self, incident_id: str) -> dict:
        run = {"id": uuid.uuid4().hex[:8], "incident_id": incident_id, "status": "running",
               "events": [], "rca": None, "error": None, "started_at": _now()}
        self.runs[run["id"]] = run
        self.incidents[incident_id]["run_ids"].append(run["id"])
        self.incidents[incident_id]["status"] = "investigating"
        return run

    async def add_event(self, run_id: str, event: dict) -> None:
        async with self._cond:
            self.runs[run_id]["events"].append({**event, "ts": _now()})
            self._cond.notify_all()

    async def finish(self, run_id: str, status: str, rca: dict | None = None, error: str | None = None) -> None:
        async with self._cond:
            run = self.runs[run_id]
            run.update(status=status, rca=rca, error=error)
            self.incidents[run["incident_id"]]["status"] = "rca_ready" if status == "done" else "failed"
            self._cond.notify_all()

    async def wait_for_change(self, timeout: float = 15) -> None:
        async with self._cond:
            try:
                await asyncio.wait_for(self._cond.wait(), timeout)
            except asyncio.TimeoutError:
                pass


store = Store()
