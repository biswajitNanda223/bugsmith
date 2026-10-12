"""Runs an ADK pipeline for one incident and records each event in the store."""
import asyncio
import json
import logging

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app import config, confidence, memory, pr
from app.agents.pipeline import build_full_pipeline
from app.store import store

log = logging.getLogger("bugsmith.runner")
APP_NAME = "bugsmith"


async def run_incident(run_id: str, issue_id: str, agent=None) -> None:
    agent = agent or build_full_pipeline()
    sessions = InMemorySessionService()
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=sessions)
    session = await sessions.create_session(app_name=APP_NAME, user_id="bugsmith", state={"issue_id": issue_id})
    msg = types.Content(role="user", parts=[types.Part(text=f"Investigate crash {issue_id}.")])
    try:
        async for ev in runner.run_async(user_id="bugsmith", session_id=session.id, new_message=msg):
            delta = ev.actions.state_delta if ev.actions else None
            if delta and "validation" in delta:
                await store.add_event(run_id, {"agent": ev.author, "type": "validation", **delta["validation"]})
            for part in (ev.content.parts if ev.content and ev.content.parts else []):
                if part.function_call:
                    await store.add_event(run_id, {"agent": ev.author, "type": "tool_call",
                                                   "name": part.function_call.name, "args": dict(part.function_call.args or {})})
                elif part.function_response:
                    await store.add_event(run_id, {"agent": ev.author, "type": "tool_result",
                                                   "name": part.function_response.name})
                elif part.text and ev.is_final_response():
                    await store.add_event(run_id, {"agent": ev.author, "type": "message", "text": part.text})
        state = (await sessions.get_session(app_name=APP_NAME, user_id="bugsmith", session_id=session.id)).state
        final = state.get("rca")
        if isinstance(final, str):
            final = json.loads(final)
        inc = store.get_incident(store.get_run(run_id)["incident_id"])
        if final:
            final["confidence_breakdown"] = state.get("confidence") or confidence.aggregate(
                {k[len("report_"):]: v for k, v in state.items() if k.startswith("report_")}, final, state.get("similar_past") or [])
            await store.add_event(run_id, {"agent": "Confidence", "type": "confidence", **final["confidence_breakdown"]})
            memory.save_run(run_id, issue_id, inc["title"], final, final["confidence_breakdown"]["final"])
        fix, validation = state.get("fix"), state.get("validation")
        updates = {"fix": fix, "validation": validation, "fix_skipped": state.get("fix_skipped")}
        if final and state.get("fix_accepted"):
            updates["pr_preview"] = pr.preview({"rca": final, "fix": fix, "validation": validation}, inc)
        store.update_run(run_id, **updates)
        if updates.get("pr_preview") and config.AUTO_PR and config.GITHUB_TOKEN and config.GITHUB_REPO:
            try:
                opened = await asyncio.to_thread(pr.open_draft_pr, updates["pr_preview"], fix, repo=config.GITHUB_REPO,
                                                 base=config.GITHUB_BASE, token=config.GITHUB_TOKEN)
                store.update_run(run_id, pr=opened)
            except Exception as e:  # the fix is still available for a manual "Open draft PR"
                log.exception("auto PR failed for run %s", run_id)
                await store.add_event(run_id, {"agent": "PR", "type": "message", "text": f"auto PR failed: {e}"})
        await store.finish(run_id, "done", rca=final)
    except Exception as e:  # surface failures in the UI instead of dying silently
        log.exception("run %s failed", run_id)
        await store.finish(run_id, "failed", error=f"{type(e).__name__}: {e}")
