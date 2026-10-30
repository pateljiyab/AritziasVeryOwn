"""Budgets (FR-REQ-5, FR-FB-8, FR-FB-9): a limit on the whole outfit or on each piece, set in
the first message or later, kept through every later outfit, and "make it cheaper"."""

import pytest

from factories import make_item, vector
from stylist.core import keyword_parser
from stylist.core.catalog import Catalog
from stylist.core.feedback import FeedbackError, apply_feedback, over_budget, start
from stylist.core.generator import generate
from stylist.core.models import Constraints, FeedbackIntent, ParsedRequest
from stylist.core.session import handle_action, handle_message

SLOTS = ("top", "bottom", "dress", "outerwear", "shoes", "accessory")
GOING_OUT = {"vibes": ["going-out"], "formality": 3}
CHILLY = {**GOING_OUT, "weather": ["cool"]}  # outerwear required (FR-REQ-4)


def priced_catalog() -> Catalog:
    """In every slot: a $300 favourite (the best vibe match), an $80 near miss, a $20 weak match."""
    tiers = [("lux", ("going-out",), 300.0), ("mid", ("minimal", "going-out"), 80.0), ("low", ("minimal",), 20.0)]
    return Catalog([make_item(f"{slot}-{tier}", slot, vibes=vibes, price=price)
                    for slot in SLOTS for tier, vibes, price in tiers])


def act(catalog, state, **intent):
    return apply_feedback(catalog, state, FeedbackIntent(**intent))


def prices(outfit) -> dict[str, float]:
    return {slot: item.price for slot, item in outfit.pieces.items()}


# ------------------------------------------------------------- generation

def test_without_a_budget_the_favourites_win():
    outfit = generate(priced_catalog(), ParsedRequest(**CHILLY))

    assert set(prices(outfit).values()) == {300.0}


@pytest.mark.parametrize("limit", [100, 150, 250, 400, 700])
def test_the_total_never_goes_over_the_budget(limit):
    outfit = generate(priced_catalog(), ParsedRequest(**CHILLY, max_total=limit))

    assert outfit.total_price <= limit
    assert "outerwear" in outfit.pieces  # the budget doesn't drop a required slot


def test_the_budget_is_spent_where_it_helps():
    """With room for upgrades, it takes the best ones that fit instead of the cheapest outfit."""
    catalog = priced_catalog()
    cheapest = generate(catalog, ParsedRequest(**CHILLY, max_total=80))
    roomier = generate(catalog, ParsedRequest(**CHILLY, max_total=400))
    roomiest = generate(catalog, ParsedRequest(**CHILLY, max_total=700))

    assert cheapest.score < roomier.score < roomiest.score
    assert prices(roomier) == dict.fromkeys(["top", "bottom", "outerwear", "shoes"], 80.0)  # four near misses
    assert 300.0 in prices(roomiest).values()


def test_a_cheaper_colorway_of_a_favourite_is_an_option():
    """Colorways share a product, and the shortlist keeps each product's best listing; on a
    budget, the cheaper colorway of that product has to be considered too."""
    flats = [make_item("shoes-flat-black", "shoes", name="Ballet Flat", vibes=("going-out",), price=100),
             make_item("shoes-flat-sale", "shoes", name="Ballet Flat", vibes=("going-out",), price=25)]
    catalog = Catalog([*priced_catalog().items.values(), *flats])

    outfit = generate(catalog, ParsedRequest(**CHILLY, max_total=200))

    assert outfit.pieces["shoes"].item_id == "shoes-flat-sale"


def test_per_piece_limit():
    outfit = generate(priced_catalog(), ParsedRequest(**CHILLY, max_item=100))

    assert max(prices(outfit).values()) <= 100 and 80.0 in prices(outfit).values()


def test_impossible_budget_gives_the_cheapest_outfit_that_fits_everything_else():
    request = ParsedRequest(**CHILLY, max_total=50)
    outfit = generate(priced_catalog(), request)

    assert outfit.total_price == 60  # $20 dress, shoes, and the coat the weather needs
    assert "outerwear" in outfit.pieces and over_budget(outfit, request, {})


def test_a_locked_piece_stays_whatever_it_costs():
    catalog = priced_catalog()
    request = ParsedRequest(**GOING_OUT, max_total=200, max_item=50)

    outfit = generate(catalog, request, constraints=Constraints(locked={"top": "top-lux"}))

    assert outfit.pieces["top"].item_id == "top-lux"
    assert over_budget(outfit, request, {"top": "top-lux"})  # $300 alone is over $200


def test_a_lock_over_the_per_piece_limit_isnt_a_budget_break():
    request = ParsedRequest(**GOING_OUT, max_item=50)
    outfit = generate(priced_catalog(), request, constraints=Constraints(locked={"top": "top-lux"}))

    assert not over_budget(outfit, request, {"top": "top-lux"})
    assert all(item.price <= 50 for slot, item in outfit.pieces.items() if slot != "top")


# ------------------------------------------------------------- the refinement loop

def test_budget_set_after_the_first_outfit_keeps_the_request():
    """The reported bug: "keep the price under $200" threw away the occasion and the weather."""
    catalog = priced_catalog()
    state, outfit = start(catalog, ParsedRequest(**CHILLY))
    assert outfit.total_price > 200

    state, outfit = act(catalog, state, action="budget", max_total=200)

    assert outfit.total_price <= 200 and "outerwear" in outfit.pieces
    assert state.request.vibes == ["going-out"] and state.request.weather == ["cool"]
    assert state.request.max_total == 200


