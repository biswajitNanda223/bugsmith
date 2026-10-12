"""ADK entry point: `adk web`, `adk run` and `adk api_server` load `root_agent` from here.

Scaffolded with `adk create bugsmith_agent`, then pointed at the BugSmith pipeline:
Investigators (parallel) -> RcaWriter -> FixLoop (gated). Session state needs `issue_id`; the dev UI
starts with none, so pass it in the first message state (see README) or use the FastAPI app.
"""
from app.agents.pipeline import build_full_pipeline

root_agent = build_full_pipeline()
