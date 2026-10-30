"""Small hand-built catalogs for engine tests, so each rule can be checked in isolation."""

from __future__ import annotations

import zlib

import numpy as np

from stylist.core.catalog import Catalog, product_key
from stylist.core.models import Item

DIM = 8


def vector(seed: str) -> np.ndarray:
    v = np.random.default_rng(zlib.crc32(seed.encode())).normal(size=DIM)
    return (v / np.linalg.norm(v)).astype(np.float32)


def make_item(item_id: str, slot: str, *, name: str | None = None, vibes=("minimal",), colors=("black",),
              formality: int | None = 3, seasons=("spring", "summer", "fall", "winter"), price: float = 50.0,
              embedding: bool = True, category: str = "") -> Item:
    name = name or f"{slot.title()} {item_id}"
    return Item(
        item_id=item_id, name=name, brand_line="Aritzia", slot=slot, price=price, currency="USD",
        image_url=f"https://example.com/{item_id}.jpg", product_url=f"https://example.com/{item_id}",
        product_key=product_key(name), seasons=tuple(seasons), vibes=tuple(vibes), colors=tuple(colors),
        formality=formality, embedding=vector(item_id) if embedding else None, category=category,
    )


def basic_catalog(extra: list[Item] = ()) -> Catalog:
    """Two of every slot, all neutral and mid-formality, plus any extra items."""
    items = [make_item(f"{slot}-{n}", slot) for slot in ("top", "bottom", "dress", "outerwear", "shoes", "accessory")
             for n in (1, 2)]
    return Catalog([*items, *extra])