def test_budget_that_already_fits_keeps_the_outfit():
    catalog = priced_catalog()
    state, outfit = start(catalog, ParsedRequest(**CHILLY, max_total=150))

    state, same = act(catalog, state, action="budget", max_total=140)

    assert same.item_ids == outfit.item_ids and state.request.max_total == 140


@pytest.mark.parametrize("change", [{}, {"max_total": 900}])  # cleared, or raised
def test_a_looser_budget_rebuilds_with_more_to_choose_from(change):
    catalog = priced_catalog()
    state, tight = start(catalog, ParsedRequest(**CHILLY, max_total=120))

    state, outfit = act(catalog, state, action="budget", **change)

    assert outfit.total_price > tight.total_price and outfit.score > tight.score


def test_the_budget_holds_through_swaps_rejects_and_nudges():
    catalog = priced_catalog()
    state, outfit = start(catalog, ParsedRequest(**CHILLY, max_total=200))
    for intent in ({"action": "swap", "slot": "shoes"}, {"action": "reject", "slot": "shoes"},
                   {"action": "nudge", "vibe": "edgy"}, {"action": "nudge", "formality": 1},
                   {"action": "swap", "slot": "outerwear"}):
        state, outfit = act(catalog, state, **intent)
        assert outfit.total_price <= 200, intent


def test_cheaper_piece_replaces_only_that_piece():
    catalog = priced_catalog()
    state, outfit = start(catalog, ParsedRequest(**CHILLY))

    state, cheaper = act(catalog, state, action="cheaper", slot="outerwear")

    assert cheaper.pieces["outerwear"].price < outfit.pieces["outerwear"].price
    assert {s: i.item_id for s, i in cheaper.pieces.items() if s != "outerwear"} == \
           {s: i.item_id for s, i in outfit.pieces.items() if s != "outerwear"}


def test_cheaper_piece_when_it_is_already_the_cheapest():
    catalog = priced_catalog()
    state, _ = start(catalog, ParsedRequest(**CHILLY, max_item=20))

    with pytest.raises(FeedbackError, match="already the cheapest outerwear"):
        act(catalog, state, action="cheaper", slot="outerwear")


def test_cheaper_outfit_lowers_the_total_and_keeps_it_down():
    catalog = priced_catalog()
    state, outfit = start(catalog, ParsedRequest(**CHILLY))

    state, cheaper = act(catalog, state, action="cheaper")

    assert cheaper.total_price <= outfit.total_price * 0.85
    assert state.request.max_total is not None and cheaper.total_price <= state.request.max_total
    state, nudged = act(catalog, state, action="nudge", formality=1)
    assert nudged.total_price <= state.request.max_total  # later outfits don't creep back up


def test_cheaper_outfit_at_the_bottom_says_so():
    catalog = priced_catalog()
    state, outfit = start(catalog, ParsedRequest(**CHILLY, max_total=60))
    assert outfit.total_price == 60

    with pytest.raises(FeedbackError, match="already the cheapest outfit"):
        act(catalog, state, action="cheaper")


# ------------------------------------------------------------- chat turns

def embed(text):
    return vector(text) if text else None


def say(catalog, state, text):
    return handle_message(catalog, state, text, embed, keyword_parser.parse)


def test_reported_conversation_end_to_end():
    catalog = priced_catalog()
    first = say(catalog, None, "rooftop birthday dinner, it might get chilly")

    reply = say(catalog, first.state, "keep the price under $200")

    assert reply.parsed == FeedbackIntent(action="budget", max_total=200)
    assert reply.outfit.total_price <= 200 and "outerwear" in reply.outfit.pieces
    assert reply.notes == []


def test_budget_in_the_first_message():
    reply = say(priced_catalog(), None, "rooftop birthday dinner, it might get chilly, under $150")

    assert reply.state.request.max_total == 150 and reply.outfit.total_price <= 150


def test_a_new_request_keeps_the_session_budget():
    catalog = priced_catalog()
    first = say(catalog, None, "date night under $150")

    reply = say(catalog, first.state, "what about a wedding instead?")

    assert reply.state.request.max_total == 150 and reply.outfit.total_price <= 150
    assert "Keeping to a $150 budget." in reply.notes


def test_impossible_budget_is_explained():
    reply = say(priced_catalog(), None, "rooftop birthday dinner, it might get chilly, under $40")

    assert reply.outfit.total_price == 60
    assert reply.notes == ["Nothing fits a $40 budget for this, so here's the cheapest outfit I could put "
                           "together ($60)."]


def test_locked_pieces_over_the_budget_are_explained():
    catalog = priced_catalog()
    state, _ = start(catalog, ParsedRequest(**GOING_OUT))
    state, _ = act(catalog, state, action="lock", slot="top")  # the $300 favourite

    reply = handle_action(catalog, state, FeedbackIntent(action="budget", max_total=200), embed)

    assert reply.outfit.pieces["top"].item_id == "top-lux"
    assert reply.notes == ["Your locked pieces already cost $300, more than a $200 budget. Unlock one to get under it."]


def test_cheaper_with_nothing_cheaper_is_a_conflict_not_a_crash():
    catalog = priced_catalog()
    state, _ = start(catalog, ParsedRequest(**CHILLY, max_total=60))

    reply = handle_action(catalog, state, FeedbackIntent(action="cheaper"), embed)

    assert reply.error == "This is already the cheapest outfit I can put together for this request."
    assert reply.outfit.total_price == 60  # the outfit on screen is unchanged
