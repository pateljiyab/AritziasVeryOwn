"""The LLM vibe tagger end to end, with a fake Claude client: the cache, failures, and no key."""

import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stylist.jobs import llm_tag
from stylist.jobs.ingest import CATALOG_CSV, ItemTag, ingest


class FakeClaude:
    def __init__(self, vibes=("cozy", "cozy", "minimal", "chic", "edgy"), fail_on=()):
        self.vibes, self.fail_on, self.calls = list(vibes), set(fail_on), []
        self.lock = threading.Lock()
        self.messages = self

    def parse(self, **kwargs):
        text = kwargs["messages"][0]["content"][-1]["text"]
        with self.lock:
            self.calls.append(text)
        if any(name in text for name in self.fail_on):
            raise RuntimeError("boom")
        return SimpleNamespace(stop_reason="end_turn", parsed_output=llm_tag.VibeTags(vibes=self.vibes))


@pytest.fixture
def db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'catalog.db'}"
    ingest(CATALOG_CSV, url)
    monkeypatch.setattr(llm_tag, "photo_jpeg", lambda item: None)  # no photo cache in tests
    return url


def llm_rows(url):
    with Session(create_engine(url)) as session:
        return list(session.scalars(select(ItemTag).where(ItemTag.source == "llm")))


def test_tags_items_and_caches_the_answers(db, tmp_path):
    cache = tmp_path / "vibes.json"
    client = FakeClaude()

    llm_tag.tag_vibes(db, client, limit=5, cache_path=cache)

    rows = llm_rows(db)
    assert len(rows) == 5 and len(client.calls) == 5
    assert all(r.vibes == ["cozy", "minimal", "chic"] for r in rows)  # deduplicated, at most 3
    assert "Name: " in client.calls[0] and "Slot: " in client.calls[0]

    again = FakeClaude(fail_on={""})  # would fail every call
    llm_tag.tag_vibes(db, again, limit=5, cache_path=cache)
    assert again.calls == [] and len(llm_rows(db)) == 5  # everything came from the cache


def test_a_failed_item_keeps_no_llm_row(db, tmp_path):
    client = FakeClaude()
    llm_tag.tag_vibes(db, client, limit=3, cache_path=tmp_path / "vibes.json")
    first_name = client.calls[0].split("Name: ")[1].split("\n")[0]

    failing = FakeClaude(fail_on={first_name})
    llm_tag.tag_vibes(db, failing, limit=3, cache_path=tmp_path / "other.json")

    assert len(llm_rows(db)) == 2


def test_without_a_key_only_cached_vibes_are_saved(db, tmp_path):
    cache = tmp_path / "vibes.json"
    llm_tag.tag_vibes(db, FakeClaude(), limit=2, cache_path=cache)

    llm_tag.tag_vibes(db, None, limit=4, cache_path=cache)

    assert len(llm_rows(db)) == 2


def test_a_changed_prompt_version_ignores_the_old_cache(db, tmp_path, monkeypatch):
    cache = tmp_path / "vibes.json"
    llm_tag.tag_vibes(db, FakeClaude(), limit=2, cache_path=cache)
    monkeypatch.setattr(llm_tag, "MODEL_VERSION", "other-model/v2")

    client = FakeClaude()
    llm_tag.tag_vibes(db, client, limit=2, cache_path=cache)

    assert len(client.calls) == 2
