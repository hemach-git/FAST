import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True, scope="session")
def isolated_db(tmp_path_factory):
    """Each test session gets its own seeded DB + log dir (never touches data/fast.db)."""
    import os
    d = tmp_path_factory.mktemp("fast")
    os.environ["FAST_DB_PATH"] = str(d / "test.db")
    os.environ["FAST_LOG_DIR"] = str(d / "logs")
    os.environ["LLM_PROVIDER"] = "mock"
    from app.config import get_config
    get_config.cache_clear()
    from app.services import retriever, workflow
    workflow._ready = False
    retriever.invalidate()
    workflow.ensure_ready()
    yield d
