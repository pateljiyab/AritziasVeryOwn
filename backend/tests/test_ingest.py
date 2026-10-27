import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest
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


@pytest.mark.parametrize("invalid_count", [3, 2])  # all rows invalid; most rows invalid
def test_bad_csv_does_not_wipe_existing_items(tmp_path, invalid_count):
    """A broken CSV (e.g. a renamed column) must not delete the catalog."""
    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    rows = seed_rows()[:3]
    ingest(write_csv(tmp_path / "good.csv", rows), db)

    for row in rows[:invalid_count]:
        row["slot"] = "hat"
    total, skipped = ingest(write_csv(tmp_path / "bad.csv", rows), db)

    assert skipped == invalid_count
    assert total == 3


def test_cli_reports_skipped_rows_when_output_is_not_utf8(tmp_path):
    """Product names can contain characters like a non-breaking hyphen (U+2011), which
    Windows' default encoding for redirected output (cp1252) can't print."""
    good, bad = seed_rows()[:2]
    bad["slot"] = "hat"
    bad["name"] = "Hi\u2011rise Pant"
    csv_path = write_csv(tmp_path / "catalog.csv", [good, bad])
    script = Path(__file__).resolve().parents[1] / "src" / "stylist" / "jobs" / "ingest.py"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}

    result = subprocess.run(
        [sys.executable, str(script), "--csv", str(csv_path), "--db", f"sqlite:///{tmp_path / 'catalog.db'}"],
        capture_output=True, env=env,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "Hi\u2011rise Pant" in result.stdout.decode("utf-8")


def test_pruned_items_take_their_tags_with_them(tmp_path):
    """Review #6: removing an item from the CSV must not leave orphan rows in item_tags."""
    from datetime import UTC, datetime

    from stylist.jobs.ingest import ItemTag

    db = f"sqlite:///{tmp_path / 'catalog.db'}"
    rows = seed_rows()[:3]
    ingest(write_csv(tmp_path / "v1.csv", rows), db)
    with Session(create_engine(db)) as session, session.begin():
        for row in rows:
            session.add(ItemTag(item_id=row["item_id"], source="auto", seasons=["fall"], vibes=["cozy"],
                                colors=["black"], formality=2, color_source="hex", probabilities={},
                                model_version="test", tagged_at=datetime.now(UTC)))

    ingest(write_csv(tmp_path / "v2.csv", rows[:2]), db)

    with Session(create_engine(db)) as session:
        tagged = set(session.scalars(select(ItemTag.item_id)))
    assert tagged == {rows[0]["item_id"], rows[1]["item_id"]}
