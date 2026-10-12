import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
