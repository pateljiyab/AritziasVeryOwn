"""Build the best-scoring outfit for a request (FR-GEN-1, -3, -4, -5, FR-REQ-4, FR-REQ-5).

1. Shortlist: for each slot, score every item on its own, keep the best listing of each
   product, and take the top few. Locked slots hold only the locked item; rejected
   products are left out.
2. Enumerate: try every valid combination of the shortlists (top + bottom or a dress,
   shoes, optional outerwear and accessory) and keep the highest outfit score.

With TOP_K = 6 that is about 12,000 combinations, small enough to score them all.

A budget (FR-REQ-5) is a hard constraint. Pieces that can't fit it, even next to the cheapest
choices for the other slots, are left out of the shortlist, which also gets a few cheaper picks
(the best-scoring items may add up to more than the budget when cheaper outfits fit), and
outfits over it are skipped. When nothing fits, the result is the cheapest outfit that meets
every other constraint, so the user sees how close it gets (feedback.over_budget tells them).
"""

from __future__ import annotations

from itertools import product
from math import inf

import numpy as np

from stylist.core import scoring
from stylist.core.catalog import Catalog
from stylist.core.models import SLOTS, Constraints, Item, Outfit, ParsedRequest

TOP_K = 6
CHEAP_K = 3  # with a budget, the cheaper picks added to each slot's shortlist
OPTIONAL = None  # stands for "leave this slot out" in the enumeration
CENT = 0.005  # price comparisons allow for float rounding

Scored = tuple[Item, dict[str, float]]


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


def candidates(catalog: Catalog, slot: str, request: ParsedRequest, constraints: Constraints,
               locked: dict[str, Item]) -> list[Item]:
    """The items one slot may use before any budget: the locked item, or the slot minus rejected
    products and pieces above a "cheaper" limit, in the color asked for, with outerwear filtered
    by season."""
    if slot in locked:
        return [locked[slot]]
    if slot in constraints.empty:
        return []
    items = [i for i in catalog.in_slot(slot) if i.product_key not in constraints.rejected]
    if slot in request.colors:
        items = [i for i in items if request.colors[slot] in i.colors]
    if slot in constraints.below:
        items = [i for i in items if i.price < constraints.below[slot] - CENT]
    if slot == "outerwear" and request.season:
        # The request's season is a hard filter for outerwear only (PRD §11, resolved Q2).
        in_season = [i for i in items if not i.seasons or request.season in i.seasons]
        items = in_season or items
    return items


def price_caps(pools: dict[str, list[Item]], required: set[str], request: ParsedRequest) -> dict[str, float]:
    """The most each slot's piece can cost and still fit the budget: the budget minus the
    cheapest possible choices for the slots that would have to go with it."""
    low = {slot: min((i.price for i in items), default=inf) for slot, items in pools.items()}
    pair = low["top"] + low["bottom"] if "dress" not in required else inf
    dress = low["dress"] if not required & {"top", "bottom"} else inf
    base = min(pair, dress)
    extras = {slot: low[slot] for slot in ("outerwear", "accessory") if slot in required}
    total = request.max_total if request.max_total is not None else inf
    each = request.max_item if request.max_item is not None else inf

    caps = {}
    for slot in SLOTS:
        if slot in ("top", "bottom"):
            others = low["bottom" if slot == "top" else "top"] + low["shoes"] + sum(extras.values())
        elif slot == "dress":
            others = low["shoes"] + sum(extras.values())
        elif slot == "shoes":
            others = base + sum(extras.values())
        else:
            others = base + low["shoes"] + sum(price for s, price in extras.items() if s != slot)
        caps[slot] = min(total - others, each)
    return caps


def cheaper_picks(ranked: list[Scored], taken: list[Scored], k: int) -> list[Scored]:
    """Up to k more items from `ranked` (best first), each cheaper than every better-scoring
    item in the shortlist, so each is the best choice at its price or less; spread from the
    pricier of those down to the cheapest."""
    taken_ids = {item.item_id for item, _ in taken}
    lowest = inf
    frontier = []
    for pair in ranked:
        if pair[0].item_id in taken_ids:
            lowest = min(lowest, pair[0].price)
        elif pair[0].price < lowest - CENT:
            frontier.append(pair)
            lowest = pair[0].price
    if len(frontier) <= k:
        return frontier
    step = (len(frontier) - 1) / (k - 1)
    return [frontier[round(i * step)] for i in range(k)]


