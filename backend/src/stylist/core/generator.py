"""Build the best-scoring outfit for a request (FR-GEN-1, -3, -4, -5, FR-REQ-4).

1. Shortlist: for each slot, score every item on its own, keep the best listing of each
   product, and take the top few. Locked slots hold only the locked item; rejected
   products are left out.
2. Enumerate: try every valid combination of the shortlists (top + bottom or a dress,
   shoes, optional outerwear and accessory) and keep the highest outfit score.

With TOP_K = 6 that is about 12,000 combinations, small enough to score them all.
"""

from __future__ import annotations

from itertools import product

import numpy as np

from stylist.core import scoring
from stylist.core.catalog import Catalog
from stylist.core.models import SLOTS, Constraints, Item, Outfit, ParsedRequest

TOP_K = 6
OPTIONAL = None  # stands for "leave this slot out" in the enumeration


class NoOutfitError(Exception):
    """No valid outfit fits the constraints, e.g. every pair of shoes was rejected."""


def check_locks(catalog: Catalog, locked: dict[str, str]) -> dict[str, Item]:
    items = {}
    for slot, item_id in locked.items():
        item = catalog.get(item_id)
        if item is None:
            raise ValueError(f"locked item {item_id!r} is not in the catalog")
        if item.slot != slot:
            raise ValueError(f"{item.name} is a {item.slot}, not a {slot}")
        items[slot] = item
    if "dress" in items and items.keys() & {"top", "bottom"}:
        raise ValueError("a dress can't be locked together with a top or bottom")
    return items


def shortlist(catalog: Catalog, slot: str, request: ParsedRequest, query: np.ndarray | None,
              formality: int | None, constraints: Constraints, locked: dict[str, Item],
              ) -> list[tuple[Item, dict[str, float]]]:
    """The best TOP_K products for one slot, each with its score terms."""
    if slot in locked:
        items = [locked[slot]]
    else:
        items = [i for i in catalog.in_slot(slot) if i.product_key not in constraints.rejected]
        if slot == "outerwear" and request.season:
            # The request's season is a hard filter for outerwear only (PRD §11, resolved Q2).
            in_season = [i for i in items if not i.seasons or request.season in i.seasons]
            items = in_season or items

    relevance = scoring.relevance_scores(items, query)
    scored = [(item, scoring.item_terms(item, request, relevance.get(item.item_id), formality)) for item in items]
    scored.sort(key=lambda pair: (-scoring.weighted(pair[1]), pair[0].item_id))

    best_per_product: dict[str, tuple[Item, dict[str, float]]] = {}
    for item, terms in scored:
        best_per_product.setdefault(item.product_key, (item, terms))
    return list(best_per_product.values())[:TOP_K]


def generate(catalog: Catalog, request: ParsedRequest, query: np.ndarray | None = None,
             constraints: Constraints | None = None, formality: int | None = None) -> Outfit:
    """The best outfit. `query` is the CLIP embedding of the request text (None skips the
    relevance term); `formality` overrides the request's target, for nudges."""
    constraints = constraints or Constraints()
    target = formality if formality is not None else request.formality
    locked = check_locks(catalog, constraints.locked)
    lists = {slot: shortlist(catalog, slot, request, query, target, constraints, locked) for slot in SLOTS}

    required = constraints.required | locked.keys()
    if not lists["shoes"]:
        raise NoOutfitError("no shoes left to choose from")
    bases = []
    if "dress" not in required and lists["top"] and lists["bottom"]:
        bases += [(t, b) for t, b in product(lists["top"], lists["bottom"])]
    if not required & {"top", "bottom"}:
        bases += [(d,) for d in lists["dress"]]
    if not bases:
        raise NoOutfitError("no top and bottom, or dress, left to choose from")

    if request.needs_outerwear:
        required = required | {"outerwear"}
    if "outerwear" in required and not lists["outerwear"]:
        raise NoOutfitError("no outerwear left to choose from, and this outfit needs some")
    if "accessory" in required and not lists["accessory"]:
        raise NoOutfitError("no accessories left to choose from")
    outer_options = lists["outerwear"] if "outerwear" in required else [OPTIONAL, *lists["outerwear"]]
    accessory_options = lists["accessory"] if "accessory" in required else [OPTIONAL, *lists["accessory"]]

    best: tuple[float, list[Item], dict[str, float]] | None = None
    for base, shoes, outer, accessory in product(bases, lists["shoes"], outer_options, accessory_options):
        chosen = [p for p in (*base, shoes, outer, accessory) if p is not OPTIONAL]
        items = [item for item, _ in chosen]
        if sum(item.main_color == "multi" for item in items) > 1:
            continue  # at most one print per outfit (PRD Appendix A)
        if frozenset(item.item_id for item in items) in constraints.avoid:
            continue
        breakdown = scoring.outfit_breakdown([terms for _, terms in chosen], items)
        score = scoring.weighted(breakdown)
        if best is None or score > best[0]:
            best = (score, items, breakdown)

    if best is None:
        raise NoOutfitError("every valid outfit has already been shown or breaks the print rule")
    score, items, breakdown = best
    pieces = {item.slot: item for item in sorted(items, key=lambda i: SLOTS.index(i.slot))}
    return Outfit(pieces, round(score, 4), {term: round(value, 4) for term, value in breakdown.items()})
