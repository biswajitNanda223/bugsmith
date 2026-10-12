"""Patch validation sandbox (Milestone 4).

Copies the repo into a throwaway directory and checks a candidate fix the way a
reviewer would:
  1. the reproduction test must FAIL on the unpatched code (proves it reproduces the bug),
  2. the patch must apply cleanly,
  3. the reproduction test must PASS after the patch,
  4. the existing test suite must still pass (no regressions).

Isolation:
  * BUGSMITH_SANDBOX=local (default): separate temp copy, scrubbed environment (no API keys), timeouts, output caps.
    Does NOT block network or filesystem access outside the copy - fine for your own repos, not for untrusted code.
  * BUGSMITH_SANDBOX=docker: every test run executes in a throwaway container: no network, read-only root and
    source mount, all capabilities dropped, non-root, memory/CPU/pid limits. `git apply` still runs on the host
    (it only edits files in the temp copy and paths are validated first).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app import config

_IGNORE = shutil.ignore_patterns(".git", ".venv", "node_modules", "__pycache__", "dist", "*.db", ".pytest_cache")
_REPRO = "_bugsmith_repro_test.py"
_MAX_OUT = 4000


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ValidationResult:
    passed: bool = False
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _env(workdir: Path) -> dict:
    keep = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "TMPDIR", "VIRTUAL_ENV"}}
    return {**keep, "PYTHONPATH": str(workdir), "PYTHONDONTWRITEBYTECODE": "1", "CI": "1"}


def docker_command(cmd: list[str], root: Path, workdir: Path, name: str, image: str) -> list[str]:
    """`docker run` argv that runs `cmd` (python path rewritten) against the read-only copy at `root`."""
    inner = ["python" if c == sys.executable else c for c in cmd]
    wd = "/work/" + str(workdir.relative_to(root)) if workdir != root else "/work"
    return ["docker", "run", "--rm", "--name", name,
            "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m,noexec",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "256", "--memory", "512m", "--cpus", "1", "--user", "10001:10001",
            "-v", f"{root}:/work:ro", "-w", wd,
            "-e", f"PYTHONPATH={wd}", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "CI=1",
            image, *inner]


def _run(cmd: list[str], cwd: Path, timeout: int, root: Path | None = None) -> tuple[int, str]:
    """Run a command. With `root` (the repo copy) and BUGSMITH_SANDBOX=docker it runs inside a container."""
    if root is not None and config.SANDBOX_MODE == "docker":
        name = f"bugsmith-{uuid.uuid4().hex[:12]}"
        argv, env = docker_command(cmd, root, cwd, name, config.SANDBOX_IMAGE), None
    else:
        name, argv, env = None, cmd, _env(cwd)
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-_MAX_OUT:]
    except subprocess.TimeoutExpired:
        if name:  # killing the docker client does not stop the container
            subprocess.run(["docker", "kill", name], capture_output=True)
        return 124, f"timed out after {timeout}s"


def _patch_paths_ok(patch: str) -> bool:
    for line in patch.splitlines():
        if line.startswith(("--- ", "+++ ")):
            path = line[4:].split("\t")[0].strip()
            if path == "/dev/null":
                continue
            path = path.removeprefix("a/").removeprefix("b/")
            if path.startswith("/") or ".." in Path(path).parts:
                return False
    return True


def validate_patch(patch: str, repro_test: str, workdir: str = ".", test_dir: str = "", repo: Path | None = None,
                   timeout: int = 60) -> ValidationResult:
    """patch: unified diff (git style, -p1). repro_test: pytest source. workdir: subdir that is the import root
    for tests (e.g. 'demo-app'). test_dir: existing suite to run for regressions (relative to workdir; '' = skip)."""
    repo = (repo or config.REPO_PATH).resolve()
    res = ValidationResult()
    if not _patch_paths_ok(patch):
        res.steps.append(Step("patch_paths", False, "patch touches paths outside the repo"))
        return res
    with tempfile.TemporaryDirectory(prefix="bugsmith-") as tmp:
        root = Path(tmp) / "repo"
        shutil.copytree(repo, root, ignore=_IGNORE)
        if config.SANDBOX_MODE == "docker":
            subprocess.run(["chmod", "-R", "a+rX", str(Path(tmp))], check=True)  # the container user is not the host user
        wd = (root / workdir).resolve()
        if root not in wd.parents and wd != root:
            res.steps.append(Step("workdir", False, "workdir escapes repo"))
            return res
        (wd / _REPRO).write_text(repro_test)
        py = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]

        code, out = _run([*py, _REPRO], wd, timeout, root)
        res.steps.append(Step("repro_fails_before_patch", code != 0 and code != 124,
                              out if code == 0 else out[-600:]))  # if it passes pre-patch, it doesn't reproduce the bug
        if not res.steps[-1].ok:
            return res

        (root / "_fix.patch").write_text(patch)
        code, out = _run(["git", "apply", "--whitespace=nowarn", "_fix.patch"], root, timeout)
        res.steps.append(Step("patch_applies", code == 0, out))
        if code != 0:
            return res

        code, out = _run([*py, _REPRO], wd, timeout, root)
        res.steps.append(Step("repro_passes_after_patch", code == 0, out[-600:]))
        if code != 0:
            return res

        if test_dir:
            code, out = _run([*py, test_dir], wd, timeout, root)
            res.steps.append(Step("existing_tests", code == 0, out[-600:]))
            if code != 0:
                return res
    res.passed = True
    return res
