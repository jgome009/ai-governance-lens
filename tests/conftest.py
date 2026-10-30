import sqlite3
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import generate_synthetic_data  # noqa: E402
import loader  # noqa: E402


@pytest.fixture(scope="session")
def db_path():
    """Regenerate the database once per test session, so tests always run
    against what the current generator code actually produces - never a
    stale file left over from a previous code version."""
    generate_synthetic_data.main()
    return generate_synthetic_data.DB_PATH


@pytest.fixture(scope="session")
def conn(db_path):
    c = sqlite3.connect(db_path)
    yield c
    c.close()


@pytest.fixture
def fixture_db():
    """An isolated, hand-built in-memory database with known exact rows -
    used to test KPI SQL logic (especially anything touching `flags`,
    which is empty in the real generated DB until phase 3 ships a
    detector) independent of the synthetic generator's random output."""
    c = sqlite3.connect(":memory:")
    loader.ensure_schema(c)
    loader.load_tools(c, {
        "chatgpt": ("OpenAI", 0, "Plus (consumer)"),
        "claude_web": ("Anthropic", 1, "Team"),
    })
    yield c
    c.close()
