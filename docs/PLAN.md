# BugSmith — From Production Crash to Validated Pull Request

An AI debugging agent for web apps, inspired by Uber's Debug Assist:
**Investigate → Root cause → Fix → Validate → Iterate → Open PR**

Stack: **Google ADK (Python) + FastAPI + React (Vite) frontend + MCP servers**.

---

## 1. ADK vs LangGraph — which one?

| Concern | Google ADK | LangGraph |
|---|---|---|
| MCP tools | Native `McpToolset` (stdio / SSE / streamable HTTP) | Via `langchain-mcp-adapters` |
| Parallel subagents | Built-in `ParallelAgent` | Fan-out edges / `Send` API |
| Fix → validate → retry loop | Built-in `LoopAgent` + `escalate` to exit | Conditional edges back to a node |
| Fixed pipelines | `SequentialAgent` | Explicit graph |
| Pause for human approval mid-run | Possible (long-running tools / callbacks), less ergonomic | First-class `interrupt()` + checkpointer |
| Durable resume after crash | `DatabaseSessionService` (state, not mid-step) | Checkpointers resume from any node |
| Serving | `get_fast_api_app()` or embed `Runner` in your own FastAPI | LangGraph Server / your own FastAPI |
| Models | Gemini native; Claude/OpenAI/etc. via `LiteLlm` | Any |
| Built-in dev UI + eval | `adk web`, `adk eval` | LangGraph Studio, LangSmith |

**Recommendation: ADK.** The Debug Assist workflow maps almost 1:1 onto ADK's
workflow agents (Sequential → Parallel investigators → Loop for fix/validate),
and MCP is first-class. Pick LangGraph instead only if you need heavy
human-in-the-loop pauses mid-graph or resume-from-any-step durability. Human
approval in BugSmith happens *at the PR* (reviewers merge or not), so ADK's
model is enough.

---

## 2. Architecture

```
 ┌──────────────┐   webhook    ┌──────────────────────────── FastAPI backend ───────────────────────────┐
 │ Sentry / App │ ───────────▶ │ /webhooks/sentry  → dedupe/fingerprint → create Incident → enqueue job │
 │ error events │              │ /api/incidents, /api/runs/{id}, /api/runs/{id}/stream (SSE)            │
 └──────────────┘              └───────────────┬────────────────────────────────────────────────────────┘
                                               │ job queue (arq/Celery + Redis)
                                               ▼
                         ┌──────────────── ADK worker (Runner) ────────────────┐
                         │  DebugPipeline (SequentialAgent)                    │
                         │   1. TriageAgent                                    │
                         │   2. Investigators (ParallelAgent)                  │
                         │        ├ CodeSearchAgent   → GitHub MCP             │
                         │        ├ CrashAgent        → Sentry MCP             │
                         │        ├ LogsAgent         → Loki/CloudWatch MCP    │
                         │        ├ TraceAgent        → Jaeger/Tempo MCP       │
                         │        └ ChangeAgent       → GitHub (recent deploys)│
                         │                              + Feature-flag MCP     │
                         │   3. RootCauseAgent  (synthesize → RCA + confidence)│
                         │   4. FixLoop (LoopAgent, max 3–5 iterations)        │
                         │        ├ FixerAgent     → writes patch              │
                         │        ├ ValidatorAgent → Sandbox MCP (tests, repro)│
                         │        └ CriticAgent    → pass? escalate : retry    │
                         │   5. PRAgent → GitHub MCP (branch, commit, PR)      │
                         └─────────────────────────────────────────────────────┘
                                               │ events
                                               ▼
                         Postgres (incidents, runs, steps, RCAs, PRs, feedback)
                                               ▲
                         React frontend: incident list, live run timeline,
                         RCA view, diff viewer, PR link, 👍/👎 feedback
```

### How the pieces connect

1. **Ingest** – Your website reports errors to Sentry (frontend `@sentry/react`,
   backend SDK). A Sentry *alert rule / internal integration webhook* POSTs to
   `FastAPI /webhooks/sentry`. Verify the signature, fingerprint, and skip
   duplicates already being worked on.
2. **Queue** – FastAPI creates an `Incident` row and enqueues a job. Agent runs
   take minutes; never run them inside the request.
