"""Local fallback tools, used when no Sentry/GitHub MCP server is configured.

Read-only and confined to the configured repo path.
"""
import json

from app import config

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist"}


def get_crash_report(issue_id: str) -> dict:
    """Fetch a crash report (stack trace, release, impact) by issue id, e.g. DEMO-1."""
    from app.store import store  # reports received by the webhook take precedence over sample fixtures

    if (report := store.get_crash_report(issue_id)) is not None:
        return report
    path = config.FIXTURES_DIR / f"sentry_issue_{issue_id}.json"
    if not path.is_file() or path.parent != config.FIXTURES_DIR:
        return {"error": f"issue {issue_id} not found"}
    return json.loads(path.read_text())


def _safe(rel: str):
    p = (config.REPO_PATH / rel).resolve()
    if config.REPO_PATH not in p.parents and p != config.REPO_PATH:
        return None
    return p


def search_code(query: str) -> dict:
    """Search source files in the repo for a case-insensitive substring. Returns up to 30 matches."""
    q = query.lower()
    hits = []
    for p in config.REPO_PATH.rglob("*"):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        if p.suffix not in {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md"}:
            continue
        try:
            lines = p.read_text().splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(lines, 1):
            if q in line.lower():
                hits.append({"file": str(p.relative_to(config.REPO_PATH)), "line": i, "text": line.strip()})
                if len(hits) >= 30:
                    return {"matches": hits}
    return {"matches": hits}


def read_file(path: str, start_line: int = 1, end_line: int = 200) -> dict:
    """Read lines [start_line, end_line] of a repo file (1-indexed, max 400 lines)."""
    p = _safe(path)
    if p is None or not p.is_file():
        return {"error": f"cannot read {path}"}
    lines = p.read_text().splitlines()
    end_line = min(end_line, start_line + 399)
    chunk = [f"{i}: {l}" for i, l in enumerate(lines[start_line - 1 : end_line], start_line)]
    return {"path": path, "total_lines": len(lines), "content": "\n".join(chunk)}


def _fixture(name: str, service: str):
    path = config.FIXTURES_DIR / f"{name}_{service}.json"
    if path.parent != config.FIXTURES_DIR or not path.is_file():
        return None
    return json.loads(path.read_text())


def query_logs(service: str, contains: str = "") -> dict:
    """Search recent application logs for a service; optional case-insensitive substring filter. Max 50 lines."""
    rows = _fixture("logs", service)
    if rows is None:
        return {"error": f"no logs for service {service}"}
    return {"lines": [r for r in rows if contains.lower() in json.dumps(r).lower()][:50]}


def get_traces(service: str, route: str = "") -> dict:
    """Fetch distributed traces for a service, optionally filtered by route substring. Shows which span failed."""
    rows = _fixture("traces", service)
    if rows is None:
        return {"error": f"no traces for service {service}"}
    return {"traces": [r for r in rows if route.lower() in r["route"].lower()][:20]}


def recent_changes(service: str) -> dict:
    """List recent deploys, commits and feature-flag changes for a service."""
    data = _fixture("changes", service)
    return data if data is not None else {"error": f"no change history for service {service}"}
