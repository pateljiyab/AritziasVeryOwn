"""Tag every catalog item with season, vibe, color, and formality (FR-TAG-1, FR-TAG-2).

Usage (from the repo root, after `pip install -r requirements-ml.txt`):
    py backend/src/stylist/jobs/tag.py               # all items
    py backend/src/stylist/jobs/tag.py --limit 10    # quick trial run

- Color comes from rules over the image alt text and hex color (stylist/tagging/colors.py).
- Season, vibe, and formality come from CLIP zero-shot on the product photo
  (stylist/tagging/clip_tagger.py). No API key or paid service is needed.

Photos are downloaded once into data/images/. The CLIP image embeddings are also saved to
data/embeddings.npy (+ data/embedding_ids.json) for outfit retrieval later.
Re-running replaces this job's tags; nothing else in the database is touched.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# Run as a plain script: make `import stylist...` work from backend/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
from sqlalchemy import JSON, DateTime, ForeignKey, String, create_engine, delete, select  # noqa: E402
from sqlalchemy.orm import Mapped, Session, mapped_column  # noqa: E402

from stylist.jobs.ingest import DB_URL, ROOT, Base, Item  # noqa: E402
from stylist.tagging.clip_tagger import MODEL_VERSION, ClipModel, LabelBank  # noqa: E402
from stylist.tagging.colors import main_color  # noqa: E402

IMAGE_DIR = ROOT / "data" / "images"
EMBEDDINGS_NPY = ROOT / "data" / "embeddings.npy"
EMBEDDING_IDS_JSON = ROOT / "data" / "embedding_ids.json"
SOURCE = "auto"
USER_AGENT = "AritziasVeryOwn-edu/0.1 (student project)"
BATCH_SIZE = 32


# SQLAlchemy ORM model for the item_tags table. Each row is a tag for one item, from one source.
class ItemTag(Base):    
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


# Download and cache the item's photo, returning the local path or None if it fails.
def download_image(item: Item) -> Path | None:
    """Cached path of the item's photo, downloading it the first time. None if it fails."""
    path = IMAGE_DIR / f"{item.item_id}.img"
    if path.exists():
        return path
    try:
        request = urllib.request.Request(item.image_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except OSError as exc:
        print(f"  ! image download failed for {item.name}: {exc}")
        return None
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    time.sleep(0.2)  # be polite to the image server
    return path


# Load an image from disk and convert it to RGB for CLIP.
def load_image(path: Path):
    from PIL import Image

    with Image.open(path) as img:
        return img.convert("RGB")

# Tag every catalog item with season, vibe, color, and formality, saving to the database.
def tag_catalog(db_url: str = DB_URL, limit: int | None = None) -> None:
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        items = list(session.scalars(select(Item).order_by(Item.item_id)))
    if limit:
        items = items[:limit]

    print(f"Downloading photos for {len(items)} items (cached in {IMAGE_DIR.relative_to(ROOT)})...")
    paths = {item.item_id: download_image(item) for item in items}
    with_photo = [item for item in items if paths[item.item_id]]

    print(f"Loading CLIP ({MODEL_VERSION}); the first run downloads the model weights...")
    model = ClipModel()
    bank = LabelBank(model.encode_texts, sorted({item.slot for item in items}))

    embeddings: dict[str, np.ndarray] = {}
    for start in range(0, len(with_photo), BATCH_SIZE):
        batch = with_photo[start:start + BATCH_SIZE]
        vectors = model.encode_images([load_image(paths[item.item_id]) for item in batch])
        embeddings.update(zip((item.item_id for item in batch), vectors))
        print(f"  embedded {min(start + BATCH_SIZE, len(with_photo))}/{len(with_photo)} photos")

    now = datetime.now(UTC)
    rows = []
    for item in items:
        color, color_source = main_color(item.image_alt_text, item.color_hex, item.slot)
        clip = bank.tag(embeddings[item.item_id], item.slot) if item.item_id in embeddings else None
        rows.append(ItemTag(
            item_id=item.item_id,
            source=SOURCE,
            seasons=clip.seasons if clip else [],
            vibes=clip.vibes if clip else [],
            colors=[color] if color else [],
            formality=clip.formality if clip else None,
            color_source=color_source,
            probabilities=clip.probabilities if clip else {},
            model_version=MODEL_VERSION,
            tagged_at=now,
        ))

    stale = delete(ItemTag).where(ItemTag.source == SOURCE)
    if limit:  # a trial run only replaces the items it tagged
        stale = stale.where(ItemTag.item_id.in_([item.item_id for item in items]))
    with Session(engine) as session, session.begin():
        session.execute(stale)
        session.add_all(rows)

    if not limit:  # a trial run shouldn't overwrite the full embedding file
        ids = sorted(embeddings)
        np.save(EMBEDDINGS_NPY, np.stack([embeddings[i] for i in ids]))
        EMBEDDING_IDS_JSON.write_text(json.dumps({"model_version": MODEL_VERSION, "item_ids": ids}, indent=1))

    missing = len(items) - len(with_photo)
    print(f"\nTagged {len(rows)} items ({missing} without a photo got color only).")
    if not limit:
        print(f"Saved {len(embeddings)} image embeddings to {EMBEDDINGS_NPY}.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_URL, help="SQLAlchemy URL (default: data/catalog.db)")
    parser.add_argument("--limit", type=int, help="only tag the first N items (trial run)")
    args = parser.parse_args()
    tag_catalog(args.db, args.limit)


if __name__ == "__main__":
    main()
