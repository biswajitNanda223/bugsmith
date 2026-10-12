import json
import sys
from pathlib import Path

import pytest

from app import sandbox

REPRO = '''
from app import cart_total

def test_none_discount():
    assert cart_total([{"sku": "B2", "price": 15.0, "qty": 2, "discount": None}]) == 30.0
'''

GOOD_PATCH = '''--- a/demo-app/app.py
+++ b/demo-app/app.py
@@ -16,7 +16,7 @@
 def cart_total(items: list[dict]) -> float:
     total = 0.0
     for item in items:
-        total += item["price"] * item["qty"] * (1 - item["discount"])
+        total += item["price"] * item["qty"] * (1 - (item["discount"] or 0))
     return round(total, 2)
 
 
'''

@pytest.fixture
def repo():
    return Path(__file__).resolve().parents[2]


def test_good_patch_validates(repo):
    r = sandbox.validate_patch(GOOD_PATCH, REPRO, "demo-app", repo=repo)
    assert r.passed, r.to_dict()
    assert [s.name for s in r.steps] == ["repro_fails_before_patch", "patch_applies", "repro_passes_after_patch"]
    assert "None" in (repo / "demo-app/app.py").read_text() and "or 0" not in (repo / "demo-app/app.py").read_text()  # original untouched


def test_patch_that_does_not_fix_is_rejected(repo):
    noop = GOOD_PATCH.replace('(1 - (item["discount"] or 0))', '(1 - (item["discount"] or None))')
    r = sandbox.validate_patch(noop, REPRO, "demo-app", repo=repo)
    assert not r.passed and r.steps[-1].name == "repro_passes_after_patch"


def test_repro_that_already_passes_is_rejected(repo):
    r = sandbox.validate_patch(GOOD_PATCH, "def test_ok():\n    assert True\n", "demo-app", repo=repo)
    assert not r.passed and r.steps[0].name == "repro_fails_before_patch"


def test_path_traversal_rejected(repo):
    evil = "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n"
    r = sandbox.validate_patch(evil, REPRO, "demo-app", repo=repo)
    assert not r.passed and r.steps[0].name == "patch_paths"


def test_secrets_not_leaked_to_sandbox(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "secret")
    assert "GOOGLE_API_KEY" not in sandbox._env(Path("."))


@pytest.mark.asyncio
async def test_mcp_roundtrip(repo, monkeypatch):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_servers.sandbox_server"],
                                   cwd=str(Path(__file__).resolve().parent.parent),
                                   env={"BUGSMITH_REPO_PATH": str(repo), "PATH": "/usr/bin:/bin"})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        assert "validate_patch" in [t.name for t in (await session.list_tools()).tools]
        out = await session.call_tool("validate_patch", {"patch": GOOD_PATCH, "repro_test": REPRO, "workdir": "demo-app"})
        assert json.loads(out.content[0].text)["passed"] is True
