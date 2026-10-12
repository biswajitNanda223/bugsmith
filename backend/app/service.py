import asyncio

from app.runner import run_incident
from app.store import store

_tasks: set[asyncio.Task] = set()


def start_run(incident_id: str) -> dict:
    """Create a run and execute it in the background (a separate worker process comes with the job queue)."""
    run = store.create_run(incident_id)
    inc = store.get_incident(incident_id)
    task = asyncio.create_task(run_incident(run["id"], inc["issue_id"]))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run
