"""Response models: they type the API and show up in /docs and /redoc."""
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.rca import RCA

IncidentStatus = Literal["new", "investigating", "rca_ready", "pr_open", "failed"]
RunStatus = Literal["running", "done", "failed"]


class IncidentIn(BaseModel):
    issue_id: str = Field(description="Crash fingerprint / dedupe key, e.g. a Sentry issue id", examples=["DEMO-1"])
    title: str = Field(examples=["TypeError: NoneType in cart_total"])
    service: str = ""


class Incident(BaseModel):
    id: str
    issue_id: str
    title: str
    service: str = ""
    source: str = Field(description="manual | sentry", examples=["sentry"])
    status: IncidentStatus
    occurrences: int = Field(description="How many times this crash was reported")
    created_at: str
    last_seen_at: str
    run_ids: list[str]


class ConfidenceBreakdown(BaseModel):
    final: float = Field(ge=0, le=1, description="Calibrated score computed in code, not the model's own claim")
    agents: dict[str, float] = Field(description="Each agent's self-reported confidence")
    evidence_factor: float
    reliability: dict[str, float] = Field(description="Each agent's historical acceptance rate (from 👍/👎 feedback)")
    memory_delta: float = Field(description="Adjustment from similar past incidents that were accepted/rejected")
    similar_incidents: int
    action: Literal["auto_pr_allowed", "rca_only", "needs_human"]


class RcaOut(RCA):
    confidence_breakdown: ConfidenceBreakdown | None = None


class Fix(BaseModel):
    patch: str = Field(description="Unified diff (git format)")
    repro_test: str
    explanation: str
    workdir: str
    test_dir: str
    attempt: int


class ValidationStep(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class Validation(BaseModel):
    passed: bool
    steps: list[ValidationStep]
    attempt: int


class PrPreview(BaseModel):
    title: str
    body: str
    branch: str
    patch: str
    repro_test_path: str
    draft: bool = True


class PrResult(BaseModel):
    url: str
    number: int | None = None


class Run(BaseModel):
    id: str
    incident_id: str
    status: RunStatus
    started_at: str
    error: str | None = None
    feedback: Literal["accepted", "rejected"] | None = None
    fix_skipped: str | None = None
    rca: RcaOut | None = None
    fix: Fix | None = None
    validation: Validation | None = None
    pr_preview: PrPreview | None = None
    pr: PrResult | None = None
    events: list[dict] = Field(description="Timeline: tool calls, messages, validations, confidence")


class FeedbackIn(BaseModel):
    accepted: bool = Field(description="True if an engineer judged the root cause correct")


class Ok(BaseModel):
    ok: bool = True


class WebhookResult(BaseModel):
    status: Literal["created", "occurrence_recorded", "investigating", "investigation_in_progress",
                    "recently_investigated", "rate_limited", "duplicate_delivery", "ignored"]
    incident_id: str | None = None
    run_id: str | None = None


class Detail(BaseModel):
    detail: str
