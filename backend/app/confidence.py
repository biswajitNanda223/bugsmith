"""Deterministic confidence aggregation.

LLMs are poorly calibrated about their own certainty, so the model's number is an
input, not the answer. The final score combines:
  * each agent's self-reported confidence (mean of investigators, capped by the writer),
  * how much concrete evidence backs the RCA,
  * each agent's historical reliability (from user feedback),
  * what happened to similar past incidents (memory).
"""
from app import memory


def aggregate(reports: dict[str, dict], rca: dict, similar_past: list[dict]) -> dict:
    """reports: investigator name -> {summary, evidence, confidence}."""
    inv = {n: float(r.get("confidence", 0.0)) for n, r in reports.items()}
    wri = float(rca.get("confidence", 0.0))
    inv_mean = sum(inv.values()) / len(inv) if inv else 0.0
    base = min(inv_mean, wri)  # the writer cannot be more certain than its sources
    agents = (*inv, "RcaWriter")

    n_evidence = len(rca.get("evidence", []))
    evidence_factor = min(1.0, n_evidence / 2)  # <2 pieces of evidence caps the score

    rel = {a: memory.reliability(a) for a in agents}
    reliability_factor = 0.8 + 0.4 * (sum(rel.values()) / len(rel))  # 0.8..1.2, neutral 1.0 at 0.5

    memory_delta = 0.0
    for s in similar_past:
        if s["outcome"] == "accepted":
            memory_delta += 0.05 * s["similarity"]
        elif s["outcome"] == "rejected":
            memory_delta -= 0.10 * s["similarity"]

    final = max(0.0, min(1.0, base * evidence_factor * reliability_factor + memory_delta))
    return {
        "final": round(final, 2),
        "agents": {**inv, "RcaWriter": wri},
        "evidence_factor": round(evidence_factor, 2),
        "reliability": {a: round(v, 2) for a, v in rel.items()},
        "memory_delta": round(memory_delta, 2),
        "similar_incidents": len(similar_past),
        "action": "auto_pr_allowed" if final >= 0.75 else "rca_only" if final >= 0.4 else "needs_human",
    }
