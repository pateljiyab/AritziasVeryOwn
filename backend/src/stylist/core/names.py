"""Resolve a typed product name to a catalog product (FR-NAME-1, -2, -3).

Names are compared after the same clean-up as product grouping (core.catalog.product_key),
so "Effortless Pant™" and "effortless pant" are the same text. The score blends two
rapidfuzz measures:
- token_set_ratio ignores missing words ("effortless pant" fully matches every Effortless
  Pant variant), so partial names still match;
- token_sort_ratio rewards using the whole name, so the closest variant ranks first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from stylist.core.catalog import Catalog, product_key
from stylist.core.models import Item

MATCH_THRESHOLD = 75  # below this, nothing matches (FR-NAME-2)
AMBIGUITY_MARGIN = 5  # a runner-up this close to the best also counts as a match (FR-NAME-3)
MAX_SUGGESTIONS = 3


@dataclass
class Resolution:
    item: Item | None  # the best match, or None if nothing is close enough
    score: float
    choices: list[Item] = field(default_factory=list)  # near-ties with the best match, best first
    suggestions: list[Item] = field(default_factory=list)  # closest products when nothing matched

    @property
    def ambiguous(self) -> bool:
        return len(self.choices) > 1


def similarity(query_key: str, name_key: str) -> float:
    return 0.6 * fuzz.token_set_ratio(query_key, name_key) + 0.4 * fuzz.token_sort_ratio(query_key, name_key)


def representatives(catalog: Catalog, slot: str | None = None) -> list[Item]:
    """One listing per product (the first by item_id), so colorways don't count as ties."""
    seen: dict[str, Item] = {}
    for item in sorted(catalog.items.values(), key=lambda i: i.item_id):
        if slot is None or item.slot == slot:
            seen.setdefault(item.product_key, item)
    return list(seen.values())


def resolve(catalog: Catalog, query: str, slot: str | None = None) -> Resolution:
    """The product a user meant by `query`, optionally only within one slot."""
    query_key = product_key(query)
    ranked = sorted(((similarity(query_key, item.product_key), item) for item in representatives(catalog, slot)),
                    key=lambda pair: (-pair[0], pair[1].product_key))
    if not ranked or not query_key:
        return Resolution(item=None, score=0.0)

    best_score, best = ranked[0]
    if best_score < MATCH_THRESHOLD:
        return Resolution(item=None, score=round(best_score, 1),
                          suggestions=[item for _, item in ranked[:MAX_SUGGESTIONS]])
    if best_score == 100:  # the full name, typed exactly: never ask "which one?"
        return Resolution(item=best, score=100.0, choices=[best])
    choices = [item for score, item in ranked if score >= max(MATCH_THRESHOLD, best_score - AMBIGUITY_MARGIN)]
    return Resolution(item=best, score=round(best_score, 1), choices=choices[:MAX_SUGGESTIONS])