3. **Agent run** – A worker builds an ADK `Runner` with a session whose initial
   state contains `incident_id`, the Sentry issue id, repo, and service.
4. **Tools via MCP** – Each investigator gets only the toolsets it needs
   (least privilege, smaller prompts). Agents pass results through
   **session state** using `output_key`.
5. **Validation** – A sandbox service clones the repo at the deployed commit
   into a throwaway Docker container, applies the patch, runs lint/typecheck/
   unit tests, and runs a **reproduction test** (Playwright for UI crashes,
   pytest/jest for API crashes) that the agent writes *before* the fix.
6. **PR** – Only when validation passes. The PR body contains the RCA, the
   evidence links (Sentry issue, trace, log query), the repro test, and the
   validation output.
7. **Streaming UI** – Worker writes each ADK event to Postgres and publishes it
   to Redis pub/sub; FastAPI streams it to the browser over SSE.

---

## 3. MCP servers (the website version of Uber's 11)

| Capability | Server | Used by |
|---|---|---|
| Source code search / read / PR | GitHub MCP (official) | CodeSearch, Change, PR agents |
| Crash analytics | Sentry MCP (official) | Crash, Triage |
| Logs | Grafana MCP (Loki) or a small custom CloudWatch MCP | Logs |
| Distributed tracing | Grafana MCP (Tempo) or custom Jaeger MCP | Trace |
| Issue tracking | Jira / Linear MCP | Triage (link ticket), PR |
| Feature flags | Custom MCP over LaunchDarkly/Unleash/GrowthBook API | Change |
| Deploy history | GitHub releases/Actions or custom | Change |
| **Sandbox / validation** | **Custom MCP you build** (`run_tests`, `apply_patch`, `run_repro`) | Validator |
| Metrics (optional) | Prometheus via Grafana MCP | Logs/Trace |

Write custom servers with the Python `mcp` SDK (`FastMCP`). Start with
**GitHub + Sentry + Sandbox**; add the rest one at a time.

---

## 4. Agent design (ADK sketch)

```python
from google.adk.agents import LlmAgent, SequentialAgent, ParallelAgent, LoopAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.tool_context import ToolContext

def exit_loop(tool_context: ToolContext):
    """Call when the fix is validated and no more iterations are needed."""
    tool_context.actions.escalate = True
    return {}

crash_agent = LlmAgent(
    name="CrashAgent", model=MODEL,
    instruction="Fetch the Sentry issue {sentry_issue_id}: stack trace, breadcrumbs, "
                "release, affected users, first/last seen. Summarize facts only.",
    tools=[sentry_toolset], output_key="crash_findings",
)
# CodeSearchAgent, LogsAgent, TraceAgent, ChangeAgent defined the same way,
# each with its own toolset and output_key.

investigators = ParallelAgent(name="Investigators",
    sub_agents=[crash_agent, code_agent, logs_agent, trace_agent, change_agent])

root_cause = LlmAgent(
    name="RootCauseAgent", model=STRONG_MODEL,
    instruction="Using {crash_findings} {code_findings} {log_findings} {trace_findings} "
                "{change_findings}, produce an RCA: hypothesis, evidence for/against, "
                "faulty file+lines, confidence 0-1. If confidence < 0.5, say what is missing.",
    output_schema=RCA, output_key="rca",
)

fixer = LlmAgent(name="FixerAgent", tools=[github_read],
    instruction="Write a failing repro test, then a minimal patch for {rca}. "
                "Previous validation feedback: {validation_report?}",
    output_key="patch")
validator = LlmAgent(name="ValidatorAgent", tools=[sandbox_toolset],
    instruction="Apply {patch} in the sandbox. Run repro test (must fail before, pass after), "
                "lint, typecheck, unit tests. Report results.",
    output_key="validation_report")
critic = LlmAgent(name="CriticAgent", tools=[exit_loop],
    instruction="If {validation_report} is all green and the patch is minimal and "
                "addresses {rca}, call exit_loop. Otherwise explain what to change.")

fix_loop = LoopAgent(name="FixLoop", sub_agents=[fixer, validator, critic], max_iterations=4)

pipeline = SequentialAgent(name="DebugPipeline",
    sub_agents=[triage, investigators, root_cause, fix_loop, pr_agent])
```

