# BugSmith

AI debugging agent: production crash → root cause → validated fix → PR. Built with Google ADK, FastAPI and React.
See [docs/PLAN.md](docs/PLAN.md) for the full architecture and milestones.

**Status:** Milestones 0–2 done (demo app with a seeded bug, API + SSE streaming, crash → RCA agent, React UI). Fix loop, sandbox validation and PR creation come next.

## Run it
```bash
# backend
cd backend && python -m venv ../.venv && . ../.venv/bin/activate && pip install -r requirements.txt
cp .env.example .env   # add GOOGLE_API_KEY (free at aistudio.google.com)
set -a; . ./.env; set +a
uvicorn app.main:app --reload            # http://localhost:8000

# frontend (second terminal)
cd frontend && npm install && npm run dev  # http://localhost:5173
```
Click **Add incident** (defaults to the seeded `DEMO-1` crash), then **Investigate**.

## Test (no API key needed — uses a fake LLM)
```bash
cd backend && python -m pytest -q
```

## Layout
- `demo-app/` — sample service with a seeded bug (`cart_total` crashes on `discount=None`)
- `backend/app/agents/rca.py` — Investigator (tools) → RcaWriter (structured RCA) pipeline
- `backend/app/tools/local.py` — fixture crash report + read-only code search, used until Sentry/GitHub MCP URLs are set
- `backend/app/main.py` — incidents/runs API and SSE stream
- `frontend/` — incident list, live timeline, RCA view
