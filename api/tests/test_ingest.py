import csv
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stylist.jobs.ingest import CATALOG_CSV, Item, ingest


def write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return path


def seed_rows() -> list[dict]:
    with CATALOG_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_seed_catalog_loads_cleanly(tmp_path):
    total, skipped = ingest(CATALOG_CSV, f"sqlite:///{tmp_path / 'catalog.db'}")
    assert skipped == 0
    assert total == len(seed_rows())


def test_ingesting_twice_creates_no_duplicates(tmp_path):
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    first, _ = ingest(CATALOG_CSV, db)
    second, _ = ingest(CATALOG_CSV, db)
    assert first == second == len(seed_rows())


def test_invalid_rows_are_skipped_not_loaded(tmp_path):
    good, bad_slot, no_price, dup = seed_rows()[:4]
    bad_slot["slot"] = "hat"
    no_price["price"] = ""
    dup["item_id"] = good["item_id"]
    path = write_csv(tmp_path / "catalog.csv", [good, bad_slot, no_price, dup])

    total, skipped = ingest(path, f"sqlite:///{tmp_path / 'catalog.db'}")
    assert (total, skipped) == (1, 3)


def test_reingest_updates_changed_rows_and_drops_removed_ones(tmp_path):
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    rows = seed_rows()[:3]
    ingest(write_csv(tmp_path / "v1.csv", rows), db)

    rows[0]["price"] = "1.50"
    total, _ = ingest(write_csv(tmp_path / "v2.csv", rows[:2]), db)

    with Session(create_engine(db)) as session:
        prices = dict(session.execute(select(Item.item_id, Item.price)).all())
    assert total == 2
    assert prices[rows[0]["item_id"]] == 1.50
    assert rows[2]["item_id"] not in prices
