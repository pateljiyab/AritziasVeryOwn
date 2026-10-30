"""Load the catalog for the outfit engine: items and tags from SQLite, embeddings from disk.

Season, formality, and color come from the CLIP tagger (jobs/tag.py). Vibe comes from the LLM
tagger (jobs/llm_tag.py) when it has tagged the item, since it scores better on the gold set
(eval/tagging_report.md), and from CLIP otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, aliased

from stylist.core.catalog import Catalog, product_key
from stylist.core.models import Item
from stylist.jobs.ingest import DB_URL, ROOT, ItemTag
from stylist.jobs.ingest import Item as ItemRow

EMBEDDINGS_NPY = ROOT / "data" / "embeddings.npy"
EMBEDDING_IDS_JSON = ROOT / "data" / "embedding_ids.json"
TAG_SOURCE = "auto"  # CLIP + color rules
VIBE_SOURCE = "llm"  # preferred for vibe when present


def read_embeddings(npy: Path, ids_json: Path) -> tuple[dict[str, np.ndarray], str | None]:
    """item_id -> CLIP image embedding, plus the model version that made them. Empty if the
    tag job hasn't been run."""
    if not npy.exists() or not ids_json.exists():
        return {}, None
    meta = json.loads(ids_json.read_text())
    vectors = np.load(npy)
    if len(vectors) != len(meta["item_ids"]):
        raise ValueError(f"{npy.name} has {len(vectors)} rows but {ids_json.name} lists {len(meta['item_ids'])} ids")
    return dict(zip(meta["item_ids"], vectors)), meta.get("model_version")


def load_catalog(db_url: str = DB_URL, npy: Path = EMBEDDINGS_NPY, ids_json: Path = EMBEDDING_IDS_JSON) -> Catalog:
    embeddings, _ = read_embeddings(npy, ids_json)
    engine = create_engine(db_url)
    vibe_tags = aliased(ItemTag)
    with Session(engine) as session:
        rows = session.execute(
            select(ItemRow, ItemTag, vibe_tags)
            .outerjoin(ItemTag, (ItemTag.item_id == ItemRow.item_id) & (ItemTag.source == TAG_SOURCE))
            .outerjoin(vibe_tags, (vibe_tags.item_id == ItemRow.item_id) & (vibe_tags.source == VIBE_SOURCE))
        ).all()
    engine.dispose()

    items = []
    for row, tags, llm in rows:
        vibes = llm.vibes if llm and llm.vibes else (tags.vibes if tags else [])
        items.append(Item(
            item_id=row.item_id,
            name=row.name,
            brand_line=row.brand_line,
            slot=row.slot,
            price=row.price,
            currency=row.currency,
            image_url=row.image_url,
            product_url=row.product_url,
            product_key=product_key(row.name),
            seasons=tuple(tags.seasons) if tags else (),
            vibes=tuple(vibes),
            colors=tuple(tags.colors) if tags else (),
            formality=tags.formality if tags else None,
            embedding=embeddings.get(row.item_id),
            category=row.category_path or "",
        ))
    return Catalog(items)
