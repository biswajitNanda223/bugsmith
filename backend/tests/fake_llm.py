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


FINDINGS_CONFIDENCE = 0.8


class FakeLlm(BaseLlm):
    model: str = "fake"

    async def generate_content_async(self, llm_request, stream=False):
        contents = llm_request.contents
        last = contents[-1].parts if contents and contents[-1].parts else []
        if llm_request.config.response_schema is not None:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(RCA_JSON))]))
        else:
            done = sum(1 for c in contents for p in (c.parts or []) if p.function_response)
            script = [
                ("recall_similar_incidents", {"title": "TypeError: NoneType in cart_total"}),
                ("get_crash_report", {"issue_id": "DEMO-1"}),
                ("submit_findings", {"summary": "cart_total subtracts None", "evidence": ["app.py:19", "c2 discount None"],
                                     "confidence": FINDINGS_CONFIDENCE}),
            ]
            if done < len(script):
                name, args = script[done]
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))
            else:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Crash at app.py:19 (discount None).")]))
