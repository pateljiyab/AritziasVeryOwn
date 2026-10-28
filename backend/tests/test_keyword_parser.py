"""Keyword parsing: the PRD's Flows A-D, feedback phrases, and vague requests (FR-REQ)."""

import pytest

from stylist.core.keyword_parser import parse
from stylist.core.models import FeedbackIntent, ParsedRequest


def test_flow_a_mood_request():
    r = parse("cozy fall Sunday, running errands", has_outfit=False)
    assert r.vibes == ["cozy", "off-duty"] and r.season == "fall" and r.formality == 2


def test_flow_b_parses_as_the_prd_describes():
    """PRD §4 Flow B: occasion dinner, vibe going-out, weather cool, formality 3, with outerwear."""
    r = parse("rooftop birthday dinner, it might get chilly", has_outfit=False)
    assert r.vibes == ["going-out"] and r.formality == 3 and r.weather == ["cool"]
    assert r.needs_outerwear


def test_flow_c_named_item():
    r = parse("build something around the Effortless Pant", has_outfit=False)
    assert r.named_items == ["Effortless Pant"]


@pytest.mark.parametrize("text,names", [
    ("brunch with my Only Coat", ["Only Coat"]),
    ("dinner with the girls", []),             # "with the ..." needs a garment word
    ("what style for a wedding", []),          # not a product name
    ("style the Elowen coat for work", ["Elowen coat"]),
])
def test_named_items_only_when_a_product_is_named(text, names):
    assert parse(text, has_outfit=False).named_items == names


@pytest.mark.parametrize("text,intent", [
    ("keep the coat", {"action": "lock", "slot": "outerwear"}),
    ("love those sneakers", {"action": "lock", "slot": "shoes"}),
    ("unlock the shoes", {"action": "unlock", "slot": "shoes"}),
    ("not that top", {"action": "reject", "slot": "top"}),
    ("not a fan of the bag", {"action": "reject", "slot": "accessory"}),
    ("different shoes", {"action": "swap", "slot": "shoes"}),
    ("swap the jeans", {"action": "swap", "slot": "bottom"}),
    ("dressier", {"action": "nudge", "formality": 1}),
    ("dress it up a bit", {"action": "nudge", "formality": 1}),
    ("more relaxed please", {"action": "nudge", "formality": -1}),
    ("make it edgier", {"action": "nudge", "vibe": "edgy"}),
    ("more sporty", {"action": "nudge", "vibe": "athleisure"}),
    ("perfect, I'll take it", {"action": "accept"}),
])
def test_flow_d_feedback_phrases(text, intent):
    assert parse(text, has_outfit=True) == FeedbackIntent(**intent)


def test_feedback_words_are_a_new_request_when_there_is_no_outfit():
    assert isinstance(parse("keep the coat", has_outfit=False), ParsedRequest)


def test_a_new_request_mid_session_is_not_feedback():
    r = parse("now something for a wedding", has_outfit=True)
    assert isinstance(r, ParsedRequest) and r.formality == 5


def test_wanting_an_item_is_not_locking_it():
    assert isinstance(parse("I'd like a jacket", has_outfit=True), ParsedRequest)


def test_vague_request_still_parses_with_defaults():  # FR-REQ-3
    r = parse("something nice", has_outfit=False)
    assert r == ParsedRequest(text="something nice")


def test_explicit_style_word_beats_the_occasion():
    assert parse("casual dinner", has_outfit=False).formality == 2
    assert parse("work dinner", has_outfit=False).formality == 4


@pytest.mark.parametrize("text,weather", [
    ("it's freezing out", ["cold"]),
    ("rainy commute", ["rainy"]),
    ("hot summer day", ["hot"]),
    ("something cool for a party", []),  # slang, not weather
])
def test_weather(text, weather):
    assert parse(text, has_outfit=False).weather == weather
