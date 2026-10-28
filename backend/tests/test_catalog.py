"""Product grouping and loading the catalog from the database."""

import json
from datetime import UTC, datetime

import numpy as np
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from factories import make_item
from stylist.adapters.catalog_store import load_catalog
from stylist.core.catalog import Catalog, product_key
from stylist.jobs.ingest import CATALOG_CSV, ItemTag, ingest
from stylist.jobs.ingest import Item as ItemRow


@pytest.mark.parametrize("a,b", [
    ("The Effortless Pant", "The Effortless Pant™"),
    ("Aven Pant", "Aven Pant | Aritzia US"),
    ("BestHug™ Gilmore Pant", "Besthug™ Gilmore Pant"),
    ("Denim Forum Sadie Relaxed Mid-Rise Straight Jean", "Denim Forum Sadie Relaxed Mid‑Rise Straight Jean"),
    ("Sperry X Aritzia Slim Jogger", "Sperry x Aritzia Slim Jogger"),
    ("G.h.bass X Aritzia Spence Loafer", "Women's G.H.BASS x Aritzia Spence Coin Penny Loafer"),
])
def test_listings_of_one_product_share_a_key(a, b):
    assert product_key(a) == product_key(b)


def test_different_products_keep_different_keys():
    assert product_key("The Effortless Pant") != product_key("The Effortless Pant™ Lo-Rise – Twill")
    assert product_key("Sperry X Aritzia Cvo Sneaker") != product_key("Sperry X Aritzia Slim Jogger")


def test_catalog_groups_listings_by_product():
    catalog = Catalog([make_item("a", "bottom", name="Aven Pant"), make_item("b", "bottom", name="Aven Pant | Aritzia US"),
                       make_item("c", "top", name="Sebring Sweater")])

    assert {i.item_id for i in catalog.listings(product_key("Aven Pant"))} == {"a", "b"}
    assert catalog.slot_counts()["bottom"] == 2


def test_load_catalog_joins_tags_and_embeddings(tmp_path):
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    ingest(CATALOG_CSV, db)
    with Session(create_engine(db)) as session, session.begin():
        ids = list(session.scalars(select(ItemRow.item_id).order_by(ItemRow.item_id)))
        session.add(ItemTag(item_id=ids[0], source="auto", seasons=["fall"], vibes=["cozy"], colors=["grey"],
                            formality=2, color_source="hex", probabilities={}, model_version="test",
                            tagged_at=datetime.now(UTC)))
    np.save(tmp_path / "e.npy", np.ones((1, 4), dtype=np.float32))
    (tmp_path / "e.json").write_text(json.dumps({"model_version": "test", "item_ids": [ids[0]]}))

    catalog = load_catalog(db, tmp_path / "e.npy", tmp_path / "e.json")

    tagged, untagged = catalog.get(ids[0]), catalog.get(ids[1])
    assert len(catalog) == len(ids)
    assert tagged.vibes == ("cozy",) and tagged.formality == 2 and tagged.embedding.shape == (4,)
    assert untagged.vibes == () and untagged.formality is None and untagged.embedding is None


def test_load_catalog_without_the_tag_job_still_works(tmp_path):
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    ingest(CATALOG_CSV, db)

    catalog = load_catalog(db, tmp_path / "missing.npy", tmp_path / "missing.json")

    assert len(catalog) > 100
    assert all(item.embedding is None for item in catalog.items.values())
