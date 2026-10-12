import json
import sys
from pathlib import Path

import pytest

from app import sandbox

from tests.patches import GOOD_PATCH, NOOP_PATCH, REPRO


@pytest.fixture
def repo():
    return Path(__file__).resolve().parents[2]


def test_good_patch_validates(repo):
    r = sandbox.validate_patch(GOOD_PATCH, REPRO, "demo-app", repo=repo)
    assert r.passed, r.to_dict()
    assert [s.name for s in r.steps] == ["repro_fails_before_patch", "patch_applies", "repro_passes_after_patch"]
    assert "None" in (repo / "demo-app/app.py").read_text() and "or 0" not in (repo / "demo-app/app.py").read_text()  # original untouched


def test_patch_that_does_not_fix_is_rejected(repo):
    r = sandbox.validate_patch(NOOP_PATCH, REPRO, "demo-app", repo=repo)
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


def test_docker_command_is_locked_down(tmp_path):
    root = tmp_path / "repo"
    wd = root / "demo-app"
    cmd = sandbox.docker_command([sys.executable, "-m", "pytest", "-q"], root, wd, "bugsmith-x", "img")
    joined = " ".join(cmd)
    assert cmd[:3] == ["docker", "run", "--rm"] and cmd[-5:] == ["img", "python", "-m", "pytest", "-q"]
    assert "--network none" in joined and "--read-only" in joined and "--cap-drop ALL" in joined
    assert "no-new-privileges" in joined and "--user 10001:10001" in joined and "--pids-limit" in joined
    assert f"-v {root}:/work:ro" in joined and "-w /work/demo-app" in joined
    assert str(sys.executable) not in cmd  # host interpreter path never leaks into the container


def test_docker_mode_runs_tests_in_container_not_host(monkeypatch, tmp_path):
    from app import config
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("env")))
        import subprocess
        return subprocess.CompletedProcess(argv, 1, stdout="1 failed", stderr="")  # repro "fails before patch"

    monkeypatch.setattr(config, "SANDBOX_MODE", "docker")
    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    sandbox_repo = tmp_path / "r"
    (sandbox_repo / "demo-app").mkdir(parents=True)
    r = sandbox.validate_patch("--- a/x\n+++ b/x\n", "def test_x(): pass", "demo-app", repo=sandbox_repo)
    pytest_calls = [c for c in calls if c[0][:2] == ["docker", "run"]]
    assert pytest_calls and pytest_calls[0][1] is None  # no host env passed through
    assert r.steps[0].name == "repro_fails_before_patch"


def _docker_available():
    import shutil, subprocess
    return shutil.which("docker") and subprocess.run(["docker", "image", "inspect", "bugsmith-sandbox"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="needs a Docker daemon and the bugsmith-sandbox image (mise run sandbox:build)")
def test_docker_end_to_end(monkeypatch, repo):
    from app import config
    monkeypatch.setattr(config, "SANDBOX_MODE", "docker")
    r = sandbox.validate_patch(GOOD_PATCH, REPRO, "demo-app", repo=repo)
    assert r.passed, r.to_dict()
    net = sandbox.validate_patch(GOOD_PATCH, "import socket\ndef test_net():\n    socket.create_connection(('1.1.1.1', 53), 2)\n", "demo-app", repo=repo)
    assert net.passed is False  # repro must fail before the patch; with no network it fails by design, never reaching the internet
