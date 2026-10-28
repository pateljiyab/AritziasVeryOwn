"""The catalog in memory, with listings of the same product grouped together.

Channel3 lists many products more than once: one row per colorway, plus renamed copies
("The Effortless Pant" / "The Effortless Pant™", "Aven Pant | Aritzia US"). Without grouping,
an outfit could offer two colorways of one product, and rejecting a piece would just bring
back the same thing in another color.
"""

from __future__ import annotations

import re
from collections import defaultdict

from stylist.core.models import SLOTS, Item

# Words that vary between listings of one product: retailer suffixes, collab brand names,
# and "coin penny" (G.H.BASS lists the Spence loafer both with and without it).
_NOISE = re.compile(
    r"\|\s*aritzia us|\b(?:women'?s|wmns|g\.?\s?h\.?\s?bass(?:\s*&\s*co\.?)?|sperry|salomon|aritzia|x|coin penny)\b"
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def product_key(name: str) -> str:
    """A name with listing noise removed, so copies of one product share a key.

    Known miss: "G.h.bass X Aritzia Loafer" is the Milton loafer, but its name doesn't say so.
    """
    text = _NOISE.sub(" ", name.lower().replace("™", "").replace("®", ""))
    return _NON_ALNUM.sub(" ", text).strip()


class Catalog:
    def __init__(self, items: list[Item]) -> None:
        self.items = {item.item_id: item for item in items}
        self._by_slot: dict[str, list[Item]] = defaultdict(list)
        self._by_key: dict[str, list[Item]] = defaultdict(list)
        for item in sorted(items, key=lambda i: i.item_id):
            self._by_slot[item.slot].append(item)
            self._by_key[item.product_key].append(item)

    def __len__(self) -> int:
        return len(self.items)

    def get(self, item_id: str) -> Item | None:
        return self.items.get(item_id)

    def in_slot(self, slot: str) -> list[Item]:
        return self._by_slot.get(slot, [])

    def listings(self, key: str) -> list[Item]:
        """Every listing of one product."""
        return self._by_key.get(key, [])

    def slot_counts(self) -> dict[str, int]:
        return {slot: len(self.in_slot(slot)) for slot in SLOTS}