def shortlist(catalog: Catalog, slot: str, items: list[Item], request: ParsedRequest, query: np.ndarray | None,
              formality: int | None, cap: float = inf, cheap: bool = False) -> list[Scored]:
    """The best TOP_K products among `items`, each with its score terms. Items over `cap` are
    left out, and `cheap` adds a few cheaper picks for budgets."""
    if cap < inf:
        items = [i for i in items if i.price <= cap + CENT]
    relevance = scoring.relevance_scores(catalog.in_slot(slot), query)  # whole slot: stable across turns
    scored = [(item, scoring.item_terms(item, request, relevance.get(item.item_id), formality)) for item in items]
    scored.sort(key=lambda pair: (-scoring.weighted(pair[1]), pair[0].item_id))

    best_per_product: dict[str, Scored] = {}
    for item, terms in scored:
        best_per_product.setdefault(item.product_key, (item, terms))
    picks = list(best_per_product.values())[:TOP_K]
    if not cheap:
        return picks
    # From every listing, not just each product's best: a cheaper colorway matters on a budget.
    return picks + cheaper_picks(scored, picks, CHEAP_K)


def generate(catalog: Catalog, request: ParsedRequest, query: np.ndarray | None = None,
             constraints: Constraints | None = None, formality: int | None = None) -> Outfit:
    """The best outfit. `query` is the CLIP embedding of the request text (None skips the
    relevance term); `formality` overrides the request's target, for nudges."""
    constraints = constraints or Constraints()
    try:
        return search(catalog, request, query, constraints, formality, within_budget=request.has_budget)
    except NoOutfitError:
        if not request.has_budget:
            raise
    return search(catalog, request, query, constraints, formality, cheapest=True)


def search(catalog: Catalog, request: ParsedRequest, query: np.ndarray | None, constraints: Constraints,
           formality: int | None, *, within_budget: bool = False, cheapest: bool = False) -> Outfit:
    """The highest-scoring valid outfit, kept within the budget if `within_budget`; or with
    `cheapest`, the lowest-priced valid outfit (the best-scoring one at that price)."""
    target = formality if formality is not None else request.formality
    locked = check_locks(catalog, constraints.locked)
    required = constraints.required | locked.keys() | request.colors.keys()  # "a black bag" asks for a bag
    if request.needs_outerwear:
        required = required | {"outerwear"}

    pools = {slot: candidates(catalog, slot, request, constraints, locked) for slot in SLOTS}
    caps = price_caps(pools, required, request) if within_budget else dict.fromkeys(SLOTS, inf)
    caps.update(dict.fromkeys(locked, inf))  # a locked piece stays, whatever it costs
    cheap = cheapest or (within_budget and request.max_total is not None)
    lists = {slot: shortlist(catalog, slot, pools[slot], request, query, target, caps[slot], cheap) for slot in SLOTS}

    if not lists["shoes"]:
        raise NoOutfitError("no shoes left to choose from")
    bases = []
    if "dress" not in required and lists["top"] and lists["bottom"]:
        bases += [(t, b) for t, b in product(lists["top"], lists["bottom"])]
    if not required & {"top", "bottom"}:
        bases += [(d,) for d in lists["dress"]]
    if not bases:
        raise NoOutfitError("no top and bottom, or dress, left to choose from")

    if "outerwear" in required and not lists["outerwear"]:
        raise NoOutfitError("no outerwear left to choose from, and this outfit needs some")
    if "accessory" in required and not lists["accessory"]:
        raise NoOutfitError("no accessories left to choose from")
    outer_options = lists["outerwear"] if "outerwear" in required else [OPTIONAL, *lists["outerwear"]]
    accessory_options = lists["accessory"] if "accessory" in required else [OPTIONAL, *lists["accessory"]]

    limit = request.max_total + CENT if within_budget and request.max_total is not None else inf
    best: tuple[tuple[float, ...], list[Item], dict[str, float]] | None = None
    for base, shoes, outer, accessory in product(bases, lists["shoes"], outer_options, accessory_options):
        chosen = [p for p in (*base, shoes, outer, accessory) if p is not OPTIONAL]
        items = [item for item, _ in chosen]
        price = sum(item.price for item in items)
        if price > limit or (cheapest and best is not None and -price < best[0][0] - CENT):
            continue
        if sum(item.main_color == "multi" for item in items) > 1:
            continue  # at most one print per outfit (PRD Appendix A)
        if frozenset(item.item_id for item in items) in constraints.avoid:
            continue
        breakdown = scoring.outfit_breakdown([terms for _, terms in chosen], items)
        score = scoring.weighted(breakdown)
        key = (round(-price, 2), score) if cheapest else (score,)
        if best is None or key > best[0]:
            best = (key, items, breakdown)

    if best is None:
        if limit < inf:
            raise NoOutfitError(f"nothing fits a budget of ${request.max_total:,.0f}")
        raise NoOutfitError("every valid outfit has already been shown or breaks the print rule")
    key, items, breakdown = best
    score = key[-1]
    pieces = {item.slot: item for item in sorted(items, key=lambda i: SLOTS.index(i.slot))}
    return Outfit(pieces, round(score, 4), {term: round(value, 4) for term, value in breakdown.items()})
