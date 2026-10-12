import subprocess
import uuid

import pytest

from app import pr, runner
from app.agents.pipeline import build_full_pipeline
from app.store import store
from tests import fake_llm
from tests.fake_llm import FakeLlm
from tests.patches import GOOD_PATCH, REPRO


async def run_pipeline():
    inc = store.create_incident(f"FIX-{uuid.uuid4().hex[:6]}", "TypeError: NoneType in cart_total", "demo-shop")
    run = store.create_run(inc["id"])
    await runner.run_incident(run["id"], "DEMO-1", agent=build_full_pipeline(model=FakeLlm()))
    return inc, store.runs[run["id"]]


@pytest.fixture
def confident(monkeypatch):
    monkeypatch.setattr(fake_llm, "CONFIDENCE", {k: 0.95 for k in fake_llm.CONFIDENCE})


@pytest.mark.asyncio
async def test_fix_loop_retries_then_validates(confident):
    inc, run = await run_pipeline()
    assert run["status"] == "done", run["error"]
    vals = [e for e in run["events"] if e["type"] == "validation"]
    assert [v["passed"] for v in vals] == [False, True]  # attempt 1 no-op rejected, attempt 2 accepted
    assert run["fix"]["attempt"] == 2 and run["validation"]["passed"]
    assert run["pr_preview"]["draft"] and run["pr_preview"]["branch"].startswith("bugsmith/")
    assert "repro_passes_after_patch" in run["pr_preview"]["body"] and "Draft" in run["pr_preview"]["body"]


@pytest.mark.asyncio
async def test_loop_gives_up_after_max_attempts(confident, monkeypatch):
    monkeypatch.setattr(fake_llm, "FIX_FAILS_FIRST", 99)
    _, run = await run_pipeline()
    assert len([e for e in run["events"] if e["type"] == "validation"]) == 3
    assert not run["validation"]["passed"] and "pr_preview" not in run


@pytest.mark.asyncio
async def test_low_confidence_skips_fix(monkeypatch):
    # default fake confidences give 0.74 < 0.75 threshold -> RCA only
    _, run = await run_pipeline()
    assert run["status"] == "done" and run["fix"] is None and "threshold" in run["fix_skipped"]
    assert not any(e["type"] == "validation" for e in run["events"])


def test_open_draft_pr_pushes_branch_not_main(tmp_path):
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[2]
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
    (seed / "demo-app").mkdir()
    (seed / "demo-app/app.py").write_text((repo_root / "demo-app/app.py").read_text())
    for c in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], ["push", "-q", "origin", "HEAD:main"]):
        subprocess.run(["git", *c], cwd=seed, check=True, capture_output=True)

    fix = {"patch": GOOD_PATCH, "repro_test": REPRO, "workdir": "demo-app", "explanation": "x"}
    pv = {"title": "fix: t", "body": "b", "branch": pr.branch_name("DEMO 1"), "patch": GOOD_PATCH,
          "repro_test_path": pr.repro_path(fix, "DEMO-1"), "draft": True}
    sent = {}
    out = pr.open_draft_pr(pv, fix, repo="o/r", remote_url=str(origin), api=lambda payload: sent.update(payload) or {"url": "u"})
    assert sent["draft"] is True and sent["head"] == "bugsmith/demo-1" and sent["base"] == "main"
    branches = subprocess.run(["git", "branch", "--list"], cwd=origin, capture_output=True, text=True).stdout
    assert "bugsmith/demo-1" in branches
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", "bugsmith/demo-1"], cwd=origin, capture_output=True, text=True).stdout
    assert "demo-app/tests/test_bugsmith_demo_1.py" in files
    main_app = subprocess.run(["git", "show", "main:demo-app/app.py"], cwd=origin, capture_output=True, text=True).stdout
    assert "or 0" not in main_app  # main untouched
    with pytest.raises(ValueError):
        pr.open_draft_pr({**pv, "branch": "main"}, fix, repo="o/r", remote_url=str(origin), api=lambda p: {})
