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


# ------------------------------------------------------------- budgets (FR-REQ-5, FR-FB-8, -9)

@pytest.mark.parametrize("text,max_total,max_item", [
    ("rooftop dinner under $200", 200, None),
    ("cozy fall Sunday running errands, under $150 total", 150, None),
    ("brunch outfit, my budget is 250 dollars", 250, None),
    ("i have $180 to spend on a date night fit", 180, None),
    ("office outfit, nothing over $100", None, 100),
    ("date night, $60 each max", None, 60),
    ("wedding guest under $300 total but nothing over $100 each", 300, 100),
    ("party look, keep it under 1,000 bucks", 1000, None),
    ("I got a $50 gift card, brunch outfit", None, None),  # a price, not a limit
    ("hiking when it's under 20 degrees", None, None),     # a temperature
])
def test_budget_in_a_request(text, max_total, max_item):
    r = parse(text, has_outfit=False)
    assert (r.max_total, r.max_item) == (max_total, max_item)


@pytest.mark.parametrize("text,intent", [
    ("keep the price under $200", {"action": "budget", "max_total": 200}),
    ("keep it under 200", {"action": "budget", "max_total": 200}),
    ("nothing over $80 please", {"action": "budget", "max_item": 80}),
    ("actually price doesn't matter", {"action": "budget"}),
    ("can you make it cheaper?", {"action": "cheaper"}),
    ("this is too expensive", {"action": "cheaper"}),
    ("cheaper coat please", {"action": "cheaper", "slot": "outerwear"}),
    ("swap the coat for something cheaper", {"action": "cheaper", "slot": "outerwear"}),
    ("the shoes are too pricey", {"action": "cheaper", "slot": "shoes"}),
])
def test_budget_feedback(text, intent):
    assert parse(text, has_outfit=True) == FeedbackIntent(**intent)


def test_budget_with_a_new_occasion_is_a_new_request():
    r = parse("actually a wedding instead, under $300", has_outfit=True)
    assert isinstance(r, ParsedRequest) and r.formality == 5 and r.max_total == 300


def test_too_much_isnt_about_price():
    assert parse("too much going on, make it simpler", has_outfit=True) == FeedbackIntent(action="nudge", vibe="minimal")


# ------------------------------------------------------------- colors for one piece

@pytest.mark.parametrize("text,intent", [
    ("black shoes", {"action": "swap", "slot": "shoes", "color": "black"}),
    ("can i get black shoes?", {"action": "swap", "slot": "shoes", "color": "black"}),
    ("do you have the shoes in black", {"action": "swap", "slot": "shoes", "color": "black"}),
    ("swap the shoes for black ones", {"action": "swap", "slot": "shoes", "color": "black"}),
    ("black leather boots please", {"action": "swap", "slot": "shoes", "color": "black"}),
    ("a tan coat instead", {"action": "swap", "slot": "outerwear", "color": "camel"}),
    ("any color shoes", {"action": "swap", "slot": "shoes", "color": "any"}),
    ("different shoes", {"action": "swap", "slot": "shoes"}),
    ("keep the black shoes", {"action": "lock", "slot": "shoes"}),  # a color before the piece doesn't make it a swap
    ("not the white top", {"action": "reject", "slot": "top"}),
])
def test_piece_colors_as_feedback(text, intent):
    assert parse(text, has_outfit=True) == FeedbackIntent(**intent)


def test_piece_colors_in_a_request():
    r = parse("dinner date, i want black shoes and a navy coat", has_outfit=False)
    assert r.colors == {"shoes": "black", "outerwear": "navy"}


def test_a_color_with_a_new_occasion_is_a_new_request():
    r = parse("black shoes for a wedding instead", has_outfit=True)
    assert isinstance(r, ParsedRequest) and r.formality == 5 and r.colors == {"shoes": "black"}
