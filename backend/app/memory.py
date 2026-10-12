"""Long-term memory (SQLite): past RCAs, their outcomes, and per-agent track record.

Short-term state lives in the ADK session (`tool_context.state`); this is what
survives across incidents and restarts.
"""
import json
import re
import sqlite3
from contextlib import closing

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rca_memory (
  run_id TEXT PRIMARY KEY, issue_id TEXT, title TEXT, tokens TEXT,
  rca TEXT, confidence REAL, outcome TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS agent_stats (
  agent TEXT PRIMARY KEY, accepted INTEGER DEFAULT 0, rejected INTEGER DEFAULT 0
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def tokenize(title: str) -> set[str]:
    """Normalise an error title so 'Error in X 123' matches 'Error in X 456'."""
    return {t for t in re.findall(r"[a-z_]{3,}", title.lower())}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def save_run(run_id: str, issue_id: str, title: str, rca: dict, confidence: float) -> None:
    with closing(_conn()) as c, c:
        c.execute("INSERT OR REPLACE INTO rca_memory(run_id,issue_id,title,tokens,rca,confidence) VALUES (?,?,?,?,?,?)",
                  (run_id, issue_id, title, " ".join(sorted(tokenize(title))), json.dumps(rca), confidence))


def similar(title: str, exclude_run: str = "", min_sim: float = 0.5, limit: int = 3) -> list[dict]:
    q = tokenize(title)
    with closing(_conn()) as c:
        rows = c.execute("SELECT * FROM rca_memory WHERE run_id != ?", (exclude_run,)).fetchall()
    scored = [(_jaccard(q, set(r["tokens"].split())), r) for r in rows]
    out = [{"similarity": round(s, 2), "issue_id": r["issue_id"], "title": r["title"], "outcome": r["outcome"] or "unreviewed",
            "confidence": r["confidence"], "summary": json.loads(r["rca"]).get("summary"),
            "faulty_file": json.loads(r["rca"]).get("faulty_file"), "suggested_fix": json.loads(r["rca"]).get("suggested_fix")}
           for s, r in sorted(scored, key=lambda x: -x[0]) if s >= min_sim]
    return out[:limit]


def record_feedback(run_id: str, accepted: bool, agents: list[str]) -> bool:
    with closing(_conn()) as c, c:
        cur = c.execute("UPDATE rca_memory SET outcome=? WHERE run_id=?", ("accepted" if accepted else "rejected", run_id))
        if cur.rowcount == 0:
            return False
        col = "accepted" if accepted else "rejected"
        for a in agents:
            c.execute(f"INSERT INTO agent_stats(agent,{col}) VALUES (?,1) ON CONFLICT(agent) DO UPDATE SET {col}={col}+1", (a,))
    return True


def reliability(agent: str) -> float:
    """Smoothed historical acceptance rate in [0,1]; 0.5 with no history."""
    with closing(_conn()) as c:
        r = c.execute("SELECT accepted, rejected FROM agent_stats WHERE agent=?", (agent,)).fetchone()
    a, b = (r["accepted"], r["rejected"]) if r else (0, 0)
    return (a + 1) / (a + b + 2)
