"""Leaving a piece out ("no bag", "I don't need a coat"), and a budget set before the first outfit."""

import pytest

from factories import basic_catalog, vector
from stylist.core import keyword_parser
from stylist.core.feedback import FeedbackError, apply_feedback, start
from stylist.core.models import FeedbackIntent, ParsedRequest
from stylist.core.session import handle_action, handle_message

CHILLY = ParsedRequest(vibes=["minimal"], weather=["cool"])  # outerwear required (FR-REQ-4)


def act(catalog, state, **intent):
    return apply_feedback(catalog, state, FeedbackIntent(**intent))


def embed(text):
    return vector(text) if text else None


def with_accessory(catalog):
    """A session whose outfit has an accessory, so there's one to leave out."""
    state, outfit = start(catalog, CHILLY)
    if "accessory" not in outfit.pieces:
        state, outfit = act(catalog, state, action="swap", slot="accessory")
    return state, outfit


# ------------------------------------------------------------- remove

def test_remove_leaves_the_piece_out_and_keeps_the_rest():
    catalog = basic_catalog()
    state, outfit = with_accessory(catalog)

    state, after = act(catalog, state, action="remove", slot="accessory")

    assert "accessory" not in after.pieces
    assert {s: i.item_id for s, i in after.pieces.items()} == {
        s: i.item_id for s, i in outfit.pieces.items() if s != "accessory"}
    assert state.omitted == {"accessory"}


def test_a_removed_piece_stays_out_through_later_changes():
    catalog = basic_catalog()
    state, _ = with_accessory(catalog)
    state, _ = act(catalog, state, action="remove", slot="accessory")

    for intent in ({"action": "swap", "slot": "shoes"}, {"action": "nudge", "formality": 1},
                   {"action": "reject", "slot": "top"}):
        state, outfit = act(catalog, state, **intent)
        assert "accessory" not in outfit.pieces


def test_removing_the_coat_wins_over_the_weather():
    catalog = basic_catalog()
    state, outfit = start(catalog, CHILLY)
    assert "outerwear" in outfit.pieces

    state, outfit = act(catalog, state, action="remove", slot="outerwear")
    state, outfit = act(catalog, state, action="nudge", formality=1)

    assert "outerwear" not in outfit.pieces


def test_asking_for_the_piece_again_brings_it_back():
    catalog = basic_catalog()
    state, _ = with_accessory(catalog)
    state, _ = act(catalog, state, action="remove", slot="accessory")

    state, outfit = act(catalog, state, action="swap", slot="accessory")  # "add a bag"

    assert "accessory" in outfit.pieces and state.omitted == set()


def test_removing_a_locked_piece_unlocks_it():
    catalog = basic_catalog()
    state, outfit = with_accessory(catalog)
    state, _ = act(catalog, state, action="lock", slot="accessory")

    state, _ = act(catalog, state, action="remove", item_id=outfit.pieces["accessory"].item_id)

    assert "accessory" not in state.locked


def test_removing_a_piece_the_outfit_doesnt_have_keeps_it_out_from_now_on():
    catalog = basic_catalog()
    state, outfit = start(catalog, CHILLY)
    state, outfit = act(catalog, state, action="remove", slot="accessory") if "accessory" in outfit.pieces \
        else (state, outfit)

    state, again = act(catalog, state, action="remove", slot="accessory")

    assert again.item_ids == outfit.item_ids and state.omitted == {"accessory"}


@pytest.mark.parametrize("slot", ["shoes", "top"])
def test_a_piece_every_outfit_needs_cant_be_removed(slot):
    catalog = basic_catalog()
    state, outfit = start(catalog, ParsedRequest(vibes=["minimal"]))
    if slot not in outfit.pieces:
        pytest.skip("this outfit is a dress")

    with pytest.raises(FeedbackError, match="every outfit needs"):
        act(catalog, state, action="remove", slot=slot)


def test_a_new_request_keeps_the_pieces_left_out():
    catalog = basic_catalog()
    first = handle_message(catalog, None, "chilly walk", embed, keyword_parser.parse)
    removed = handle_action(catalog, first.state, FeedbackIntent(action="remove", slot="outerwear"), embed)

    reply = handle_message(catalog, removed.state, "what about a rainy day instead?", embed, keyword_parser.parse)

    assert "outerwear" not in reply.outfit.pieces
    assert "Leaving out the coats or jackets, as you asked." in reply.notes


@pytest.mark.parametrize("text, slot", [("no bag", "accessory"), ("i don't need a coat", "outerwear"),
                                        ("skip the accessories", "accessory"), ("lose the jacket", "outerwear")])
def test_chat_leaves_optional_pieces_out(text, slot):
    assert keyword_parser.parse(text, True) == FeedbackIntent(action="remove", slot=slot)


@pytest.mark.parametrize("text, slot", [("not the bag", "accessory"), ("drop the shoes", "shoes")])
def test_disliking_a_piece_or_dropping_a_needed_one_is_still_a_reject(text, slot):
    assert keyword_parser.parse(text, True) == FeedbackIntent(action="reject", slot=slot)


# ------------------------------------------------------------- budget first

def test_budget_before_the_first_outfit_is_kept_for_it():
    catalog = basic_catalog()

    budgeted = handle_action(catalog, None, FeedbackIntent(action="budget", max_total=150), embed)
    reply = handle_message(catalog, budgeted.state, "minimal look", embed, keyword_parser.parse)

    assert budgeted.outfit is None and budgeted.error is None
    assert budgeted.notes == ["Got it: I'll keep to a $150 budget."]
    assert reply.state.request.max_total == 150 and reply.outfit.total_price <= 150


def test_clearing_the_budget_before_the_first_outfit():
    catalog = basic_catalog()
    budgeted = handle_action(catalog, None, FeedbackIntent(action="budget", max_total=150), embed)

    cleared = handle_action(catalog, budgeted.state, FeedbackIntent(action="budget"), embed)

    assert not cleared.state.request.has_budget and cleared.notes == ["Budget cleared."]


def test_other_actions_still_need_an_outfit():
    reply = handle_action(basic_catalog(), None, FeedbackIntent(action="remove", slot="accessory"), embed)

    assert reply.error and reply.outfit is None
