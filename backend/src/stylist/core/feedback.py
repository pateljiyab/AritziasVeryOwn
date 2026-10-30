"""The refinement loop (Flow D): one session's state and how each action changes it (FR-FB).

Buttons and chat both end up here as a FeedbackIntent, so they behave the same (FR-FB-7).

- lock / unlock: pin a piece through later outfits, or release it. The outfit doesn't change.
- reject: never show that product again this session, and replace just that piece.
- swap: replace just that piece with one not shown yet in that slot; once every option has been
  shown, they come round again. With a color ("black shoes"), the slot keeps to that color from
  then on, and a slot the outfit doesn't have gets a piece ("a black dress instead").
- nudge: move the target formality one step, or put a vibe first, then rebuild the outfit
  around the locked pieces.
- accept: end the session. The final outfit is the summary (links and total price, FR-FB-5).
- budget: set or clear the budget (FR-FB-8), then rebuild around the locked pieces if the
  outfit on screen doesn't fit it. The budget is part of the request, so every later outfit
  keeps to it.
- cheaper: replace one piece with a cheaper one, or bring the whole outfit's total down by
  about 15% (that becomes the new budget) (FR-FB-9).

The state stores item ids only, so it can be saved to the database as JSON.
"""

from __future__ import annotations

from math import floor

import numpy as np
from pydantic import BaseModel, Field

from stylist.core.catalog import Catalog
from stylist.core.generator import CENT, NoOutfitError, generate
from stylist.core.models import SLOTS, Constraints, FeedbackIntent, Item, Outfit, ParsedRequest

DEFAULT_FORMALITY = 3
CHEAPER_STEP = 0.85  # "make it cheaper" aims for a total about 15% lower


class FeedbackError(ValueError):
    """The action doesn't make sense for this session, e.g. locking a slot the outfit doesn't have."""


class SessionState(BaseModel):
    request: ParsedRequest
    target_formality: int | None = None  # the request's formality, moved by nudges
    locked: dict[str, str] = Field(default_factory=dict)  # slot -> item_id
    rejected: set[str] = Field(default_factory=set)  # product keys
    current: dict[str, str] = Field(default_factory=dict)  # slot -> item_id of the outfit on screen
    score: float = 0.0  # of the outfit on screen
    breakdown: dict[str, float] = Field(default_factory=dict)
    history: list[list[str]] = Field(default_factory=list)  # item ids of every outfit shown, oldest first
    accepted: bool = False


def outfit_of(catalog: Catalog, state: SessionState) -> Outfit:
    """The outfit on screen, rebuilt from the state."""
    pieces = {slot: catalog.items[item_id] for slot, item_id in state.current.items()}
    return Outfit(pieces, state.score, dict(state.breakdown))


def over_budget(outfit: Outfit, request: ParsedRequest, locked: dict[str, str]) -> bool:
    """Whether the outfit breaks the budget: its total is over, or an unlocked piece costs more
    than the per-piece limit (a piece the user locked is their call)."""
    if request.max_total is not None and outfit.total_price > request.max_total + CENT:
        return True
    return request.max_item is not None and any(
        item.price > request.max_item + CENT for slot, item in outfit.pieces.items() if locked.get(slot) != item.item_id)


def build(catalog: Catalog, state: SessionState, query: np.ndarray | None, *,
          keep: dict[str, str] | None = None, required: set[str] = frozenset(), exclude: set[str] = frozenset(),
          below: dict[str, float] | None = None, empty: set[str] = frozenset()) -> tuple[SessionState, Outfit]:
    """Generate the next outfit and record it. `keep` pins pieces for this one generation
    (on top of the session's locks); `exclude` leaves out products, `below` caps slot prices,
    and `empty` leaves slots out, for this generation only."""
    constraints = Constraints(
        locked={**state.locked, **(keep or {})},
        required=set(required),
        rejected=state.rejected | set(exclude),
        avoid={frozenset(state.current.values())} if state.current else set(),  # FR-GEN-5
        below=dict(below or {}),
        empty=set(empty),
    )
    try:
        outfit = generate(catalog, state.request, query, constraints, state.target_formality)
    except NoOutfitError:
        if not constraints.avoid:
            raise
        outfit = None
    if constraints.avoid and (outfit is None or over_budget(outfit, state.request, constraints.locked)):
        # Not repeating the last outfit is a nice-to-have (P1): when every piece is locked,
        # showing the same outfit beats showing nothing, and one within the budget beats one over it.
        constraints.avoid = set()
        repeat = generate(catalog, state.request, query, constraints, state.target_formality)
        if outfit is None or not over_budget(repeat, state.request, constraints.locked):
            outfit = repeat
    current = {slot: item.item_id for slot, item in outfit.pieces.items()}
    return state.model_copy(update={
        "current": current, "score": outfit.score, "breakdown": outfit.breakdown,
        "history": [*state.history, sorted(current.values())],
    }), outfit


