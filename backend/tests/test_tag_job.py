"""The tagging job end to end, with CLIP and image downloads replaced by fakes."""

import json

import numpy as np
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stylist.jobs import tag
from stylist.jobs.ingest import CATALOG_CSV, ingest
from test_clip_tagger import fake_encode_texts


class FakeClipModel:
    def encode_texts(self, texts):
        return fake_encode_texts(texts)

    def encode_images(self, images):
        return fake_encode_texts([f"image {i}" for i in images])


def run_job(tmp_path, monkeypatch, limit=None, no_photo=()):
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    ingest(CATALOG_CSV, db)
    monkeypatch.setattr(tag, "ClipModel", FakeClipModel)
    monkeypatch.setattr(tag, "download_image", lambda item: None if item.item_id in no_photo else tmp_path)
    monkeypatch.setattr(tag, "load_image", lambda path: object())
    monkeypatch.setattr(tag, "EMBEDDINGS_NPY", tmp_path / "embeddings.npy")
    monkeypatch.setattr(tag, "EMBEDDING_IDS_JSON", tmp_path / "embedding_ids.json")
    tag.tag_catalog(db, limit)
    with Session(create_engine(db)) as session:
        return list(session.scalars(select(tag.ItemTag)))


def test_every_item_gets_valid_tags_and_embeddings_are_saved(tmp_path, monkeypatch):
    rows = run_job(tmp_path, monkeypatch, limit=None)

    assert len(rows) > 100
    for row in rows:
        assert 1 <= len(row.seasons) <= 4
        assert 1 <= len(row.vibes) <= 3
        assert row.formality in {1, 2, 3, 4, 5}
        assert len(row.colors) <= 1
    ids = json.loads((tmp_path / "embedding_ids.json").read_text())["item_ids"]
    assert np.load(tmp_path / "embeddings.npy").shape[0] == len(ids) == len(rows)


def test_item_without_a_photo_still_gets_a_color(tmp_path, monkeypatch):
    first_id = run_job(tmp_path, monkeypatch, limit=5)[0].item_id

    rows = run_job(tmp_path, monkeypatch, limit=5, no_photo={first_id})

    row = next(r for r in rows if r.item_id == first_id)
    assert row.seasons == [] and row.vibes == [] and row.formality is None
    assert row.colors and row.color_source in {"alt_text", "hex"}


def test_trial_run_does_not_touch_other_items_or_the_embedding_file(tmp_path, monkeypatch):
    full = run_job(tmp_path, monkeypatch, limit=None)
    (tmp_path / "embeddings.npy").unlink()

    after_trial = run_job(tmp_path, monkeypatch, limit=3)

    assert len(after_trial) == len(full)
    assert not (tmp_path / "embeddings.npy").exists()


def test_unreadable_photo_is_skipped_and_dropped_from_the_cache(tmp_path, monkeypatch):
    """Review #1: one corrupt image must not crash the run, and must be re-downloaded next time."""
    bad_id = run_job(tmp_path, monkeypatch, limit=5)[0].item_id
    photo_dir = tmp_path / "photos"
    photo_dir.mkdir()

    def fake_download(item):
        path = photo_dir / f"{item.item_id}.img"
        path.write_bytes(b"not an image")
        return path

    def fake_load(path):
        if path.stem == bad_id:
            raise OSError("cannot identify image file")
        return object()

    monkeypatch.setattr(tag, "download_image", fake_download)
    monkeypatch.setattr(tag, "load_image", fake_load)
    tag.tag_catalog(f"sqlite:///{tmp_path / 'catalog.db'}", limit=5)

    with Session(create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")) as session:
        row = session.get(tag.ItemTag, (bad_id, tag.SOURCE))
    assert row.vibes == [] and row.colors  # color only
    assert not (photo_dir / f"{bad_id}.img").exists()


def test_incomplete_download_counts_as_a_failed_download(monkeypatch, tmp_path):
    """Review #1: http.client.IncompleteRead is not an OSError."""
    import http.client

    def broken_urlopen(*args, **kwargs):
        raise http.client.IncompleteRead(b"partial")

    monkeypatch.setattr(tag.urllib.request, "urlopen", broken_urlopen)
    monkeypatch.setattr(tag, "IMAGE_DIR", tmp_path)
    item = type("FakeItem", (), {"item_id": "abc", "name": "Test", "image_url": "https://example.com/a.jpg"})()

    assert tag.download_image(item) is None


def test_no_loadable_photos_changes_nothing(tmp_path, monkeypatch):
    """Review #4: abort before writing tags, and leave the old embedding file alone."""
    before = run_job(tmp_path, monkeypatch, limit=None)
    saved = (tmp_path / "embeddings.npy").read_bytes()
    monkeypatch.setattr(tag, "download_image", lambda item: None)

    with pytest.raises(SystemExit):
        tag.tag_catalog(f"sqlite:///{tmp_path / 'catalog.db'}")

    with Session(create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")) as session:
        after = list(session.scalars(select(tag.ItemTag)))
    assert [r.vibes for r in after] == [r.vibes for r in before]
    assert (tmp_path / "embeddings.npy").read_bytes() == saved


@pytest.mark.parametrize("limit", [0, -3])
def test_limit_below_one_is_rejected(tmp_path, monkeypatch, limit):
    """Review #5: --limit 0 used to mean a full run that overwrote the embeddings."""
    with pytest.raises(ValueError):
        run_job(tmp_path, monkeypatch, limit=limit)


def test_cached_photo_is_refetched_when_the_image_url_changes(tmp_path, monkeypatch):
    """Review #7: the cache key includes the URL."""
    monkeypatch.setattr(tag, "IMAGE_DIR", tmp_path)
    old = type("FakeItem", (), {"item_id": "abc", "name": "Test", "image_url": "https://example.com/old.jpg"})()
    new = type("FakeItem", (), {"item_id": "abc", "name": "Test", "image_url": "https://example.com/new.jpg"})()

    assert tag.image_path(old) != tag.image_path(new)
