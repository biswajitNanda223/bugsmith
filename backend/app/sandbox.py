"""Patch validation sandbox (Milestone 4).

Copies the repo into a throwaway directory and checks a candidate fix the way a
reviewer would:
  1. the reproduction test must FAIL on the unpatched code (proves it reproduces the bug),
  2. the patch must apply cleanly,
  3. the reproduction test must PASS after the patch,
  4. the existing test suite must still pass (no regressions).

Isolation level: separate temp copy, scrubbed environment (no API keys), timeouts and
output caps. It does NOT block network or syscalls. Anything untrusted should run in a
container; `docker_wrap` is the single place to add that (see docs/PLAN.md).
"""
import os
import shutil
import subprocess
import sys
import tempfile
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


def docker_wrap(cmd: list[str], workdir: Path) -> list[str]:
    """Hook for container isolation, e.g. ['docker','run','--rm','--network=none','-v',...]. Identity for now."""
    return cmd


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(docker_wrap(cmd, cwd), cwd=cwd, env=_env(cwd), capture_output=True, text=True, timeout=timeout)
        out = (p.stdout + p.stderr)[-_MAX_OUT:]
        return p.returncode, out
    except subprocess.TimeoutExpired:
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
        wd = (root / workdir).resolve()
        if root not in wd.parents and wd != root:
            res.steps.append(Step("workdir", False, "workdir escapes repo"))
            return res
        (wd / _REPRO).write_text(repro_test)
        py = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]

        code, out = _run([*py, _REPRO], wd, timeout)
        res.steps.append(Step("repro_fails_before_patch", code != 0 and code != 124,
                              out if code == 0 else out[-600:]))  # if it passes pre-patch, it doesn't reproduce the bug
        if not res.steps[-1].ok:
            return res

        (root / "_fix.patch").write_text(patch)
        code, out = _run(["git", "apply", "--whitespace=nowarn", "_fix.patch"], root, timeout)
        res.steps.append(Step("patch_applies", code == 0, out))
        if code != 0:
            return res

        code, out = _run([*py, _REPRO], wd, timeout)
        res.steps.append(Step("repro_passes_after_patch", code == 0, out[-600:]))
        if code != 0:
            return res

        if test_dir:
            code, out = _run([*py, test_dir], wd, timeout)
            res.steps.append(Step("existing_tests", code == 0, out[-600:]))
            if code != 0:
                return res
    res.passed = True
    return res
