"""The tagging job end to end, with CLIP and image downloads replaced by fakes."""

import json

import numpy as np
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
