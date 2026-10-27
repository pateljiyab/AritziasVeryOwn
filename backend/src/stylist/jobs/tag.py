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
import hashlib
import http.client
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# Run as a plain script: make `import stylist...` work from backend/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
from sqlalchemy import create_engine, delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from stylist.jobs.ingest import DB_URL, ROOT, Base, Item, ItemTag  # noqa: E402
from stylist.tagging.clip_tagger import MODEL_VERSION, ClipModel, LabelBank  # noqa: E402
from stylist.tagging.colors import main_color  # noqa: E402

IMAGE_DIR = ROOT / "data" / "images"
EMBEDDINGS_NPY = ROOT / "data" / "embeddings.npy"
EMBEDDING_IDS_JSON = ROOT / "data" / "embedding_ids.json"
SOURCE = "auto"
USER_AGENT = "AritziasVeryOwn-edu/0.1 (student project)"
BATCH_SIZE = 32


def image_path(item: Item) -> Path:
    """Cache file for the item's photo. The name includes a hash of the URL, so a changed
    image_url is downloaded again instead of reusing the old photo."""
    url_hash = hashlib.sha1(item.image_url.encode()).hexdigest()[:12]
    return IMAGE_DIR / f"{item.item_id}-{url_hash}.img"


# Download and cache the item's photo, returning the local path or None if it fails.
def download_image(item: Item) -> Path | None:
    """Cached path of the item's photo, downloading it the first time. None if it fails."""
    path = image_path(item)
    if path.exists():
        return path
    try:
        request = urllib.request.Request(item.image_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except (OSError, http.client.HTTPException) as exc:  # IncompleteRead is an HTTPException, not OSError
        print(f"  ! image download failed for {item.name}: {exc!r}")
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


def try_load_image(item: Item, path: Path):
    """The photo, or None if the cached file can't be read. A bad file is deleted so the
    next run downloads it again instead of failing on it forever."""
    try:
        return load_image(path)
    except Exception as exc:  # Pillow raises several error types for corrupt or truncated files
        print(f"  ! unreadable photo for {item.name}, will re-download next run: {exc!r}")
        path.unlink(missing_ok=True)
        return None

# Tag every catalog item with season, vibe, color, and formality, saving to the database.
def tag_catalog(db_url: str = DB_URL, limit: int | None = None) -> None:
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    trial = limit is not None

    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        items = list(session.scalars(select(Item).order_by(Item.item_id)))
    if trial:
        items = items[:limit]

    print(f"Downloading photos for {len(items)} items (cached in {IMAGE_DIR.relative_to(ROOT)})...")
    paths = {item.item_id: download_image(item) for item in items}
    with_photo = [item for item in items if paths[item.item_id]]

    print(f"Loading CLIP ({MODEL_VERSION}); the first run downloads the model weights...")
    model = ClipModel()
    bank = LabelBank(model.encode_texts, sorted({item.slot for item in items}))

    embeddings: dict[str, np.ndarray] = {}
    for start in range(0, len(with_photo), BATCH_SIZE):
        loaded = [(item, try_load_image(item, paths[item.item_id])) for item in with_photo[start:start + BATCH_SIZE]]
        loaded = [(item, image) for item, image in loaded if image is not None]
        if loaded:
            vectors = model.encode_images([image for _, image in loaded])
            embeddings.update(zip((item.item_id for item, _ in loaded), vectors))
        print(f"  embedded {min(start + BATCH_SIZE, len(with_photo))}/{len(with_photo)} photos")

    if not embeddings:  # offline or every URL dead: keep the existing tags and embeddings
        raise SystemExit("No photos could be loaded, so nothing was saved. Check the network and image URLs.")

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
    if trial:  # a trial run only replaces the items it tagged
        stale = stale.where(ItemTag.item_id.in_([item.item_id for item in items]))
    with Session(engine) as session, session.begin():
        session.execute(stale)
        session.add_all(rows)

    if not trial:  # a trial run shouldn't overwrite the full embedding file
        ids = sorted(embeddings)
        np.save(EMBEDDINGS_NPY, np.stack([embeddings[i] for i in ids]))
        EMBEDDING_IDS_JSON.write_text(json.dumps({"model_version": MODEL_VERSION, "item_ids": ids}, indent=1))

    missing = len(items) - len(embeddings)
    print(f"\nTagged {len(rows)} items ({missing} without a usable photo got color only).")
    if not trial:
        print(f"Saved {len(embeddings)} image embeddings to {EMBEDDINGS_NPY}.")


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_URL, help="SQLAlchemy URL (default: data/catalog.db)")
    parser.add_argument("--limit", type=positive_int, help="only tag the first N items (trial run)")
    args = parser.parse_args()
    tag_catalog(args.db, args.limit)


if __name__ == "__main__":
    main()
