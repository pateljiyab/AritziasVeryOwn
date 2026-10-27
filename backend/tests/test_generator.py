"""Outfit generation: slot rules, hard constraints, and that the scores steer the choice."""

import pytest

from factories import basic_catalog, make_item, vector
from stylist.core.catalog import Catalog
from stylist.core.generator import NoOutfitError, generate
from stylist.core.models import Constraints, ParsedRequest


def assert_valid(outfit, catalog):
    slots = set(outfit.pieces)
    assert "shoes" in slots
    assert slots >= {"top", "bottom"} or "dress" in slots  # FR-GEN-1
    assert not ("dress" in slots and slots & {"top", "bottom"})
    assert all(catalog.get(i.item_id) is i for i in outfit.pieces.values())  # FR-GEN-2
    assert all(outfit.pieces[slot].slot == slot for slot in slots)


@pytest.mark.parametrize("request_", [
    ParsedRequest(),
    ParsedRequest(text="cozy fall errands", vibes=["cozy"], formality=2, season="fall"),
    ParsedRequest(vibes=["going-out"], formality=5, weather=["cold"]),
])
def test_outfits_are_valid_for_any_request(request_):
    catalog = basic_catalog()
    outfit = generate(catalog, request_, vector("query"))

    assert_valid(outfit, catalog)
    assert set(outfit.breakdown) >= {"color"}
    assert 0 <= outfit.score <= 1


def test_score_breakdown_has_each_term_the_request_uses():
    outfit = generate(basic_catalog(), ParsedRequest(vibes=["cozy"], formality=2, season="fall"), vector("q"))
    assert set(outfit.breakdown) == {"relevance", "vibe", "formality", "season", "color"}


def test_vibe_and_formality_pick_the_matching_pieces():
    cozy = make_item("cozy-top", "top", vibes=("cozy",), formality=2)
    catalog = basic_catalog([cozy])

    outfit = generate(catalog, ParsedRequest(vibes=["cozy"], formality=2))

    assert outfit.pieces["top"] is cozy


def test_relevance_picks_the_piece_closest_to_the_query():
    catalog = basic_catalog()
    target = catalog.get("top-2")

    outfit = generate(catalog, ParsedRequest(text="anything"), query=target.embedding)

    assert outfit.pieces["top"] is target


def test_locked_item_always_appears():  # FR-FB-1 relies on this
    catalog = basic_catalog([make_item("great-top", "top", vibes=("cozy",), formality=2)])

    outfit = generate(catalog, ParsedRequest(vibes=["cozy"], formality=2), constraints=Constraints(locked={"top": "top-1"}))

    assert outfit.pieces["top"].item_id == "top-1"


def test_locking_a_top_rules_out_a_dress_and_vice_versa():
    catalog = basic_catalog()

    with_top = generate(catalog, ParsedRequest(), constraints=Constraints(locked={"top": "top-1"}))
    with_dress = generate(catalog, ParsedRequest(), constraints=Constraints(locked={"dress": "dress-1"}))

    assert "dress" not in with_top.pieces
    assert with_dress.pieces["dress"].item_id == "dress-1" and not with_dress.pieces.keys() & {"top", "bottom"}


@pytest.mark.parametrize("locked,error", [
    ({"top": "nope"}, "not in the catalog"),
    ({"bottom": "top-1"}, "not a bottom"),
    ({"dress": "dress-1", "top": "top-1"}, "can't be locked together"),
])
def test_impossible_locks_are_rejected(locked, error):
    with pytest.raises(ValueError, match=error):
        generate(basic_catalog(), ParsedRequest(), constraints=Constraints(locked=locked))


def test_rejected_product_never_appears_in_any_colorway():  # FR-FB-2 relies on this
    black = make_item("pant-black", "bottom", name="Aven Pant", vibes=("cozy",), formality=2)
    grey = make_item("pant-grey", "bottom", name="Aven Pant | Aritzia US", vibes=("cozy",), formality=2, colors=("grey",))
    catalog = basic_catalog([black, grey])
    request = ParsedRequest(vibes=["cozy"], formality=2)
    assert generate(catalog, request).pieces["bottom"] in (black, grey)

    outfit = generate(catalog, request, constraints=Constraints(rejected={black.product_key}))

    assert outfit.pieces.get("bottom") not in (black, grey)


def test_rejecting_the_best_piece_never_raises_the_score():
    """Relevance is scaled over the whole slot, so dropping the top item doesn't promote the
    runner-up to a perfect 1.0 and make the swapped outfit look better than the original."""
    catalog = basic_catalog([make_item(f"shoes-{n}", "shoes") for n in range(3, 8)])
    request, query = ParsedRequest(text="x"), vector("query")
    first = generate(catalog, request, query)

    second = generate(catalog, request, query, Constraints(rejected={first.pieces["shoes"].product_key}))

    assert second.score <= first.score


def test_cold_or_rainy_weather_requires_outerwear():  # FR-REQ-4
    catalog = basic_catalog()
    for weather in (["cold"], ["rainy"], ["cool"]):
        assert "outerwear" in generate(catalog, ParsedRequest(weather=weather)).pieces


def test_outerwear_is_left_out_when_it_lowers_the_score():
    catalog = basic_catalog([make_item("parka", "outerwear", vibes=("athleisure",), formality=1, colors=("red",))])
    catalog = Catalog([i for i in catalog.items.values() if i.slot != "outerwear" or i.item_id == "parka"])

    outfit = generate(catalog, ParsedRequest(vibes=["minimal"], formality=3))

    assert "outerwear" not in outfit.pieces


def test_season_filters_outerwear_but_not_other_slots():
    summer_coat = make_item("summer-coat", "outerwear", seasons=("summer",), vibes=("cozy",), formality=2)
    summer_top = make_item("summer-top", "top", seasons=("summer",), vibes=("cozy",), formality=2)
    catalog = basic_catalog([summer_coat, summer_top])

    outfit = generate(catalog, ParsedRequest(vibes=["cozy"], formality=2, season="winter", weather=["cold"]))

    assert outfit.pieces["outerwear"] is not summer_coat
    assert outfit.pieces["top"] is summer_top  # season is only a soft term outside outerwear


def test_at_most_one_print_per_outfit():
    prints = [make_item(f"print-{slot}", slot, colors=("multi",), vibes=("romantic",)) for slot in ("top", "bottom", "shoes")]
    catalog = Catalog(prints + [make_item("plain-top", "top"), make_item("plain-bottom", "bottom"),
                                make_item("plain-shoes", "shoes")])

    outfit = generate(catalog, ParsedRequest(vibes=["romantic"]))

    assert sum(i.main_color == "multi" for i in outfit.pieces.values()) == 1


def test_the_last_outfit_is_not_repeated():  # FR-GEN-5
    catalog = basic_catalog()
    first = generate(catalog, ParsedRequest())

    second = generate(catalog, ParsedRequest(), constraints=Constraints(avoid={first.item_ids}))

    assert second.item_ids != first.item_ids


def test_nothing_left_raises_a_clear_error():
    catalog = basic_catalog()
    all_shoes = {i.product_key for i in catalog.in_slot("shoes")}

    with pytest.raises(NoOutfitError, match="no shoes"):
        generate(catalog, ParsedRequest(), constraints=Constraints(rejected=all_shoes))


def test_total_price_adds_up_the_pieces():
    outfit = generate(basic_catalog(), ParsedRequest())
    assert outfit.total_price == round(sum(i.price for i in outfit.pieces.values()), 2)
