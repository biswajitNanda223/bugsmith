"""Milestone 5: Fixer -> Validator -> Critic loop.

Fixer is an LLM; Validator and Critic are deterministic agents. Whether a patch applies and
passes tests is a fact, not an opinion, so it is checked by code (the sandbox), not by a model.
The loop only runs when the aggregated confidence allows it.
"""
import asyncio
import re
from typing import AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from app import confidence, sandbox
from app.tools import local

MAX_ATTEMPTS = 3
MAX_CHANGED_LINES = 30


def submit_fix(patch: str, repro_test: str, explanation: str, workdir: str = ".", test_dir: str = "",
               tool_context: ToolContext = None) -> dict:
    """Submit your candidate fix. Call exactly once per attempt, last.

    patch: unified diff in git format (--- a/path, +++ b/path, @@ hunks), paths relative to the repo root.
    repro_test: complete pytest file that FAILS on the current code because of this bug and passes with your patch.
    workdir: subdirectory that is the import root for the tests (e.g. 'demo-app'). test_dir: existing test dir relative to workdir, if any.
    explanation: one sentence on why the patch fixes the root cause.
    """
    n = tool_context.state.get("attempt", 0) + 1
    tool_context.state["attempt"] = n
    tool_context.state["fix"] = {"patch": patch, "repro_test": repro_test, "explanation": explanation,
                                 "workdir": workdir, "test_dir": test_dir, "attempt": n}
    return {"recorded": True, "attempt": n}


def changed_lines(patch: str) -> int:
    return sum(1 for l in patch.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))


def patched_files(patch: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"^\+\+\+ b/(\S+)", patch, re.M)}


def gate(callback_context: CallbackContext):
    """Decide from aggregated confidence whether the fix loop may run. Skips the loop otherwise."""
    st = callback_context.state
    reports = {k[len("report_"):]: v for k, v in st.to_dict().items() if k.startswith("report_")}
    rca = st.get("rca") or {}
    if isinstance(rca, str):
        import json
        rca = json.loads(rca)
    b = confidence.aggregate(reports, rca, st.get("similar_past") or [])
    st["confidence"] = b
    if b["action"] != "auto_pr_allowed":
        st["fix_skipped"] = f"confidence {b['final']:.0%} below auto-fix threshold ({b['action']})"
        return types.Content(role="model", parts=[types.Part(text=f"Fix loop skipped: {st['fix_skipped']}")])
    return None


class ValidatorAgent(BaseAgent):
    """Runs the submitted fix through the sandbox and records the result in state."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        fix = ctx.session.state.get("fix")
        if not fix:
            result = {"passed": False, "steps": [{"name": "submitted", "ok": False, "detail": "no fix submitted"}]}
        else:
            result = (await asyncio.to_thread(sandbox.validate_patch, fix["patch"], fix["repro_test"],
                                              fix.get("workdir", "."), fix.get("test_dir", ""))).to_dict()
        result["attempt"] = (fix or {}).get("attempt", 0)
        yield Event(author=self.name, actions=EventActions(state_delta={"validation": result}),
                    content=types.Content(role="model", parts=[types.Part(text=f"validation {'passed' if result['passed'] else 'failed'}")]))


class CriticAgent(BaseAgent):
    """Accepts a validated, minimal, on-target patch; otherwise writes feedback for the next attempt."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        st = ctx.session.state
        fix, val, rca = st.get("fix") or {}, st.get("validation") or {}, st.get("rca") or {}
        if isinstance(rca, str):
            import json
            rca = json.loads(rca)
        problems = []
        if not val.get("passed"):
            failed = next((s for s in val.get("steps", []) if not s["ok"]), {"name": "unknown", "detail": ""})
            problems.append(f"validation failed at '{failed['name']}':\n{failed['detail']}")
        target = rca.get("faulty_file", "")
        if target and not any(f.endswith(target) or target.endswith(f) for f in patched_files(fix.get("patch", ""))):
            problems.append(f"patch does not touch the faulty file {target}")
        if changed_lines(fix.get("patch", "")) > MAX_CHANGED_LINES:
            problems.append(f"patch changes more than {MAX_CHANGED_LINES} lines; make it minimal")
        done = not problems
        attempt = fix.get("attempt", 0)
        exhausted = attempt >= MAX_ATTEMPTS
        delta = {"critique": "; ".join(problems), "fix_accepted": done}
        text = "accepted" if done else ("giving up" if exhausted else "retry: " + "; ".join(problems))
        yield Event(author=self.name, actions=EventActions(state_delta=delta, escalate=done or exhausted),
                    content=types.Content(role="model", parts=[types.Part(text=text)]))


def build_fix_loop(model) -> LoopAgent:
    fixer = LlmAgent(
        name="FixerAgent", model=model,
        instruction=(
            "ROLE:FixerAgent. Fix the root cause below with the smallest correct change.\n"
            "Root cause analysis: {rca}\n"
            "Previous attempt feedback (empty on first attempt): {critique?}\n"
            "1. Read the faulty file with read_file (get exact current lines; the diff must apply to them). "
            "2. Write a pytest repro test that fails on the current code. 3. Write a minimal unified diff. "
            "4. Call submit_fix once. Do not touch unrelated code."),
        tools=[local.read_file, local.search_code, submit_fix],
    )
    return LoopAgent(name="FixLoop", max_iterations=MAX_ATTEMPTS, before_agent_callback=gate,
                     sub_agents=[fixer, ValidatorAgent(name="Validator"), CriticAgent(name="Critic")])
