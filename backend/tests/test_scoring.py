"""Score terms: color harmony (PRD Appendix A), vibe, formality, season, and relevance."""

import numpy as np
import pytest

from factories import make_item, vector
from stylist.core import scoring
from stylist.core.models import ParsedRequest


@pytest.mark.parametrize("a,b,expected", [
    ("black", "red", 1.0),        # neutral pairs with anything
    ("navy", "multi", 1.0),       # even with a print
    ("red", "multi", 0.6),        # print vs. a color
    ("red", "burgundy", 0.9),     # 15° apart, wrapping past 360
    ("orange", "yellow", 0.9),    # 25° apart
    ("blue", "orange", 0.7),      # 170°: complementary
    ("green", "blue", 0.3),       # 85°: in between
])
def test_color_pair(a, b, expected):
    assert scoring.color_pair(a, b) == expected


def test_all_neutral_outfit_is_fully_harmonious():
    items = [make_item(str(n), "top", colors=(c,)) for n, c in enumerate(["black", "cream", "denim"])]
    assert scoring.color_harmony(items) == 1.0


def test_vibe_match_prefers_the_first_tag():
    item = make_item("a", "top", vibes=("cozy", "minimal"))
    assert scoring.vibe_match(item, ["cozy"]) == 1.0
    assert scoring.vibe_match(item, ["minimal"]) == 0.6
    assert scoring.vibe_match(item, ["edgy"]) == 0.0


def test_formality_match_falls_off_with_distance():
    assert [scoring.formality_match(make_item("a", "top", formality=f), 3) for f in (3, 4, 1)] == [1.0, 0.5, 0.0]


def test_untagged_item_scores_unknown_instead_of_zero():
    item = make_item("a", "top", vibes=(), seasons=(), formality=None)
    assert scoring.vibe_match(item, ["cozy"]) == scoring.UNKNOWN
    assert scoring.season_match(item, "fall") == scoring.UNKNOWN
    assert scoring.formality_match(item, 3) == scoring.UNKNOWN


def test_terms_the_request_doesnt_mention_are_left_out():
    terms = scoring.item_terms(make_item("a", "top"), ParsedRequest(), relevance=None, formality=None)
    assert terms == {}

    terms = scoring.item_terms(make_item("a", "top"), ParsedRequest(vibes=["cozy"], season="fall"), 0.4, 3)
    assert set(terms) == {"relevance", "vibe", "formality", "season"}


def test_relevance_is_scaled_to_the_full_range():
    items = [make_item(str(n), "top") for n in range(4)]
    query = items[2].embedding

    scores = scoring.relevance_scores(items, query)

    assert scores["2"] == 1.0 and min(scores.values()) == 0.0
    assert scoring.relevance_scores(items, None) == {}


def test_item_without_embedding_gets_unknown_relevance():
    items = [make_item("a", "top"), make_item("b", "top", embedding=False)]
    assert scoring.relevance_scores(items, vector("q"))["b"] == scoring.UNKNOWN


def test_weighted_renormalizes_over_the_terms_present():
    assert scoring.weighted({"vibe": 1.0}) == 1.0
    assert scoring.weighted({"vibe": 1.0, "formality": 0.0}) == pytest.approx(0.25 / 0.45)
    assert scoring.weighted({}) == 0.0
    assert np.isclose(sum(scoring.WEIGHTS.values()), 1.0)
