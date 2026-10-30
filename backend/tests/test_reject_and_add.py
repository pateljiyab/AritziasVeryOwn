"""A rejected product never comes back, whatever the reason given; adding a piece never breaks the budget."""

import pytest

from factories import make_item, vector
from stylist.core import keyword_parser
from stylist.core.catalog import Catalog
from stylist.core.feedback import FeedbackError, apply_feedback, start
from stylist.core.models import FeedbackIntent, ParsedRequest
from stylist.core.session import handle_action, handle_message

SLOTS = ("top", "bottom", "dress", "outerwear", "shoes", "accessory")


def catalog() -> Catalog:
    """Four products a slot, at $20, $40, $60 and $80, in black, cream, navy and camel. The $80 one
    is the favourite for a minimal look, so there's always something cheaper to fall back on."""
    colors = ["black", "cream", "navy", "camel"]
    return Catalog([make_item(f"{slot}-{n}", slot, price=20.0 * (n + 1), colors=(colors[n],),
                              vibes=("minimal",) if n == 3 else ("cozy",))
                    for slot in SLOTS for n in range(4)])


def act(cat, state, **intent):
    return apply_feedback(cat, state, FeedbackIntent(**intent))


def embed(text):
    return vector(text) if text else None


@pytest.mark.parametrize("reason", [{}, {"cheaper": True}, {"color": "cream"}], ids=["plain", "cheaper", "color"])
def test_a_rejected_piece_never_comes_back_whatever_the_reason(reason):
    cat = catalog()
    state, outfit = start(cat, ParsedRequest(vibes=["minimal"]))
    shoes = outfit.pieces["shoes"]

    state, _ = act(cat, state, action="reject", item_id=shoes.item_id, **reason)
    for _ in range(6):  # swap through every other pair, in any color, more than once
        state, outfit = act(cat, state, action="swap", slot="shoes", color="any")
        assert outfit.pieces["shoes"].product_key != shoes.product_key


def test_too_expensive_rejects_it_for_a_cheaper_one():
    cat = catalog()
    state, outfit = start(cat, ParsedRequest(vibes=["minimal"]))
    shoes = outfit.pieces["shoes"]  # the $80 pair

    state, outfit = act(cat, state, action="reject", item_id=shoes.item_id, cheaper=True)

    assert outfit.pieces["shoes"].price < shoes.price and shoes.product_key in state.rejected


def test_another_colour_rejects_it_for_one_in_that_colour():
    cat = catalog()
    state, outfit = start(cat, ParsedRequest(vibes=["minimal"]))
    shoes = outfit.pieces["shoes"]

    state, outfit = act(cat, state, action="reject", item_id=shoes.item_id, color="navy")

    assert "navy" in outfit.pieces["shoes"].colors and shoes.product_key in state.rejected


def test_too_expensive_in_a_color_with_nothing_cheaper_says_so():
    cat = catalog()
    state, outfit = start(cat, ParsedRequest(vibes=["minimal"], colors={"shoes": "camel"}))  # only the $80 pair

    with pytest.raises(FeedbackError, match="cheaper camel shoes; ask for any color"):
        act(cat, state, action="reject", item_id=outfit.pieces["shoes"].item_id, cheaper=True)


def test_cheaper_and_color_only_go_with_a_reject():
    with pytest.raises(ValueError):
        FeedbackIntent(action="lock", slot="shoes", cheaper=True)


def test_a_rejected_piece_stays_out_of_new_requests():
    cat = catalog()
    first = handle_message(cat, None, "minimal look", embed, keyword_parser.parse)
    shoes = first.outfit.pieces["shoes"]
    rejected = handle_action(cat, first.state, FeedbackIntent(action="reject", item_id=shoes.item_id), embed)

    for text in ("what about a wedding instead?", "date night", "cozy sunday"):
        reply = handle_message(cat, rejected.state, text, embed, keyword_parser.parse)
        assert reply.outfit.pieces["shoes"].product_key != shoes.product_key


# ------------------------------------------------------------- adding a piece on a budget

def outfit_without_a_coat(cat, max_total):
    state, outfit = start(cat, ParsedRequest(vibes=["minimal"], max_total=max_total))
    if "outerwear" in outfit.pieces:
        state, outfit = act(cat, state, action="remove", slot="outerwear")
    return state, outfit


def test_adding_a_coat_that_breaks_the_budget_is_refused_and_explained():
    cat = catalog()
    state, outfit = outfit_without_a_coat(cat, max_total=60)  # $20 top + $20 bottom + $20 shoes: no room left

    with pytest.raises(FeedbackError, match=r"adding a coat or jacket would go over a \$60 budget.*\$80"):
        act(cat, state, action="swap", slot="outerwear")


def test_a_refused_coat_leaves_the_outfit_and_the_session_as_they_were():
    cat = catalog()
    state, outfit = outfit_without_a_coat(cat, max_total=60)

    reply = handle_action(cat, state, FeedbackIntent(action="swap", slot="outerwear"), embed)

    assert reply.error and "raise the budget" in reply.error
    assert reply.state == state and reply.outfit.item_ids == outfit.item_ids


def test_a_coat_that_fits_the_budget_is_added():
    cat = catalog()
    state, outfit = outfit_without_a_coat(cat, max_total=100)

    state, outfit = act(cat, state, action="swap", slot="outerwear")

    assert "outerwear" in outfit.pieces and outfit.total_price <= 100


def test_adding_a_bag_is_guarded_too():
    cat = catalog()
    state, outfit = start(cat, ParsedRequest(vibes=["minimal"], max_total=60))
    if "accessory" in outfit.pieces:
        state, outfit = act(cat, state, action="remove", slot="accessory")

    with pytest.raises(FeedbackError, match="adding an accessory would go over"):
        act(cat, state, action="swap", slot="accessory")
