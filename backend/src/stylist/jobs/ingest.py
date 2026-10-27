"""Load data/seed_catalog.csv into the SQLite catalog (data/catalog.db).

Usage (from the repo root):
    py backend/src/stylist/jobs/ingest.py

Every row is validated first. Rows that fail are printed with the reason and skipped.
Re-running is safe: rows are upserted on item_id, and items no longer in the CSV are
removed, so the table always mirrors the CSV. Columns added later (tags) are left alone.
Removal is skipped when most of the CSV failed validation, so a broken file can't
wipe the catalog.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, ValidationError, field_validator
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, create_engine, delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

ROOT = Path(__file__).resolve().parents[4]
CATALOG_CSV = ROOT / "data" / "seed_catalog.csv"
DB_URL = f"sqlite:///{ROOT / 'data' / 'catalog.db'}"

SLOTS = ["top", "bottom", "dress", "outerwear", "shoes", "accessory"]

# Only remove items missing from the CSV if at most this share of its rows were skipped.
# More than that means the file itself is broken, not that the catalog changed.
MAX_SKIPPED_SHARE_TO_PRUNE = 0.5
Slot = Literal["top", "bottom", "dress", "outerwear", "shoes", "accessory"]


# ------------------------------------------------------------------ schema

class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (CheckConstraint(f"slot IN ({', '.join(repr(s) for s in SLOTS)})", name="valid_slot"),)

    item_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str]
    brand_line: Mapped[str]
    slot: Mapped[str] = mapped_column(index=True)
    color_hex: Mapped[str | None]
    color_name: Mapped[str | None]  # empty: Channel3 has no color names; color tags live in item_tags
    price: Mapped[float]
    currency: Mapped[str]
    fabric: Mapped[str | None]
    description: Mapped[str]
    image_url: Mapped[str]
    product_url: Mapped[str]
    image_alt_text: Mapped[str | None]
    category_path: Mapped[str | None]
    gender: Mapped[str | None]
    slot_source: Mapped[str | None]
    channel3_id: Mapped[str | None]


class ItemTag(Base):
    """Tags for one item from one source, written by jobs/tag.py. Defined here, next to Item,
    so the ingest prune can remove an item's tags together with the item."""

    __tablename__ = "item_tags"

    item_id: Mapped[str] = mapped_column(ForeignKey("items.item_id"), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    seasons: Mapped[list[str]] = mapped_column(JSON)
    vibes: Mapped[list[str]] = mapped_column(JSON)
    colors: Mapped[list[str]] = mapped_column(JSON)
    formality: Mapped[int | None]
    color_source: Mapped[str]  # "alt_text", "hex", or "none"
    probabilities: Mapped[dict] = mapped_column(JSON)  # CLIP scores, for debugging and the report
    model_version: Mapped[str]
    tagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CatalogRow(BaseModel):
    """One CSV row. Empty strings become None, so required fields can't be blank."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    item_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    brand_line: str = Field(min_length=1)
    slot: Slot
    color_hex: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    color_name: str | None = None
    price: PositiveFloat
    currency: str = Field(min_length=3, max_length=3)
    fabric: str | None = None
    description: str = Field(min_length=1)
    image_url: str = Field(pattern=r"^https?://")
    product_url: str = Field(pattern=r"^https?://")
    image_alt_text: str | None = None
    category_path: str | None = None
    gender: str | None = None
    slot_source: str | None = None
    channel3_id: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


# ------------------------------------------------------------------- ingest

def read_rows(csv_path: Path) -> tuple[list[dict], int]:
    """Valid rows from the CSV, plus how many were skipped (each one is printed)."""
    rows: dict[str, dict] = {}
    skipped = 0
    with csv_path.open(newline="", encoding="utf-8") as f:
        for line_no, raw in enumerate(csv.DictReader(f), start=2):  # line 1 is the header
            try:
                row = CatalogRow.model_validate(raw)
            except ValidationError as exc:
                problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                print(f"  skip line {line_no} ({raw.get('name') or 'no name'}): {problems}")
                skipped += 1
                continue
            if row.item_id in rows:
                print(f"  skip line {line_no} ({row.name}): duplicate item_id {row.item_id}")
                skipped += 1
                continue
            rows[row.item_id] = row.model_dump()
    return list(rows.values()), skipped


def ingest(csv_path: Path = CATALOG_CSV, db_url: str = DB_URL) -> tuple[int, int]:
    """Upsert the CSV into the items table. Returns (items in the table, rows skipped)."""
    rows, skipped = read_rows(csv_path)
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    upsert = insert(Item)
    upsert = upsert.on_conflict_do_update(
        index_elements=[Item.item_id],
        set_={col: upsert.excluded[col] for col in CatalogRow.model_fields if col != "item_id"},
    )
    prune = bool(rows) and skipped <= MAX_SKIPPED_SHARE_TO_PRUNE * (len(rows) + skipped)
    if not prune:
        print(f"  {skipped} of {len(rows) + skipped} rows skipped: keeping items that are missing "
              "from the CSV instead of removing them. Fix the CSV and re-run.")

    with Session(engine) as session, session.begin():
        if rows:
            session.execute(upsert, rows)
        if prune:
            kept = [r["item_id"] for r in rows]
            session.execute(delete(ItemTag).where(ItemTag.item_id.not_in(kept)))  # before the items they point to
            session.execute(delete(Item).where(Item.item_id.not_in(kept)))
        total = session.scalar(select(func.count()).select_from(Item)) or 0
    engine.dispose()
    return total, skipped


def main() -> None:
    # Product names include characters like "‑" (U+2011) that Windows can't print
    # when output is redirected to a file or a CI log (cp1252).
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=CATALOG_CSV)
    parser.add_argument("--db", default=DB_URL, help="SQLAlchemy URL (default: data/catalog.db)")
    args = parser.parse_args()

    total, skipped = ingest(args.csv, args.db)
    print(f"\n{total} items in the catalog ({skipped} rows skipped).")

    engine = create_engine(args.db)
    with Session(engine) as session:
        counts = dict(session.execute(select(Item.slot, func.count()).group_by(Item.slot)).all())
    for slot in SLOTS:
        n = counts.get(slot, 0)
        print(f"  {slot:<10} {n:>3}{'   <-- below 10' if n < 10 else ''}")


if __name__ == "__main__":
    main()
