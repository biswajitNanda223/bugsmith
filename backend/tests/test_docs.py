from fastapi.testclient import TestClient

from app.main import app


def test_docs_and_openapi_are_served_and_documented():
    c = TestClient(app)
    assert c.get("/docs").status_code == 200      # Swagger UI
    assert c.get("/redoc").status_code == 200     # ReDoc
    spec = c.get("/openapi.json").json()
    assert spec["info"]["title"] == "BugSmith API" and {t["name"] for t in spec["tags"]} >= {"incidents", "runs", "webhooks"}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op.get("tags") and op.get("summary"), f"{method} {path} lacks tags/summary"
    hook = spec["paths"]["/webhooks/sentry"]["post"]
    assert {p["name"] for p in hook["parameters"]} >= {"Sentry-Hook-Signature", "Request-ID"}
    assert {"401", "503"} <= set(hook["responses"])
    assert "text/event-stream" in spec["paths"]["/api/runs/{run_id}/stream"]["get"]["responses"]["200"]["content"]
    assert "ConfidenceBreakdown" in spec["components"]["schemas"]
