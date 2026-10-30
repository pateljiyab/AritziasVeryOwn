"""Swapping through every option, and colors for one piece ("black shoes"): asked for in a request
or in a swap, kept for later outfits, and explained when the catalog doesn't have them."""

from typing import get_args

import pytest

from factories import make_item, vector
from stylist.core import keyword_parser
from stylist.core.catalog import Catalog
from stylist.core.feedback import FeedbackError, apply_feedback, start
from stylist.core.models import Color, FeedbackIntent, ParsedRequest
from stylist.core.session import handle_message
from stylist.tagging.colors import COLOR_WORDS

SLOTS = ("top", "bottom", "dress", "outerwear", "shoes", "accessory")
SHOE_COLORS = ["black", "black", "brown", "beige", "navy", "grey"]


def color_catalog() -> Catalog:
    """Two of every slot, plus six pairs of shoes: two black, one each of four other colors."""
    items = [make_item(f"{slot}-{n}", slot, colors=("cream",)) for slot in SLOTS if slot != "shoes" for n in (1, 2)]
    items += [make_item(f"shoes-{color}-{n}", "shoes", colors=(color,)) for n, color in enumerate(SHOE_COLORS)]
    return Catalog(items)


def act(catalog, state, **intent):
    return apply_feedback(catalog, state, FeedbackIntent(**intent))


def shoes(outfit):
    return outfit.pieces["shoes"]


def test_color_tags_match_the_tagger():
    assert set(get_args(Color)) == set(COLOR_WORDS)


# ------------------------------------------------------------- swapping

def test_swap_goes_through_every_option_before_repeating():
    """The reported bug: swapping went back and forth between two pairs."""
    catalog = color_catalog()
    state, outfit = start(catalog, ParsedRequest())
    seen = [shoes(outfit).item_id]

    for _ in range(len(SHOE_COLORS) - 1):
        state, outfit = act(catalog, state, action="swap", slot="shoes")
        seen.append(shoes(outfit).item_id)

    assert len(set(seen)) == len(SHOE_COLORS)  # every pair once


def test_swap_starts_over_once_every_option_was_shown():
    catalog = color_catalog()
    state, outfit = start(catalog, ParsedRequest())
    for _ in range(len(SHOE_COLORS) + 2):
        previous = shoes(outfit).item_id
        state, outfit = act(catalog, state, action="swap", slot="shoes")
        assert shoes(outfit).item_id != previous  # never the same pair twice in a row


# ------------------------------------------------------------- colors in a swap

def test_swap_with_a_color_picks_that_color_and_keeps_to_it():
    catalog = color_catalog()
    state, _ = start(catalog, ParsedRequest())

    state, outfit = act(catalog, state, action="swap", slot="shoes", color="black")
    first = shoes(outfit)
    assert first.colors == ("black",) and state.request.colors == {"shoes": "black"}

    state, outfit = act(catalog, state, action="swap", slot="shoes")  # the other black pair
    assert shoes(outfit).colors == ("black",) and shoes(outfit).item_id != first.item_id
    state, outfit = act(catalog, state, action="nudge", formality=1)
    assert shoes(outfit).colors == ("black",)


def test_the_only_pair_in_a_color_says_so():
    catalog = color_catalog()
    state, outfit = start(catalog, ParsedRequest())
    state, outfit = act(catalog, state, action="swap", slot="shoes", color="navy")
    assert shoes(outfit).colors == ("navy",)

    with pytest.raises(FeedbackError, match="the only navy pair of shoes I have"):
        act(catalog, state, action="swap", slot="shoes")


def test_a_color_the_catalog_doesnt_have_names_the_ones_it_does():
    catalog = color_catalog()
    state, _ = start(catalog, ParsedRequest())

    with pytest.raises(FeedbackError, match=r"I don't have any pink shoes; the ones I have come in black, "):
        act(catalog, state, action="swap", slot="shoes", color="pink")


def test_any_color_drops_the_color():
    catalog = color_catalog()
    state, _ = start(catalog, ParsedRequest())
    state, _ = act(catalog, state, action="swap", slot="shoes", color="navy")

    state, outfit = act(catalog, state, action="swap", slot="shoes", color="any")

    assert state.request.colors == {} and shoes(outfit).colors != ("navy",)


def test_a_color_for_a_slot_the_outfit_doesnt_have_adds_that_piece():
    """'A black dress instead' while wearing a top and bottom."""
    catalog = Catalog([*color_catalog().items.values(), make_item("dress-black", "dress", colors=("black",))])
    state, outfit = start(catalog, ParsedRequest(), locked={"top": "top-1"})
    state, _ = act(catalog, state, action="unlock", slot="top")
    assert "dress" not in outfit.pieces

    state, outfit = act(catalog, state, action="swap", slot="dress", color="black")

    assert outfit.pieces["dress"].item_id == "dress-black"
    assert not outfit.pieces.keys() & {"top", "bottom"} and "shoes" in outfit.pieces


# ------------------------------------------------------------- chat turns

def embed(text):
    return vector(text) if text else None


def say(catalog, state, text):
    return handle_message(catalog, state, text, embed, keyword_parser.parse)


def test_asking_for_black_shoes_after_the_first_outfit():
    catalog = color_catalog()
    first = say(catalog, None, "rooftop birthday dinner")

    reply = say(catalog, first.state, "can i get black shoes?")

    assert reply.parsed == FeedbackIntent(action="swap", slot="shoes", color="black")
    assert shoes(reply.outfit).colors == ("black",)
    assert {s: i.item_id for s, i in reply.outfit.pieces.items() if s != "shoes"} == \
           {s: i.item_id for s, i in first.outfit.pieces.items() if s != "shoes"}  # the rest stays


def test_a_color_in_the_first_message():
    reply = say(color_catalog(), None, "date night, i want navy shoes")

    assert shoes(reply.outfit).colors == ("navy",) and reply.state.request.colors == {"shoes": "navy"}


def test_a_color_the_catalog_doesnt_have_in_a_request_is_left_open():
    reply = say(color_catalog(), None, "date night, i want pink shoes")

    assert reply.outfit is not None and reply.state.request.colors == {}
    assert reply.notes == ["I don't have any pink shoes, so I left the color open."]
