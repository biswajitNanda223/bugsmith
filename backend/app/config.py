import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
MODEL = os.getenv("BUGSMITH_MODEL", "gemini-2.5-flash")
REPO_PATH = Path(os.getenv("BUGSMITH_REPO_PATH", BACKEND_DIR.parent)).resolve()
FIXTURES_DIR = BACKEND_DIR / "fixtures"
SENTRY_MCP_URL = os.getenv("SENTRY_MCP_URL", "")
GITHUB_MCP_URL = os.getenv("GITHUB_MCP_URL", "")
GITHUB_MCP_TOKEN = os.getenv("GITHUB_MCP_TOKEN", "")

# sqlite for dev/tests, postgresql+psycopg://user:pass@host/db in production
DATABASE_URL = os.getenv("BUGSMITH_DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'bugsmith.db'}")
SENTRY_WEBHOOK_SECRET = os.getenv("SENTRY_WEBHOOK_SECRET", "")
AUTO_RUN = os.getenv("BUGSMITH_AUTO_RUN", "") == "1"  # start an investigation when a new crash arrives
RUN_COOLDOWN_MIN = int(os.getenv("BUGSMITH_RUN_COOLDOWN_MIN", "60"))  # don't re-investigate the same crash within this window
MAX_RUNS_PER_HOUR = int(os.getenv("BUGSMITH_MAX_RUNS_PER_HOUR", "10"))  # cost guard
SANDBOX_MODE = os.getenv("BUGSMITH_SANDBOX", "local")  # "docker" = run tests in a network-less container
SANDBOX_IMAGE = os.getenv("BUGSMITH_SANDBOX_IMAGE", "bugsmith-sandbox")

AUTO_PR = os.getenv("BUGSMITH_AUTO_PR", "") == "1"  # off by default: a person clicks "Open draft PR"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("BUGSMITH_GITHUB_REPO", "")  # owner/name of the repo that contains the crashing code
GITHUB_BASE = os.getenv("BUGSMITH_GITHUB_BASE", "main")