def start(catalog: Catalog, request: ParsedRequest, query: np.ndarray | None = None,
          locked: dict[str, str] | None = None) -> tuple[SessionState, Outfit]:
    """First outfit for a request. `locked` holds pieces the user named (Flow C)."""
    state = SessionState(request=request, target_formality=request.formality, locked=dict(locked or {}))
    return build(catalog, state, query)


def target_piece(catalog: Catalog, state: SessionState, intent: FeedbackIntent) -> Item:
    if intent.item_id:
        item = catalog.get(intent.item_id)
        if item is None:
            raise FeedbackError(f"unknown item {intent.item_id!r}")
        if intent.action != "unlock" and state.current.get(item.slot) != item.item_id:
            raise FeedbackError(f"{item.name} isn't in the current outfit")
        return item
    item_id = state.current.get(intent.slot) or (state.locked.get(intent.slot) if intent.action == "unlock" else None)
    if item_id is None:
        raise FeedbackError(f"the current outfit has no {intent.slot}")
    return catalog.items[item_id]


def nudged_formality(catalog: Catalog, state: SessionState, step: int) -> int:
    """One step from the current target, or from the outfit's average if there's no target yet."""
    base = state.target_formality
    if base is None:
        levels = [catalog.items[i].formality for i in state.current.values() if catalog.items[i].formality]
        base = round(sum(levels) / len(levels)) if levels else DEFAULT_FORMALITY
    return min(5, max(1, base + step))


def apply_feedback(catalog: Catalog, state: SessionState, intent: FeedbackIntent,
                   query: np.ndarray | None = None) -> tuple[SessionState, Outfit]:
    """The new state and the outfit to show. `query` is the CLIP embedding of the request text."""
    if state.accepted:
        raise FeedbackError("this session is already finished")
    if intent.action == "accept":
        return state.model_copy(update={"accepted": True}), outfit_of(catalog, state)
    if intent.action == "nudge":
        update: dict = {}
        if intent.formality:
            update["target_formality"] = nudged_formality(catalog, state, intent.formality)
        if intent.vibe:
            vibes = [intent.vibe, *(v for v in state.request.vibes if v != intent.vibe)][:3]
            update["request"] = state.request.model_copy(update={"vibes": vibes})
        return build(catalog, state.model_copy(update=update), query)
    if intent.action == "budget":
        old = state.request
        request = old.model_copy(update={"max_total": intent.max_total, "max_item": intent.max_item})
        state = state.model_copy(update={"request": request})
        current = outfit_of(catalog, state)
        if not over_budget(current, request, state.locked) and not looser(old, request):
            return state, current  # it already fits: keep it, and keep to the budget from here on
        return build(catalog, state, query)  # over the new budget, or more room to choose from
    if intent.action == "cheaper" and not (intent.slot or intent.item_id):
        return cheaper_outfit(catalog, state, query)

    if intent.action == "swap" and intent.color is not None:
        slot = intent.slot or target_piece(catalog, state, intent).slot
        state = with_color(catalog, state, slot, intent.color)
    if intent.action == "swap" and intent.slot and not intent.item_id and intent.slot not in state.current:
        return add_piece(catalog, state, intent.slot, query)  # "a black dress instead", "add a bag"

    item = target_piece(catalog, state, intent)
    if intent.action == "lock":
        locked = {**state.locked, item.slot: item.item_id}
        if "dress" in locked and locked.keys() & {"top", "bottom"}:
            raise FeedbackError("a dress can't be locked together with a top or bottom")
        return state.model_copy(update={"locked": locked}), outfit_of(catalog, state)
    if intent.action == "unlock":
        locked = {slot: i for slot, i in state.locked.items() if i != item.item_id}
        return state.model_copy(update={"locked": locked}), outfit_of(catalog, state)

    # reject, swap, or a cheaper piece: replace this one piece and keep every other piece as it is (FR-FB-3)
    below = {}
    empty = set(SLOTS) - state.current.keys()  # only this piece changes: no new pieces appear (FR-FB-3)
    if intent.action == "cheaper":
        if not any(i.price < item.price - CENT and i.product_key not in state.rejected for i in catalog.in_slot(item.slot)):
            raise FeedbackError(f"the {item.name} is already the cheapest {item.slot} I have")
        below = {item.slot: item.price}
    others = {slot: i for slot, i in state.current.items() if slot != item.slot}
    locked = {slot: i for slot, i in state.locked.items() if i != item.item_id}
    rejected = (state.rejected | {item.product_key}) if intent.action == "reject" else state.rejected
    state = state.model_copy(update={"locked": locked, "rejected": rejected})
    if intent.action == "swap":
        # Show a piece not seen yet in this slot, so swapping goes through every option instead
        # of back and forth between two; once all of them have been shown, start over.
        seen = shown(catalog, state, item.slot) | {item.product_key}
        if options(catalog, state, item.slot, seen):
            try:
                result = replace(catalog, state, query, item.slot, others, seen, below, empty)
                if not over_budget(result[1], state.request, state.locked):
                    return result
            except NoOutfitError:
                pass
        elif not options(catalog, state, item.slot, {item.product_key}):
            color = state.request.colors.get(item.slot)
            noun = ONE[item.slot]
            raise FeedbackError(f"the {item.name} is the only {color} {noun} I have; ask for any color to see the rest"
                                if color else f"the {item.name} is the only {noun} I have")
    return replace(catalog, state, query, item.slot, others, {item.product_key}, below, empty)


