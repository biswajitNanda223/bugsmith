"""Milestone 2: crash -> root-cause analysis.

Investigator (has tools, free-form findings) -> RcaWriter (structured RCA).
Split in two because ADK models with an output_schema cannot call tools.
"""
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from pydantic import BaseModel, Field

from app import config
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


def _model():
    if "/" in config.MODEL:  # e.g. anthropic/claude-sonnet-5-5 -> LiteLLM
        from google.adk.models.lite_llm import LiteLlm

        return LiteLlm(model=config.MODEL)
    return config.MODEL


def _tools() -> list:
    tools: list = []
    if config.SENTRY_MCP_URL:
        tools.append(McpToolset(connection_params=StreamableHTTPConnectionParams(url=config.SENTRY_MCP_URL)))
    else:
        tools.append(local.get_crash_report)
    if config.GITHUB_MCP_URL:
        headers = {"Authorization": f"Bearer {config.GITHUB_MCP_TOKEN}"} if config.GITHUB_MCP_TOKEN else {}
        tools.append(McpToolset(connection_params=StreamableHTTPConnectionParams(url=config.GITHUB_MCP_URL, headers=headers)))
    else:
        tools += [local.search_code, local.read_file]
    return tools


def build_rca_pipeline(model=None) -> SequentialAgent:
    model = model or _model()
    investigator = LlmAgent(
        name="Investigator",
        model=model,
        instruction=(
            "You are a production-debugging agent. Crash issue id: {issue_id}.\n"
            "1. Fetch the crash report. 2. Locate the faulty code with search/read tools "
            "and read the surrounding lines. 3. Report only facts you verified in tool output: "
            "stack trace, the exact faulty lines, what input triggers it, and why."
        ),
        tools=_tools(),
        output_key="findings",
    )
    writer = LlmAgent(
        name="RcaWriter",
        model=model,
        instruction=(
            "Produce the root-cause analysis from these findings:\n{findings}\n"
            "Be honest about confidence; list missing information if the evidence is thin."
        ),
        output_schema=RCA,
        output_key="rca",
    )
    return SequentialAgent(name="RcaPipeline", sub_agents=[investigator, writer])
