"""The refinement loop: lock, unlock, reject, swap, nudge, accept (FR-FB-1 to -5, -7)."""

import pytest
from pydantic import ValidationError

from factories import basic_catalog, make_item
from stylist.core.catalog import Catalog
from stylist.core.feedback import FeedbackError, SessionState, apply_feedback, start, summary
from stylist.core.models import FeedbackIntent, ParsedRequest

SLOTS = ("top", "bottom", "dress", "outerwear", "shoes", "accessory")


def ranged_catalog() -> Catalog:
    """Three products per slot at every formality level, in varied colors."""
    colors = ["black", "cream", "navy"]
    return Catalog([make_item(f"{slot}-f{f}-{n}", slot, formality=f, colors=(colors[n],))
                    for slot in SLOTS for f in range(1, 6) for n in range(3)])


def act(catalog, state, **intent):
    return apply_feedback(catalog, state, FeedbackIntent(**intent))


def mean_formality(outfit):
    return sum(i.formality for i in outfit.pieces.values()) / len(outfit.pieces)


def test_locked_piece_survives_every_regeneration():  # FR-FB-1
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest(formality=3))
    top = outfit.pieces["top"].item_id

    state, unchanged = act(catalog, state, action="lock", slot="top")
    assert unchanged.item_ids == outfit.item_ids  # locking alone doesn't change the outfit
    for intent in ({"action": "nudge", "formality": 1}, {"action": "swap", "slot": "shoes"},
                   {"action": "nudge", "formality": 1}, {"action": "reject", "slot": "bottom"}):
        state, outfit = act(catalog, state, **intent)
        assert outfit.pieces["top"].item_id == top


def test_unlock_releases_the_piece():
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest(formality=3))
    state, _ = act(catalog, state, action="lock", item_id=outfit.pieces["top"].item_id)

    state, _ = act(catalog, state, action="unlock", slot="top")

    assert state.locked == {}


def test_rejected_product_never_comes_back():  # FR-FB-2
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest(formality=3))
    rejected = outfit.pieces["shoes"].product_key

    state, outfit = act(catalog, state, action="reject", slot="shoes")
    for _ in range(6):
        assert outfit.pieces["shoes"].product_key != rejected
        state, outfit = act(catalog, state, action="swap", slot="shoes")
    assert outfit.pieces["shoes"].product_key != rejected


def test_rejecting_a_locked_piece_unlocks_it():
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest())
    state, _ = act(catalog, state, action="lock", slot="shoes")

    state, _ = act(catalog, state, action="reject", slot="shoes")

    assert "shoes" not in state.locked


@pytest.mark.parametrize("slot", ["shoes", "top", "bottom"])
def test_swap_replaces_one_slot_and_keeps_the_rest(slot):  # FR-FB-3
    catalog = ranged_catalog()
    state, before = start(catalog, ParsedRequest(formality=3))

    state, after = act(catalog, state, action="swap", slot=slot)

    assert after.pieces[slot].item_id != before.pieces[slot].item_id
    assert {s: i.item_id for s, i in after.pieces.items() if s != slot} == \
           {s: i.item_id for s, i in before.pieces.items() if s != slot}


def test_swapping_a_dress_gives_another_dress():
    dress_first = Catalog([*ranged_catalog().items.values(),
                           make_item("best-dress", "dress", vibes=("romantic",), formality=4)])
    state, before = start(dress_first, ParsedRequest(vibes=["romantic"], formality=4))
    assert "dress" in before.pieces

    state, after = act(dress_first, state, action="swap", slot="dress")

    assert "dress" in after.pieces and after.pieces["dress"].item_id != before.pieces["dress"].item_id


def test_dressier_raises_mean_formality_and_more_relaxed_lowers_it():  # FR-FB-4
    catalog = ranged_catalog()
    state, start_outfit = start(catalog, ParsedRequest(formality=3))

    _, dressier = act(catalog, state, action="nudge", formality=1)
    _, relaxed = act(catalog, state, action="nudge", formality=-1)

    assert mean_formality(dressier) > mean_formality(start_outfit) > mean_formality(relaxed)


def test_nudge_without_a_target_starts_from_the_outfit():
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest())  # no formality asked for

    state, _ = act(catalog, state, action="nudge", formality=1)

    assert state.target_formality == min(5, round(mean_formality(outfit)) + 1)


def test_formality_nudges_stop_at_the_ends_of_the_scale():
    catalog = ranged_catalog()
    state, _ = start(catalog, ParsedRequest(formality=5))

    state, _ = act(catalog, state, action="nudge", formality=1)

    assert state.target_formality == 5


def test_vibe_nudge_puts_that_vibe_first():
    catalog = basic_catalog([make_item("edgy-top", "top", vibes=("edgy",))])
    state, _ = start(catalog, ParsedRequest(vibes=["minimal", "cozy"]))

    state, outfit = act(catalog, state, action="nudge", vibe="edgy")

    assert state.request.vibes == ["edgy", "minimal", "cozy"]
    assert outfit.pieces["top"].item_id == "edgy-top"


def test_next_outfit_is_never_the_same_as_the_last_one():  # FR-GEN-5
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest(formality=3))

    for _ in range(3):
        previous = outfit.item_ids
        state, outfit = act(catalog, state, action="nudge", vibe="minimal")
        assert outfit.item_ids != previous


def test_fully_locked_outfit_can_still_be_nudged():
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest(formality=3))
    for slot in outfit.pieces:
        state, _ = act(catalog, state, action="lock", slot=slot)

    state, again = act(catalog, state, action="nudge", formality=1)

    assert again.item_ids >= outfit.item_ids  # every locked piece stays; empty optional slots may fill


def test_accept_ends_the_session_with_a_summary():  # FR-FB-5
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest())

    state, final = act(catalog, state, action="accept")
    info = summary(final)

    assert state.accepted and final.item_ids == outfit.item_ids
    assert info["total_price"] == outfit.total_price
    assert all(p["product_url"].startswith("https://") for p in info["pieces"])
    with pytest.raises(FeedbackError, match="already finished"):
        act(catalog, state, action="swap", slot="shoes")


def test_button_and_chat_targets_behave_the_same():  # FR-FB-7
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest(formality=3))
    shoes = outfit.pieces["shoes"].item_id

    by_button, _ = act(catalog, state, action="reject", item_id=shoes)
    by_chat, _ = act(catalog, state, action="reject", slot="shoes")

    assert by_button == by_chat


@pytest.mark.parametrize("intent,error", [
    ({"action": "lock", "slot": "dress"}, "has no dress"),
    ({"action": "swap", "item_id": "nope"}, "unknown item"),
    ({"action": "reject", "item_id": "shoes-f1-0"}, "isn't in the current outfit"),
])
def test_actions_on_pieces_not_in_the_outfit_are_errors(intent, error):
    catalog = ranged_catalog()
    state, outfit = start(catalog, ParsedRequest(formality=5))
    assert "dress" not in outfit.pieces and "shoes-f1-0" not in outfit.item_ids

    with pytest.raises(FeedbackError, match=error):
        act(catalog, state, **intent)


@pytest.mark.parametrize("intent", [{"action": "lock"}, {"action": "nudge"}, {"action": "explode"}])
def test_malformed_intents_fail_validation(intent):
    with pytest.raises(ValidationError):
        FeedbackIntent(**intent)


def test_session_state_round_trips_through_json():
    catalog = ranged_catalog()
    state, _ = start(catalog, ParsedRequest(vibes=["cozy"], formality=2))
    state, _ = act(catalog, state, action="reject", slot="shoes")

    assert SessionState.model_validate_json(state.model_dump_json()) == state
