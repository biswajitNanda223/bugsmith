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


class FakeLlm(BaseLlm):
    model: str = "fake"

    async def generate_content_async(self, llm_request, stream=False):
        contents = llm_request.contents
        last = contents[-1].parts if contents and contents[-1].parts else []
        if llm_request.config.response_schema is not None:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(RCA_JSON))]))
        elif any(p.function_response for p in last):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Crash at app.py:19 (discount None).")]))
        else:
            fc = types.FunctionCall(name="get_crash_report", args={"issue_id": "DEMO-1"})
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))
