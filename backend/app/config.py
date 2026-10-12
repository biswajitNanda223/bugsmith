import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
MODEL = os.getenv("BUGSMITH_MODEL", "gemini-2.5-flash")
REPO_PATH = Path(os.getenv("BUGSMITH_REPO_PATH", BACKEND_DIR.parent)).resolve()
FIXTURES_DIR = BACKEND_DIR / "fixtures"
SENTRY_MCP_URL = os.getenv("SENTRY_MCP_URL", "")
GITHUB_MCP_URL = os.getenv("GITHUB_MCP_URL", "")
GITHUB_MCP_TOKEN = os.getenv("GITHUB_MCP_TOKEN", "")
