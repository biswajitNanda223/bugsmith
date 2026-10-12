# BugSmith

AI debugging agent: production crash → root cause → validated fix → PR. Built with Google ADK, FastAPI and React.
See [docs/PLAN.md](docs/PLAN.md) for the full architecture and milestones.

**Status:** Milestones 0–6 done.
- Five parallel investigators (crash, code, logs, traces, changes), each reporting its own confidence → RCA writer.
- Final confidence is computed in code (`app/confidence.py`) from agent scores, evidence, each agent's track record and similar past incidents; it decides `auto_pr_allowed` / `rca_only` / `needs_human`.
- Long-term memory + 👍/👎 feedback in SQLite (`app/memory.py`).
- Patch-validation sandbox (`app/sandbox.py`, also an MCP server): repro test must fail before the patch and pass after, then existing tests run.

- Fix loop (`app/agents/fix.py`): runs only if confidence ≥ 0.75. FixerAgent (LLM) → Validator (sandbox, deterministic) → Critic (deterministic: validated, touches the faulty file, ≤ 30 changed lines); up to 3 attempts.
- Draft PR (`app/pr.py`): the UI shows the patch, sandbox checks and PR preview; **Open draft PR** pushes a `bugsmith/<issue>` branch (never main) and opens a draft. Needs `GITHUB_TOKEN` and `BUGSMITH_GITHUB_REPO` on the server. `BUGSMITH_AUTO_PR=1` opens it without a click (off by default).

Next: Sentry webhook, Postgres, container isolation for the sandbox. The sandbox uses a temp copy + scrubbed env but not container isolation yet.

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
- `backend/app/agents/rca.py` — parallel investigators → RcaWriter (structured RCA)
- `backend/app/sandbox.py`, `backend/app/mcp_servers/sandbox_server.py` — patch validation + its MCP server
- `backend/app/tools/local.py` — fixture crash report + read-only code search, used until Sentry/GitHub MCP URLs are set
- `backend/app/main.py` — incidents/runs API and SSE stream
- `frontend/` — incident list, live timeline, RCA view
