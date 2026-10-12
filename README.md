# BugSmith

AI debugging agent: production crash → root cause → validated fix → draft PR. Google ADK + FastAPI + React.
Architecture and milestones: [docs/PLAN.md](docs/PLAN.md).

## Quick start (mise)
Tools, env vars and tasks live in [`mise.toml`](mise.toml); Python deps in [`backend/pyproject.toml`](backend/pyproject.toml) (uv). No `requirements.txt`.

```bash
mise install                              # python 3.13, node 22, uv
cp mise.local.toml.example mise.local.toml   # git-ignored: put GOOGLE_API_KEY etc. here
mise run setup                            # uv sync + npm install
mise run dev                              # API :8000 + UI :5173
mise run test                             # no API key needed (fake LLM)
```
Open http://localhost:5173, **Add incident** (`DEMO-1`), **Investigate**.
API docs (auto-generated): Swagger UI http://localhost:8000/docs · ReDoc http://localhost:8000/redoc · spec http://localhost:8000/openapi.json

| Task | What |
|---|---|
| `mise run adk:web` / `adk:run` | ADK dev UI / terminal chat on `bugsmith_agent` (scaffolded with `adk create`) |
| `mise run adk:new <name>` | scaffold another ADK agent app with `adk create` |
| `mise run db:up` | local Postgres (`compose.yaml`) |
| `mise run sandbox:build` | build the sandbox image for `BUGSMITH_SANDBOX=docker` |

## What's built
- **Investigate**: five parallel agents (crash, code, logs, traces, changes), each reporting its own confidence → RCA writer.
- **Confidence**: final score computed in code (`app/confidence.py`) from agent scores, evidence, each agent's track record and similar past incidents. ≥ 0.75 allows an auto-fix, ≥ 0.4 RCA only, otherwise needs a human.
- **Memory**: past RCAs, outcomes and per-agent reliability, updated by 👍/👎 (`app/memory.py`).
- **Fix loop** (`app/agents/fix.py`): Fixer (LLM) → Validator (sandbox) → Critic, up to 3 attempts; the repro test must fail before the patch and pass after.
- **Draft PR** (`app/pr.py`): preview in the UI; **Open draft PR** pushes `bugsmith/<issue>` (never main). `BUGSMITH_AUTO_PR=1` skips the click.
- **Sentry webhook** (`POST /webhooks/sentry`): HMAC-verified (rejects everything if `SENTRY_WEBHOOK_SECRET` is unset), idempotent per `Request-ID`, repeat crashes bump an occurrence counter instead of creating duplicates. With `BUGSMITH_AUTO_RUN=1` it starts an investigation unless one is running or the crash was investigated within `BUGSMITH_RUN_COOLDOWN_MIN` (60), and stops at `BUGSMITH_MAX_RUNS_PER_HOUR` (10).
- **Storage**: SQLAlchemy, SQLite by default, Postgres via `BUGSMITH_DATABASE_URL=postgresql+psycopg://...`. Tables are created on startup (add Alembic before changing the schema on a live DB). Test suite on Postgres: `BUGSMITH_TEST_DATABASE_URL=postgresql+psycopg://... mise run test` (use a throwaway DB; tables are dropped between tests).
- **Sandbox** (`app/sandbox.py`, also an MCP server): `local` copies the repo and scrubs the environment; `docker` runs tests with no network, read-only mounts, dropped capabilities, non-root, memory/CPU/pid limits.

## Not done yet
Background worker process + queue (runs execute inside the API process), Alembic migrations, auth on the API/UI, the Docker mode has only been verified at the command level (no Docker daemon was available while building), real Sentry/GitHub MCP servers are wired but untested, and the agents have only run against a scripted fake model, not a live one.
