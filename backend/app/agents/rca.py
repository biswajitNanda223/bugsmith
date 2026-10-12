"""Milestone 2: crash -> root-cause analysis.

Investigator (has tools, free-form findings) -> RcaWriter (structured RCA).
Split in two because ADK models with an output_schema cannot call tools.
"""
from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from pydantic import BaseModel, Field

from google.adk.tools.tool_context import ToolContext

from app import config, memory
from app.tools import local


class RCA(BaseModel):
    summary: str = Field(description="One-sentence root cause")
    faulty_file: str
    faulty_lines: str = Field(description="e.g. '19-20'")
    hypothesis: str
    evidence: list[str]
    confidence: float = Field(ge=0, le=1)
    missing_info: list[str] = Field(default_factory=list, description="What would raise confidence")
    suggested_fix: str


def submit_report(summary: str, evidence: list[str], confidence: float, tool_context: ToolContext) -> dict:
    """Record your verified findings. Call this exactly once, last.

    confidence is 0-1: how sure you are that your findings explain the crash. Use <0.4 if you
    found no relevant data, 0.4-0.7 if the evidence is circumstantial, >0.8 only if directly verified.
    """
    tool_context.state[f"report_{tool_context.agent_name}"] = {
        "summary": summary, "evidence": evidence, "confidence": max(0.0, min(1.0, float(confidence)))}
    return {"recorded": True}


def recall_similar_incidents(title: str, tool_context: ToolContext) -> dict:
    """Look up past incidents with a similar title: their root cause, fix, and whether engineers accepted it."""
    found = memory.similar(title)
    tool_context.state["similar_past"] = found  # short-term state, used later for confidence
    return {"similar": found}


def _model():
    if "/" in config.MODEL:  # e.g. anthropic/claude-sonnet-5-5 -> LiteLLM
        from google.adk.models.lite_llm import LiteLlm

        return LiteLlm(model=config.MODEL)
    return config.MODEL


def _mcp(url: str, token: str = ""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return McpToolset(connection_params=StreamableHTTPConnectionParams(url=url, headers=headers))


INVESTIGATORS = {
    # name: (what it does, tools)
    "CrashAgent": ("Fetch the crash report for issue {issue_id}; call recall_similar_incidents with its title "
                   "(past results are hints to verify, never proof). Report stack trace, impact and trigger.",
                   lambda: [_mcp(config.SENTRY_MCP_URL)] if config.SENTRY_MCP_URL else [local.get_crash_report],
                   [recall_similar_incidents]),
    "CodeAgent": ("Find the faulty code for crash {issue_id}: search the repo for the function named in the stack "
                  "trace (get it from the crash report if needed) and read the surrounding lines. Report the exact faulty lines and why.",
                  lambda: [_mcp(config.GITHUB_MCP_URL, config.GITHUB_MCP_TOKEN)] if config.GITHUB_MCP_URL else [local.search_code, local.read_file],
                  [local.get_crash_report]),
    "LogsAgent": ("Query logs for the service of crash {issue_id} (service name is in the crash report). Report error "
                  "frequency, affected inputs (ids, routes) and anything abnormal before the crash.",
                  lambda: [local.query_logs], [local.get_crash_report]),
    "TraceAgent": ("Fetch traces for the service of crash {issue_id}. Report which span failed and whether failing and "
                   "succeeding requests differ.", lambda: [local.get_traces], [local.get_crash_report]),
    "ChangeAgent": ("List recent deploys, commits and feature-flag changes for the service of crash {issue_id}. Report "
                    "which change most plausibly introduced the bug, with timing relative to first_seen.",
                    lambda: [local.recent_changes], [local.get_crash_report]),
}


def build_investigation(model=None) -> list:
    """[Investigators (parallel), RcaWriter] - fresh agent instances (an ADK agent can have only one parent)."""
    model = model or _model()
    agents = []
    for name, (job, tools, extra) in INVESTIGATORS.items():
        agents.append(LlmAgent(
            name=name, model=model,
            instruction=(f"ROLE:{name}. You are one of several parallel production-debugging investigators.\n{job}\n"
                         "Rely only on tool output. If a tool returns nothing useful, say so and give a LOW confidence. "
                         "Finish by calling submit_report once."),
            tools=[*tools(), *[t for t in extra if t not in tools()], submit_report],
            output_key=f"notes_{name}",
        ))
    investigators = ParallelAgent(name="Investigators", sub_agents=agents)
    reports = "\n".join(f"{n}: {{report_{n}?}}" for n in INVESTIGATORS)
    writer = LlmAgent(
        name="RcaWriter", model=model,
        instruction=("Produce the root-cause analysis from the investigators' reports. Reports may disagree or be missing; "
                     "weigh them by their stated confidence and evidence, and mention conflicts.\n" + reports +
                     "\nBe honest about your own confidence; list missing information if the evidence is thin."),
        output_schema=RCA, output_key="rca",
    )
    return [investigators, writer]


def build_rca_pipeline(model=None) -> SequentialAgent:
    return SequentialAgent(name="RcaPipeline", sub_agents=build_investigation(model))
