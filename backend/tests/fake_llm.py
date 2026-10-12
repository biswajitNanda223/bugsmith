"""Deterministic stand-in for an LLM so the pipeline is testable without an API key."""
import json

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

RCA_JSON = {
    "summary": "cart_total subtracts a None discount",
    "faulty_file": "demo-app/app.py", "faulty_lines": "19",
    "hypothesis": "Legacy items have discount=None; (1 - None) raises TypeError.",
    "evidence": ["Stack trace points at app.py:19", "CARTS['c2'] has discount None"],
    "confidence": 0.9, "missing_info": [],
    "suggested_fix": "Treat a missing discount as 0.",
}


CONFIDENCE = {"CrashAgent": 0.85, "CodeAgent": 0.8, "LogsAgent": 0.7, "TraceAgent": 0.6, "ChangeAgent": 0.75}


def _report(role, summary):
    return ("submit_report", {"summary": summary, "evidence": [f"{role} evidence 1", f"{role} evidence 2"], "confidence": CONFIDENCE[role]})


SCRIPTS = {
    "CrashAgent": [("recall_similar_incidents", {"title": "TypeError: NoneType in cart_total"}),
                   ("get_crash_report", {"issue_id": "DEMO-1"}), _report("CrashAgent", "TypeError at app.py:19")],
    "CodeAgent": [("get_crash_report", {"issue_id": "DEMO-1"}), ("search_code", {"query": "def cart_total"}),
                  ("read_file", {"path": "demo-app/app.py", "start_line": 14, "end_line": 22}), _report("CodeAgent", "1 - None at line 19")],
    "LogsAgent": [("get_crash_report", {"issue_id": "DEMO-1"}), ("query_logs", {"service": "demo-shop", "contains": "error"}),
                  _report("LogsAgent", "Only cart c2 fails")],
    "TraceAgent": [("get_crash_report", {"issue_id": "DEMO-1"}), ("get_traces", {"service": "demo-shop"}), _report("TraceAgent", "cart_total span errors")],
    "ChangeAgent": [("get_crash_report", {"issue_id": "DEMO-1"}), ("recent_changes", {"service": "demo-shop"}),
                    _report("ChangeAgent", "Discount commit a1b2c3d")],
}


class FakeLlm(BaseLlm):
    model: str = "fake"

    async def generate_content_async(self, llm_request, stream=False):
        contents = llm_request.contents
        last = contents[-1].parts if contents and contents[-1].parts else []
        if llm_request.config.response_schema is not None:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(RCA_JSON))]))
        else:
            role = next((r for r in SCRIPTS if f"ROLE:{r}" in str(llm_request.config.system_instruction)), None)
            done = sum(1 for c in contents for p in (c.parts or []) if p.function_response)
            script = SCRIPTS[role]
            if done < len(script):
                name, args = script[done]
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))
            else:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=f"{role} done.")]))
