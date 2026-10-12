"""Database schema + engine. SQLite by default, Postgres in production (same code, JSON columns on both)."""
import datetime as dt
import threading

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app import config

JSON = sa.JSON().with_variant(JSONB(), "postgresql")
meta = sa.MetaData()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


incidents = sa.Table(
    "incidents", meta,
    sa.Column("id", sa.String(16), primary_key=True),
    sa.Column("issue_id", sa.String(200), nullable=False, unique=True),  # crash fingerprint = dedupe key
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("service", sa.String(200), default=""),
    sa.Column("source", sa.String(32), default="manual"),
    sa.Column("status", sa.String(32), nullable=False, default="new"),
    sa.Column("occurrences", sa.Integer, nullable=False, default=1),
    sa.Column("created_at", sa.DateTime(timezone=True), default=_now),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), default=_now),
)
runs = sa.Table(
    "runs", meta,
    sa.Column("id", sa.String(16), primary_key=True),
    sa.Column("incident_id", sa.String(16), sa.ForeignKey("incidents.id"), nullable=False, index=True),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), default=_now, index=True),
    sa.Column("error", sa.Text),
    sa.Column("feedback", sa.String(16)),
    sa.Column("fix_skipped", sa.Text),
    *[sa.Column(c, JSON) for c in ("rca", "fix", "validation", "pr_preview", "pr")],
)
run_events = sa.Table(
    "run_events", meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("run_id", sa.String(16), sa.ForeignKey("runs.id"), nullable=False, index=True),
    sa.Column("data", JSON, nullable=False),
)
crash_reports = sa.Table(  # what the webhook received; read by the agents' get_crash_report tool
    "crash_reports", meta,
    sa.Column("issue_id", sa.String(200), primary_key=True),
    sa.Column("data", JSON, nullable=False),
)
webhook_deliveries = sa.Table(  # idempotency: providers retry, we process each delivery once
    "webhook_deliveries", meta,
    sa.Column("delivery_id", sa.String(200), primary_key=True),
    sa.Column("received_at", sa.DateTime(timezone=True), default=_now),
)
rca_memory = sa.Table(
    "rca_memory", meta,
    sa.Column("run_id", sa.String(16), primary_key=True),
    sa.Column("issue_id", sa.String(200)), sa.Column("title", sa.Text), sa.Column("tokens", sa.Text),
    sa.Column("rca", JSON), sa.Column("confidence", sa.Float), sa.Column("outcome", sa.String(16)),
    sa.Column("created_at", sa.DateTime(timezone=True), default=_now),
)
agent_stats = sa.Table(
    "agent_stats", meta,
    sa.Column("agent", sa.String(64), primary_key=True),
    sa.Column("accepted", sa.Integer, nullable=False, default=0),
    sa.Column("rejected", sa.Integer, nullable=False, default=0),
)

_engines: dict[str, sa.Engine] = {}
_lock = threading.Lock()


def engine() -> sa.Engine:
    """One engine per URL (tests point config.DATABASE_URL at a temp DB). Tables are created on first use;
    swap for Alembic migrations before changing the schema on a live database."""
    url = config.DATABASE_URL
    with _lock:
        if url not in _engines:
            kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
            eng = sa.create_engine(url, **kw)
            meta.create_all(eng)
            _engines[url] = eng
        return _engines[url]