# How to name what a slot holds: one of them, and several.
ONE = {"top": "top", "bottom": "bottom", "dress": "dress", "outerwear": "coat or jacket", "shoes": "pair of shoes",
       "accessory": "accessory"}
MANY = {"top": "tops", "bottom": "bottoms", "dress": "dresses", "outerwear": "coats or jackets", "shoes": "shoes",
        "accessory": "accessories"}


def shown(catalog: Catalog, state: SessionState, slot: str) -> set[str]:
    """The products already shown in this slot for the current request."""
    return {catalog.items[i].product_key for ids in state.history for i in ids
            if i in catalog.items and catalog.items[i].slot == slot}


def options(catalog: Catalog, state: SessionState, slot: str, exclude: set[str]) -> list[Item]:
    """The slot's items a swap could pick, before scoring: not rejected or excluded, and in the
    color asked for."""
    color = state.request.colors.get(slot)
    return [i for i in catalog.in_slot(slot)
            if i.product_key not in state.rejected and i.product_key not in exclude and (color is None or color in i.colors)]


def with_color(catalog: Catalog, state: SessionState, slot: str, color: str) -> SessionState:
    """Keep to `color` in this slot from now on ("any" drops it). Raises FeedbackError when the
    catalog has nothing in that color, naming the colors it does have."""
    colors = dict(state.request.colors)
    if color == "any":
        colors.pop(slot, None)
    else:
        available = [i for i in catalog.in_slot(slot) if i.product_key not in state.rejected]
        if not any(color in i.colors for i in available):
            counts: dict[str, int] = {}
            for i in available:
                if i.main_color:
                    counts[i.main_color] = counts.get(i.main_color, 0) + 1
            have = ", ".join(sorted(counts, key=lambda c: -counts[c]))
            raise FeedbackError(f"I don't have any {color} {MANY[slot]}; the ones I have come in {have}")
        colors[slot] = color
    return state.model_copy(update={"request": state.request.model_copy(update={"colors": colors})})


def add_piece(catalog: Catalog, state: SessionState, slot: str, query: np.ndarray | None) -> tuple[SessionState, Outfit]:
    """Put a piece in a slot the outfit doesn't have, replacing only what it can't be worn with."""
    clash = {"dress": {"top", "bottom"}, "top": {"dress"}, "bottom": {"dress"}}.get(slot, set())
    for locked_slot in clash & state.locked.keys():
        name = catalog.items[state.locked[locked_slot]].name
        raise FeedbackError(f"the {name} you locked can't be worn with a {slot}; unlock it first")
    keep = {s: i for s, i in state.current.items() if s not in clash}
    return replace(catalog, state, query, slot, keep, set(), {}, set(SLOTS) - keep.keys() - {slot})


