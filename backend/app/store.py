"""Persistent incident/run store (SQLAlchemy; SQLite or Postgres). All methods return plain dicts."""
import asyncio
import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa

from app import db


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(v):
    if isinstance(v, dt.datetime):
        return (v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)).isoformat()
    return v


def _row(r) -> dict[str, Any]:
    return {k: _iso(v) for k, v in r._mapping.items()}


def _aware(v: dt.datetime) -> dt.datetime:
    return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)


class Store:
    def __init__(self) -> None:
        self._cond: asyncio.Condition | None = None  # created lazily inside the running loop

    # ---- incidents -------------------------------------------------------------------------
    def upsert_incident(self, issue_id: str, title: str, service: str = "", source: str = "manual") -> tuple[dict, bool]:
        """Create, or if this crash (issue_id) is already known bump its occurrence count. Returns (incident, created)."""
        t = db.incidents
        with db.engine().begin() as c:
            row = c.execute(sa.select(t).where(t.c.issue_id == issue_id)).first()
            if row:
                c.execute(t.update().where(t.c.id == row.id).values(occurrences=t.c.occurrences + 1, last_seen_at=_now()))
                return self._incident(c, row.id), False
            iid = uuid.uuid4().hex[:8]
            c.execute(t.insert().values(id=iid, issue_id=issue_id, title=title, service=service, source=source, status="new",
                                        occurrences=1, created_at=_now(), last_seen_at=_now()))
            return self._incident(c, iid), True

    def create_incident(self, issue_id: str, title: str, service: str = "") -> dict:
        return self.upsert_incident(issue_id, title, service)[0]

    def _incident(self, c, incident_id: str) -> dict | None:
        row = c.execute(sa.select(db.incidents).where(db.incidents.c.id == incident_id)).first()
        if not row:
            return None
        inc = _row(row)
        inc["run_ids"] = [r.id for r in c.execute(sa.select(db.runs.c.id).where(db.runs.c.incident_id == incident_id)
                                                  .order_by(db.runs.c.started_at))]
        return inc

    def get_incident(self, incident_id: str) -> dict | None:
        with db.engine().connect() as c:
            return self._incident(c, incident_id)

    def list_incidents(self) -> list[dict]:
        with db.engine().connect() as c:
            ids = [r.id for r in c.execute(sa.select(db.incidents.c.id).order_by(db.incidents.c.last_seen_at.desc()))]
            return [self._incident(c, i) for i in ids]

    def set_incident_status(self, incident_id: str, status: str) -> None:
        with db.engine().begin() as c:
            c.execute(db.incidents.update().where(db.incidents.c.id == incident_id).values(status=status))

    # ---- runs ------------------------------------------------------------------------------
    def create_run(self, incident_id: str) -> dict:
        rid = uuid.uuid4().hex[:8]
        with db.engine().begin() as c:
            c.execute(db.runs.insert().values(id=rid, incident_id=incident_id, status="running", started_at=_now()))
            c.execute(db.incidents.update().where(db.incidents.c.id == incident_id).values(status="investigating"))
        return self.get_run(rid)

    def get_run(self, run_id: str) -> dict | None:
        with db.engine().connect() as c:
            row = c.execute(sa.select(db.runs).where(db.runs.c.id == run_id)).first()
            if not row:
                return None
            run = _row(row)
            run["events"] = [e.data for e in c.execute(sa.select(db.run_events.c.data).where(db.run_events.c.run_id == run_id)
                                                      .order_by(db.run_events.c.id))]
            return run

    def events_since(self, run_id: str, offset: int) -> tuple[list[dict], str]:
        """(new events, run status) - the SSE stream's cheap poll."""
        with db.engine().connect() as c:
            ev = [e.data for e in c.execute(sa.select(db.run_events.c.data).where(db.run_events.c.run_id == run_id)
                                            .order_by(db.run_events.c.id).offset(offset))]
            status = c.execute(sa.select(db.runs.c.status).where(db.runs.c.id == run_id)).scalar_one()
            return ev, status

    def update_run(self, run_id: str, **fields) -> None:
        with db.engine().begin() as c:
            c.execute(db.runs.update().where(db.runs.c.id == run_id).values(**fields))

    def set_feedback(self, run_id: str, verdict: str) -> None:
        self.update_run(run_id, feedback=verdict)

    async def add_event(self, run_id: str, event: dict) -> None:
        with db.engine().begin() as c:
            c.execute(db.run_events.insert().values(run_id=run_id, data={**event, "ts": _now().isoformat()}))
        await self._notify()

    async def finish(self, run_id: str, status: str, rca: dict | None = None, error: str | None = None) -> None:
        with db.engine().begin() as c:
            c.execute(db.runs.update().where(db.runs.c.id == run_id).values(status=status, rca=rca, error=error))
            inc_id = c.execute(sa.select(db.runs.c.incident_id).where(db.runs.c.id == run_id)).scalar_one()
            c.execute(db.incidents.update().where(db.incidents.c.id == inc_id)
                      .values(status="rca_ready" if status == "done" else "failed"))
        await self._notify()

    async def _notify(self) -> None:
        if self._cond is None:
            self._cond = asyncio.Condition()
        async with self._cond:
            self._cond.notify_all()

    async def wait_for_change(self, timeout: float = 2) -> None:
        """Wakes on in-process changes; the short timeout also covers writes from another process (worker)."""
        if self._cond is None:
            self._cond = asyncio.Condition()
        async with self._cond:
            try:
                await asyncio.wait_for(self._cond.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    # ---- dedupe / rate limiting -------------------------------------------------------------
    def latest_run(self, incident_id: str) -> dict | None:
        with db.engine().connect() as c:
            row = c.execute(sa.select(db.runs.c.id, db.runs.c.status, db.runs.c.started_at).where(db.runs.c.incident_id == incident_id)
                            .order_by(db.runs.c.started_at.desc()).limit(1)).first()
            return _row(row) if row else None

    def runs_started_since(self, minutes: int) -> int:
        since = _now() - dt.timedelta(minutes=minutes)
        with db.engine().connect() as c:
            return c.execute(sa.select(sa.func.count()).select_from(db.runs).where(db.runs.c.started_at >= since)).scalar_one()

    # ---- webhook inputs ----------------------------------------------------------------------
    def claim_delivery(self, delivery_id: str) -> bool:
        """True the first time a delivery id is seen (providers retry; each delivery is processed once)."""
        try:
            with db.engine().begin() as c:
                c.execute(db.webhook_deliveries.insert().values(delivery_id=delivery_id, received_at=_now()))
            return True
        except sa.exc.IntegrityError:
            return False

    def save_crash_report(self, issue_id: str, data: dict) -> None:
        t = db.crash_reports
        with db.engine().begin() as c:
            if c.execute(sa.select(t.c.issue_id).where(t.c.issue_id == issue_id)).first():
                c.execute(t.update().where(t.c.issue_id == issue_id).values(data=data))
            else:
                c.execute(t.insert().values(issue_id=issue_id, data=data))

    def get_crash_report(self, issue_id: str) -> dict | None:
        with db.engine().connect() as c:
            row = c.execute(sa.select(db.crash_reports.c.data).where(db.crash_reports.c.issue_id == issue_id)).first()
            return row.data if row else None


store = Store()
