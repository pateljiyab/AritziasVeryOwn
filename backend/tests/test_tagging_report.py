"""The tagging report: gold-set parsing, the metric definitions, and the report end to end."""

import csv
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from stylist.jobs import tagging_report as report
from stylist.jobs.ingest import CATALOG_CSV, SLOTS, ItemTag, ingest
from stylist.jobs.tagging_report import Tags, item_results, read_gold


def test_committed_gold_set_is_valid_and_stratified():
    gold = read_gold()

    assert len(gold) == 30
    assert sorted(g.slot for g in gold) == sorted(SLOTS * 5)
    assert len({g.item_id for g in gold}) == 30


@pytest.mark.parametrize("column,value,error", [
    ("vibe", "posh", "bad vibe"),
    ("season", "fall;fall", "bad season"),
    ("color", "", "bad color"),
    ("formality", "6", "bad formality"),
])
def test_gold_label_outside_the_taxonomy_is_rejected(tmp_path, column, value, error):
    with report.GOLD_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows[0][column] = value
    bad = tmp_path / "gold.csv"
    with bad.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ValueError, match=error):
        read_gold(bad)


def test_top1_counts_any_gold_tag_but_first_tag_needs_the_gold_first_tag():
    gold = Tags(seasons=["fall", "winter"], vibes=["polished", "minimal"], colors=["black"], formality=4)
    pred = Tags(seasons=["winter"], vibes=["minimal", "polished"], colors=["navy"], formality=3)

    r = item_results(gold, pred)

    assert r["season_top1"] and not r["season_first"]
    assert r["vibe_top1"] and not r["vibe_first"]
    assert r["formality_within_1"] and not r["formality_exact"]
    assert not r["color_main"]


def test_untagged_item_misses_every_metric():
    gold = Tags(["fall"], ["cozy"], ["grey"], 1)

    assert not any(item_results(gold, None).values())  # no tags row at all
    no_photo = item_results(gold, Tags([], [], ["grey"], None))  # color only, no CLIP tags
    assert [k for k, hit in no_photo.items() if hit] == ["color_main"]


def write_tags(db: str, tags_for) -> None:
    rows = [ItemTag(item_id=g.item_id, source="auto", seasons=t.seasons, vibes=t.vibes, colors=t.colors,
                    formality=t.formality, color_source="hex", probabilities={}, model_version="test",
                    tagged_at=datetime.now(UTC))
            for g in read_gold() if (t := tags_for(g)) is not None]
    with Session(create_engine(db)) as session, session.begin():
        session.add_all(rows)


def test_perfect_tagger_meets_every_target(tmp_path):
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    ingest(CATALOG_CSV, db)
    write_tags(db, lambda g: g.tags)

    predictions, versions = report.read_predictions(db)
    text = report.build_report(read_gold(), predictions, versions)

    assert versions == {"test"}
    assert "**no**" not in text
    assert text.count("30/30 (100%)") == 7


def test_missing_tags_count_as_misses_and_are_named(tmp_path):
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    ingest(CATALOG_CSV, db)
    skipped = read_gold()[0]
    write_tags(db, lambda g: None if g.item_id == skipped.item_id else g.tags)

    predictions, versions = report.read_predictions(db)
    text = report.build_report(read_gold(), predictions, versions)

    assert "29/30 (97%)" in text
    assert skipped.name in text.split("## By slot")[0]


def test_llm_vibes_get_a_comparison_table(tmp_path):  # FR-TAG-5
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    ingest(CATALOG_CSV, db)
    wrong = lambda g: [next(v for v in report.VIBE_PHRASES if v not in g.tags.vibes)]  # noqa: E731
    write_tags(db, lambda g: Tags(g.tags.seasons, wrong(g), g.tags.colors, g.tags.formality))  # CLIP always misses vibe
    clip, versions = report.read_predictions(db)
    llm = {g.item_id: Tags([], g.tags.vibes, [], None) for g in read_gold()}  # LLM always right

    text = report.build_report(read_gold(), clip, versions, llm, {"llm-test"})

    table = text.split("## Vibe: CLIP vs. LLM")[1].split("## First vibe")[0]
    assert "| Vibe top-1 (target ≥ 75%) | 0/30 (0%) | 30/30 (100%) |" in table
    assert "agree on the first vibe (0 items)" in table
    assert "LLM vibe |" in text.split("## Every item")[1]


def test_report_without_llm_vibes_has_no_comparison(tmp_path):
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    ingest(CATALOG_CSV, db)
    write_tags(db, lambda g: g.tags)
    clip, versions = report.read_predictions(db)

    assert "CLIP vs. LLM" not in report.build_report(read_gold(), clip, versions)