def replace(catalog: Catalog, state: SessionState, query: np.ndarray | None, slot: str, keep: dict[str, str],
            exclude: set[str], below: dict[str, float], empty: set[str]) -> tuple[SessionState, Outfit]:
    """A new piece in `slot`, keeping the pieces in `keep` (FR-FB-3)."""
    new_state, outfit = build(catalog, state, query, keep=keep, required={slot}, exclude=exclude, below=below,
                              empty=empty)
    if over_budget(outfit, state.request, state.locked):
        # Keeping every other piece leaves no room in the budget for a replacement, so let the
        # unlocked pieces change too: a few more changes beat breaking the budget.
        retry = build(catalog, state, query, required={slot}, exclude=exclude, below=below, empty=empty)
        if not over_budget(retry[1], state.request, state.locked):
            return retry
    return new_state, outfit


def looser(old: ParsedRequest, new: ParsedRequest) -> bool:
    """Whether the new budget allows more than the old one: a limit removed or raised."""
    return any(before is not None and (after is None or after > before)
               for before, after in ((old.max_total, new.max_total), (old.max_item, new.max_item)))


def cheaper_outfit(catalog: Catalog, state: SessionState, query: np.ndarray | None) -> tuple[SessionState, Outfit]:
    """Bring the total down: about 15% if that can be done, else by any amount. The lower total
    becomes the session's budget, so later outfits don't creep back up."""
    total = outfit_of(catalog, state).total_price
    for limit in (floor(total * CHEAPER_STEP), round(total - 0.01, 2)):
        if limit <= 0:
            continue
        if state.request.max_total is not None:
            limit = min(limit, state.request.max_total)
        trial = state.model_copy(update={"request": state.request.model_copy(update={"max_total": limit})})
        new_state, outfit = build(catalog, trial, query)
        if outfit.total_price < total - CENT and not over_budget(outfit, trial.request, trial.locked):
            return new_state, outfit
    raise FeedbackError("this is already the cheapest outfit I can put together for this request")


def money(amount: float) -> str:
    return f"${amount:,.0f}" if amount == round(amount) else f"${amount:,.2f}"


def budget_words(request: ParsedRequest) -> str:
    """'a $200 budget', '$80 a piece', or both."""
    parts = []
    if request.max_total is not None:
        parts.append(f"a {money(request.max_total)} budget")
    if request.max_item is not None:
        parts.append(f"{money(request.max_item)} a piece")
    return " and ".join(parts)


def budget_notes(catalog: Catalog, state: SessionState, outfit: Outfit) -> list[str]:
    """What to tell the user when the outfit can't keep to the budget, and why."""
    request = state.request
    if not request.has_budget:
        return []
    notes = []
    if request.max_item is not None:
        for slot, item_id in state.locked.items():
            item = catalog.items[item_id]
            if outfit.pieces.get(slot) is item and item.price > request.max_item + CENT:
                notes.append(f"The {item.name} you locked costs {money(item.price)}, "
                             f"over your {money(request.max_item)}-a-piece limit.")
    if over_budget(outfit, request, state.locked):
        locked_cost = sum(catalog.items[i].price for i in state.locked.values())
        if request.max_total is not None and locked_cost > request.max_total + CENT:
            notes.append(f"Your locked pieces already cost {money(locked_cost)}, more than {budget_words(request)}. "
                         "Unlock one to get under it.")
        else:
            notes.append(f"Nothing fits {budget_words(request)} for this, so here's the cheapest outfit I could "
                         f"put together ({money(outfit.total_price)}).")
    return notes


def summary(outfit: Outfit) -> dict:
    """The accepted outfit: each piece with its link, and the total price (FR-FB-5)."""
    return {
        "pieces": [{"slot": slot, "name": item.name, "brand_line": item.brand_line, "price": item.price,
                    "product_url": item.product_url} for slot, item in outfit.pieces.items()],
        "total_price": outfit.total_price,
        "currency": next(iter(outfit.pieces.values())).currency if outfit.pieces else "USD",
    }
