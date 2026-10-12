import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """SQLite temp file by default. Set BUGSMITH_TEST_DATABASE_URL=postgresql+psycopg://... to run the suite on Postgres
    (tables are dropped and recreated before every test, so use a throwaway database)."""
    from app import config, db

    url = os.getenv("BUGSMITH_TEST_DATABASE_URL")
    monkeypatch.setattr(config, "DATABASE_URL", url or f"sqlite:///{tmp_path / 'test.db'}")
    if url:
        eng = db.engine()
        db.meta.drop_all(eng)
        db.meta.create_all(eng)