Guardrails (ADK callbacks):
- `before_tool_callback`: block writes outside the sandbox and any push to `main`.
- `before_agent_callback` on PRAgent: skip if validation failed or RCA confidence
  is low → publish RCA only (no PR), like Uber's "RCA-only" mode.
- Token / cost / iteration budgets per run.

Models: use a strong reasoning model for RootCause and Fixer, a cheaper/faster one
for investigators. ADK supports Gemini natively and Claude and others via `LiteLlm`.

---

## 5. Backend (FastAPI)

```
backend/
  app/main.py              FastAPI app, routers, CORS
  app/api/webhooks.py      POST /webhooks/sentry (HMAC verify, dedupe)
  app/api/incidents.py     GET /api/incidents, GET /api/incidents/{id}
  app/api/runs.py          POST /api/incidents/{id}/runs (manual trigger),
                           GET  /api/runs/{id}, GET /api/runs/{id}/stream (SSE),
                           POST /api/runs/{id}/feedback
  app/worker.py            arq worker: builds Runner, iterates run_async events
  app/agents/              pipeline.py, investigators.py, fixer.py, prompts/
  app/mcp/                 toolset factories + custom servers (sandbox, flags)
  app/db/                  SQLAlchemy models + Alembic
  app/sandbox/             Docker runner (clone, apply, test, Playwright)
```

Data model: `Incident`, `Run` (status, cost, timings), `RunEvent` (agent,
type, payload), `RCA`, `Patch`, `ValidationResult`, `PullRequest`, `Feedback`.

Use `DatabaseSessionService` (Postgres) for ADK sessions so runs survive restarts.

## 6. Frontend (React + Vite + Tailwind + TanStack Query)

- **Incidents** – table: error, service, users affected, status
  (investigating / RCA ready / fixing / PR open / merged / rejected).
- **Run view** – live timeline of agents (parallel investigators shown side by
  side), tool calls, and reasoning summaries streamed via SSE.
- **RCA panel** – hypothesis, confidence, evidence links.
- **Patch panel** – diff viewer (`react-diff-viewer`), validation results per
  iteration.
- **PR card** – link, CI status, merged/closed.
- **Feedback** – 👍/👎 + reason. This becomes your eval dataset.
- **Settings** – repos, MCP connections, budgets, auto-PR on/off.

## 7. Trust & metrics (the hard part Uber highlights)

- Start in **RCA-only mode**; enable auto-PR per repo once RCA accuracy is good.
- Always include a repro test; "failing before, passing after" is the main
  signal that the fix is right.
- Draft PRs, labelled `bugsmith`, assigned to the code owner.
- Track: RCA acceptance, PR merge rate (Uber went 5% → 30%), time-to-PR,
  cost per run, iterations per fix.
- Build an eval set of past incidents with known fixes; run `adk eval` on every
  prompt change.

---

## 8. Milestones

| # | Milestone | Outcome |
|---|---|---|
| 0 | Demo target | A small sample web app (e.g. Next.js + FastAPI) with seeded bugs, Sentry wired in |
| 1 | Skeleton | FastAPI + Postgres + Redis + React shell, docker-compose |
| 2 | Single agent RCA | One `LlmAgent` + Sentry MCP + GitHub MCP → RCA shown in UI |
| 3 | Parallel investigators | `ParallelAgent` with logs/traces/changes; SSE timeline |
| 4 | Sandbox validation | Custom sandbox MCP; repro test + test suite in Docker |
| 5 | Fix loop | `LoopAgent` Fixer/Validator/Critic with budgets |
| 6 | PR creation | GitHub MCP opens draft PR with RCA + evidence |
| 7 | Webhook automation | Sentry alert → auto run, dedupe, rate limits |
| 8 | Feedback + evals | 👍/👎, eval set, metrics dashboard |
| 9 | Hardening | Auth (OAuth/GitHub App), secrets, multi-repo, more MCPs (Jira, flags) |

Build M0–M2 first: a working end-to-end "crash → RCA" demo is the foundation for
everything else.
