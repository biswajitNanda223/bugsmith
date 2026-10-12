import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
MODEL = os.getenv("BUGSMITH_MODEL", "gemini-2.5-flash")
REPO_PATH = Path(os.getenv("BUGSMITH_REPO_PATH", BACKEND_DIR.parent)).resolve()
FIXTURES_DIR = BACKEND_DIR / "fixtures"
SENTRY_MCP_URL = os.getenv("SENTRY_MCP_URL", "")
GITHUB_MCP_URL = os.getenv("GITHUB_MCP_URL", "")
GITHUB_MCP_TOKEN = os.getenv("GITHUB_MCP_TOKEN", "")

DB_PATH = os.getenv("BUGSMITH_DB", str(BACKEND_DIR / "bugsmith.db"))

AUTO_PR = os.getenv("BUGSMITH_AUTO_PR", "") == "1"  # off by default: a person clicks "Open draft PR"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("BUGSMITH_GITHUB_REPO", "")  # owner/name of the repo that contains the crashing code
GITHUB_BASE = os.getenv("BUGSMITH_GITHUB_BASE", "main")
