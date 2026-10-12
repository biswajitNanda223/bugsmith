from google.adk.agents import SequentialAgent

from app.agents.fix import build_fix_loop
from app.agents.rca import _model, build_investigation


def build_full_pipeline(model=None) -> SequentialAgent:
    """investigate (parallel) -> RCA -> gated fix loop."""
    model = model or _model()
    return SequentialAgent(name="DebugPipeline", sub_agents=[*build_investigation(model), build_fix_loop(model)])
