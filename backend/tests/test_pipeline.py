import pytest
from fastapi.testclient import TestClient

from app import main, runner
from app.agents.rca import build_rca_pipeline
from app.store import store
from tests.fake_llm import FakeLlm


@pytest.mark.asyncio
async def test_pipeline_produces_rca():
    inc = store.create_incident("DEMO-1", "TypeError", "demo-shop")
    run = store.create_run(inc["id"])
    await runner.run_incident(run["id"], "DEMO-1", agent=build_rca_pipeline(model=FakeLlm()))
    done = store.runs[run["id"]]
    assert done["status"] == "done", done["error"]
    assert done["rca"]["faulty_file"] == "demo-app/app.py"
    assert any(e["type"] == "tool_call" and e["name"] == "get_crash_report" for e in done["events"])
    assert store.incidents[inc["id"]]["status"] == "rca_ready"


def test_api_flow(monkeypatch):
    monkeypatch.setattr(main, "run_incident",
                        lambda run_id, issue_id: runner.run_incident(run_id, issue_id, agent=build_rca_pipeline(model=FakeLlm())))
    with TestClient(main.app) as c:
        inc = c.post("/api/incidents", json={"issue_id": "DEMO-1", "title": "TypeError"}).json()
        assert c.post("/api/incidents", json={"issue_id": "DEMO-1", "title": "dup"}).json()["id"] == inc["id"]
        run = c.post(f"/api/incidents/{inc['id']}/runs").json()
        with c.stream("GET", f"/api/runs/{run['id']}/stream") as r:
            body = "".join(r.iter_text())
        assert "event: done" in body and "cart_total" in body
        assert c.get("/api/incidents").json()[0]["status"] == "rca_ready"


def test_local_tools_confined():
    from app.tools import local
    assert "error" in local.read_file("../../etc/passwd")
    assert local.search_code("def cart_total")["matches"]
