"""Score the name resolver on the hand-written queries in eval/names_test.csv (PRD G4).

Usage (from the repo root, after ingest.py has run):
    python backend/src/stylist/jobs/names_report.py
    python backend/src/stylist/jobs/names_report.py --make-template   # draw the 30 products (done once)

Each row names a catalog product and a query a person typed for it. A query is a hit when the
resolver's best match is that product (any colorway). Target: at least 90% top-1.

These queries are not a clean held-out test: the resolver was revised after seeing which of
them failed (plurals, split/joined words, query-weighted scoring). The score before that
revision is kept in BASELINE and printed with every result.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

# Run as a plain script: make `import stylist...` work from backend/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stylist.adapters.catalog_store import load_catalog  # noqa: E402
from stylist.core.catalog import Catalog  # noqa: E402
from stylist.core.names import representatives, resolve  # noqa: E402
from stylist.jobs.ingest import ROOT  # noqa: E402

NAMES_CSV = ROOT / "eval" / "names_test.csv"
TARGET = 0.9
SAMPLE_SIZE = 30
SEED = 42
BASELINE = "17/30 (57%) with the first resolver, before it was revised using these queries"


def make_template(catalog: Catalog, path: Path = NAMES_CSV) -> None:
    """30 products drawn at random (one listing each), with an empty query column to fill in."""
    picked = random.Random(SEED).sample(representatives(catalog), SAMPLE_SIZE)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["item_id", "slot", "name", "query"])
        writer.writeheader()
        writer.writerows({"item_id": i.item_id, "slot": i.slot, "name": i.name, "query": ""} for i in picked)


def score(catalog: Catalog, rows: list[dict]) -> tuple[int, int, list[str]]:
    """(hits, queries scored, a line per miss). Rows with an empty query are skipped."""
    hits, total, misses = 0, 0, []
    for row in rows:
        query = row["query"].strip()
        expected = catalog.get(row["item_id"])
        if not query or expected is None:
            continue
        total += 1
        found = resolve(catalog, query)
        if found.item is not None and found.item.product_key == expected.product_key:
            hits += 1
        else:
            offered = found.ambiguous and expected.product_key in {c.product_key for c in found.choices}
            got = found.item.name if found.item else "no match"
            misses.append(f"  {query!r}: wanted {expected.name}, got {got}"
                          + ("  (offered as one of the choices)" if offered else ""))
    return hits, total, misses


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--make-template", action="store_true", help=f"write {NAMES_CSV.name} with blank queries")
    args = parser.parse_args()

    catalog = load_catalog()
    if args.make_template:
        if NAMES_CSV.exists():
            raise SystemExit(f"{NAMES_CSV} already exists; delete it first to draw again.")
        make_template(catalog)
        print(f"Wrote {SAMPLE_SIZE} products to {NAMES_CSV}. Fill in the query column, then run this again.")
        return

    with NAMES_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    hits, total, misses = score(catalog, rows)
    if not total:
        raise SystemExit(f"No queries yet: fill in the query column of {NAMES_CSV}.")
    print(f"Top-1: {hits}/{total} ({hits / total:.0%}), target {TARGET:.0%}"
          f"{'' if total == len(rows) else f'  ({len(rows) - total} rows still blank)'}")
    print(f"Not a held-out score: the resolver was revised using these queries. Baseline: {BASELINE}.")
    for line in misses:
        print(line)


if __name__ == "__main__":
    main()
