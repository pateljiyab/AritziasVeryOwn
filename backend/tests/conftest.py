import sys
from pathlib import Path

import pytest

# No package install: make `import stylist...` work straight from backend/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    """Tests never call a real LLM, even if the shell exports a key: the parser falls back to keyword rules."""
    for name in ("ANTHROPIC_API_KEY", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "CLAUDE_MODEL"):
        monkeypatch.delenv(name, raising=False)
