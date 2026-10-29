"""The parser benchmark's scoring rules, and the labels in eval/parser_test.csv."""

from typing import get_args

import pytest

from stylist.core.models import FeedbackIntent, ParsedRequest, Season, Slot, Vibe, Weather
from stylist.jobs import parser_report
from stylist.jobs.parser_report import alternatives, as_list, same_piece, score_row

BLANK = {"text": "", "live": "0", "in_scope": "1", "kind": "request", "action": "", "slot": "", "vibes": "",
         "formality": "", "season": "", "weather": "", "named_items": "", "notes": ""}


def label(**fields) -> dict:
    return {**BLANK, **fields}


def test_vibe_counts_when_the_first_predicted_vibe_is_labeled():
    row = label(vibes="going-out;romantic")

    assert score_row(row, ParsedRequest(vibes=["romantic", "edgy"]))["vibe"]
    assert not score_row(row, ParsedRequest(vibes=["edgy", "romantic"]))["vibe"]
    assert not score_row(row, ParsedRequest())["vibe"]
    assert score_row(label(), ParsedRequest())["vibe"]  # nothing labeled, nothing predicted


def test_formality_exact_and_within_one():
    row = label(formality="4")

    fields = score_row(row, ParsedRequest(formality=3))
    assert not fields["formality"] and fields["formality ±1"]
    assert not score_row(label(), ParsedRequest(formality=3))["formality ±1"]  # no hint means null


def test_alternatives_all_count():
    assert score_row(label(weather="cool|cold"), ParsedRequest(weather=["cold"]))["weather"]
    row = label(kind="feedback", action="swap|reject", slot="bottom")
    assert score_row(row, FeedbackIntent(action="reject", slot="bottom")) == {"kind": True, "action": True, "slot": True}


@pytest.mark.parametrize("gold,predicted,same", [
    ("agency pants", "Agency Pant", True),
    ("long trench coat", "trench coats", True),     # one word missing is allowed
    ("satin midi dress", "those aritzia satin midi dresses", True),
    ("long white button up dress", "dress", False),
    ("linen dress", "linen pants", False),
])
def test_named_items_ignore_case_plurals_and_filler(gold, predicted, same):
    assert same_piece(gold, predicted) is same


def test_wrong_kind_makes_every_field_wrong():
    fields = score_row(label(kind="feedback", action="nudge", formality="1"), ParsedRequest(formality=4))

    assert fields == {"kind": False, "action": False, "nudge": False}


def test_nudge_is_scored_on_the_labeled_direction_or_vibe():
    row = label(kind="feedback", action="nudge", vibes="romantic|going-out")

    assert score_row(row, FeedbackIntent(action="nudge", vibe="going-out"))["nudge"]
    assert not score_row(row, FeedbackIntent(action="nudge", formality=1))["nudge"]


def test_committed_labels_use_the_system_vocabulary():
    vocab = {"vibes": set(get_args(Vibe)), "season": set(get_args(Season)), "weather": set(get_args(Weather)),
             "slot": set(get_args(Slot)), "action": set(get_args(FeedbackIntent.model_fields["action"].annotation))}
    rows = parser_report.read_rows()

    assert len(rows) == 40 and all(r["live"] in {"0", "1"} and r["in_scope"] in {"0", "1"} for r in rows)
    for r in rows:
        assert r["kind"] in {"request", "feedback"}, r["text"]
        for column, allowed in vocab.items():
            values = {v for alt in alternatives(r[column]) for v in as_list(alt)}
            assert values <= allowed, (r["text"], column, values - allowed)
        assert all(a == "" or int(a) in {-1, 1, 2, 3, 4, 5} for a in alternatives(r["formality"])), r["text"]
