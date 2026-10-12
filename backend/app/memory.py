"""Long-term memory: past RCAs, their outcomes, and per-agent track record (same database as the store).

Short-term state lives in the ADK session (`tool_context.state`); this is what survives across incidents and restarts.
"""
import re

import sqlalchemy as sa

from app import db


def tokenize(title: str) -> set[str]:
    """Normalise an error title so 'Error in X 123' matches 'Error in X 456'."""
    return {t for t in re.findall(r"[a-z_]{3,}", title.lower())}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def save_run(run_id: str, issue_id: str, title: str, rca: dict, confidence: float) -> None:
    t = db.rca_memory
    vals = dict(issue_id=issue_id, title=title, tokens=" ".join(sorted(tokenize(title))), rca=rca, confidence=confidence)
    with db.engine().begin() as c:
        if c.execute(sa.select(t.c.run_id).where(t.c.run_id == run_id)).first():
            c.execute(t.update().where(t.c.run_id == run_id).values(**vals))
        else:
            c.execute(t.insert().values(run_id=run_id, **vals))


def similar(title: str, exclude_run: str = "", min_sim: float = 0.5, limit: int = 3) -> list[dict]:
    q = tokenize(title)
    with db.engine().connect() as c:
        rows = c.execute(sa.select(db.rca_memory).where(db.rca_memory.c.run_id != exclude_run)).fetchall()
    scored = sorted(((_jaccard(q, set((r.tokens or "").split())), r) for r in rows), key=lambda x: -x[0])
    return [{"similarity": round(s, 2), "issue_id": r.issue_id, "title": r.title, "outcome": r.outcome or "unreviewed",
             "confidence": r.confidence, "summary": r.rca.get("summary"), "faulty_file": r.rca.get("faulty_file"),
             "suggested_fix": r.rca.get("suggested_fix")} for s, r in scored if s >= min_sim][:limit]


def record_feedback(run_id: str, accepted: bool, agents: list[str]) -> bool:
    col = "accepted" if accepted else "rejected"
    with db.engine().begin() as c:
        n = c.execute(db.rca_memory.update().where(db.rca_memory.c.run_id == run_id).values(outcome=col)).rowcount
        if n == 0:
            return False
        for a in agents:
            if c.execute(sa.select(db.agent_stats.c.agent).where(db.agent_stats.c.agent == a)).first():
                c.execute(db.agent_stats.update().where(db.agent_stats.c.agent == a).values({col: db.agent_stats.c[col] + 1}))
            else:
                c.execute(db.agent_stats.insert().values(agent=a, accepted=int(accepted), rejected=int(not accepted)))
    return True


def reliability(agent: str) -> float:
    """Smoothed historical acceptance rate in [0,1]; 0.5 with no history."""
    with db.engine().connect() as c:
        r = c.execute(sa.select(db.agent_stats).where(db.agent_stats.c.agent == agent)).first()
    a, b = (r.accepted, r.rejected) if r else (0, 0)
    return (a + 1) / (a + b + 2)
