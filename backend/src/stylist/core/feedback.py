"""The refinement loop (Flow D): one session's state and how each action changes it (FR-FB).

Buttons and chat both end up here as a FeedbackIntent, so they behave the same (FR-FB-7).

- lock / unlock: pin a piece through later outfits, or release it. The outfit doesn't change.
- reject: never show that product again this session, and replace just that piece.
- swap: replace just that piece; it may come back later.
- nudge: move the target formality one step, or put a vibe first, then rebuild the outfit
  around the locked pieces.
- accept: end the session. The final outfit is the summary (links and total price, FR-FB-5).

The state stores item ids only, so it can be saved to the database as JSON.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, Field

from stylist.core.catalog import Catalog
from stylist.core.generator import NoOutfitError, generate
from stylist.core.models import Constraints, FeedbackIntent, Item, Outfit, ParsedRequest

DEFAULT_FORMALITY = 3


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


def build(catalog: Catalog, state: SessionState, query: np.ndarray | None, *,
          keep: dict[str, str] | None = None, required: set[str] = frozenset(), exclude: set[str] = frozenset(),
          ) -> tuple[SessionState, Outfit]:
    """Generate the next outfit and record it. `keep` pins pieces for this one generation
    (on top of the session's locks); `exclude` leaves out products for this generation only."""
    constraints = Constraints(
        locked={**state.locked, **(keep or {})},
        required=set(required),
        rejected=state.rejected | set(exclude),
        avoid={frozenset(state.current.values())} if state.current else set(),  # FR-GEN-5
    )
    try:
        outfit = generate(catalog, state.request, query, constraints, state.target_formality)
    except NoOutfitError:
        if not constraints.avoid:
            raise
        # Not repeating the last outfit is a nice-to-have (P1); when every piece is locked,
        # showing the same outfit beats showing nothing.
        constraints.avoid = set()
        outfit = generate(catalog, state.request, query, constraints, state.target_formality)
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

    item = target_piece(catalog, state, intent)
    if intent.action == "lock":
        locked = {**state.locked, item.slot: item.item_id}
        if "dress" in locked and locked.keys() & {"top", "bottom"}:
            raise FeedbackError("a dress can't be locked together with a top or bottom")
        return state.model_copy(update={"locked": locked}), outfit_of(catalog, state)
    if intent.action == "unlock":
        locked = {slot: i for slot, i in state.locked.items() if i != item.item_id}
        return state.model_copy(update={"locked": locked}), outfit_of(catalog, state)

    # reject or swap: replace this one piece and keep every other piece as it is (FR-FB-3)
    others = {slot: i for slot, i in state.current.items() if slot != item.slot}
    locked = {slot: i for slot, i in state.locked.items() if i != item.item_id}
    rejected = (state.rejected | {item.product_key}) if intent.action == "reject" else state.rejected
    state = state.model_copy(update={"locked": locked, "rejected": rejected})
    return build(catalog, state, query, keep=others, required={item.slot}, exclude={item.product_key})


def summary(outfit: Outfit) -> dict:
    """The accepted outfit: each piece with its link, and the total price (FR-FB-5)."""
    return {
        "pieces": [{"slot": slot, "name": item.name, "brand_line": item.brand_line, "price": item.price,
                    "product_url": item.product_url} for slot, item in outfit.pieces.items()],
        "total_price": outfit.total_price,
        "currency": next(iter(outfit.pieces.values())).currency if outfit.pieces else "USD",
    }
