import uuid
import pytest
from fastapi.testclient import TestClient

from app import main, runner, service
from app.agents.rca import build_rca_pipeline
from app.store import store
from tests.fake_llm import FakeLlm


@pytest.mark.asyncio
async def test_pipeline_produces_rca():
    inc = store.create_incident("DEMO-1", "TypeError", "demo-shop")
    run = store.create_run(inc["id"])
    await runner.run_incident(run["id"], "DEMO-1", agent=build_rca_pipeline(model=FakeLlm()))
    done = store.get_run(run["id"])
    assert done["status"] == "done", done["error"]
    assert done["rca"]["faulty_file"] == "demo-app/app.py"
    agents = {e["agent"] for e in done["events"] if e["type"] == "tool_call"}
    assert agents == {"CrashAgent", "CodeAgent", "LogsAgent", "TraceAgent", "ChangeAgent"}
    assert set(done["rca"]["confidence_breakdown"]["agents"]) == agents | {"RcaWriter"}
    assert store.get_incident(inc["id"])["status"] == "rca_ready"


def test_api_flow(monkeypatch):
    monkeypatch.setattr(service, "run_incident",
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


@pytest.mark.asyncio
async def test_confidence_and_memory_loop():
    from app import memory

    async def go():
        inc = store.create_incident(f"MEM-{uuid.uuid4().hex[:6]}", "TypeError: NoneType in cart_total")
        run = store.create_run(inc["id"])
        await runner.run_incident(run["id"], "DEMO-1", agent=build_rca_pipeline(model=FakeLlm()))
        return run["id"], store.get_run(run["id"])

    rid1, r1 = await go()
    b1 = r1["rca"]["confidence_breakdown"]
    assert b1["agents"]["TraceAgent"] == 0.6 and b1["agents"]["RcaWriter"] == 0.9
    assert b1["final"] == 0.74 and b1["similar_incidents"] == 0  # mean(.85,.8,.7,.6,.75) * full evidence * neutral reliability
    assert any(e["type"] == "confidence" for e in r1["events"])

    memory.record_feedback(rid1, accepted=True, agents=list(b1["agents"]))
    assert memory.reliability("CodeAgent") > 0.5

    _, r2 = await go()
    b2 = r2["rca"]["confidence_breakdown"]
    assert b2["similar_incidents"] == 1 and b2["memory_delta"] > 0 and b2["final"] > b1["final"]

    memory.record_feedback(r2["id"], accepted=False, agents=list(b1["agents"]))
    _, r3 = await go()
    assert r3["rca"]["confidence_breakdown"]["memory_delta"] < b2["memory_delta"]
