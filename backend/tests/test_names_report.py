"""The G4 name-resolution score over eval/names_test.csv."""

import csv

from factories import make_item
from stylist.core.catalog import Catalog
from stylist.jobs import names_report


def test_hits_count_any_colorway_and_blank_queries_are_skipped():
    catalog = Catalog([make_item("a", "bottom", name="The Effortless Pant"),
                       make_item("a2", "bottom", name="The Effortless Pant™"),  # same product, other colorway
                       make_item("b", "outerwear", name="The Only Coat")])
    rows = [{"item_id": "a2", "query": "effortless pant"},
            {"item_id": "b", "query": "zebra cape"},
            {"item_id": "b", "query": ""}]

    hits, total, misses = names_report.score(catalog, rows)

    assert (hits, total) == (1, 2)
    assert "zebra cape" in misses[0]


def test_committed_template_names_real_products():
    with names_report.NAMES_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == names_report.SAMPLE_SIZE
    assert len({r["item_id"] for r in rows}) == len(rows)
    assert all(r["name"] and r["slot"] for r in rows)
