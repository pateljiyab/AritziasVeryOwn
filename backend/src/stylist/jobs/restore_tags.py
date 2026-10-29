"""Load the committed CLIP tags (data/clip_tags.json) into the database, without PyTorch.

Usage (from the repo root, after ingest.py):
    python backend/src/stylist/jobs/restore_tags.py

jobs/tag.py writes the file after every full run. This job fills in the tags for machines that
can't run CLIP, like CI and the Docker image. Items that already have CLIP tags keep them, so it
never overwrites a real run.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Run as a plain script: make `import stylist...` work from backend/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from stylist.jobs.ingest import DB_URL, Base, Item, ItemTag  # noqa: E402
from stylist.jobs.tag import CLIP_TAGS_JSON, SOURCE  # noqa: E402


def restore(db_url: str = DB_URL, path: Path = CLIP_TAGS_JSON) -> int:
    """Add the snapshot's tags for catalog items that have none. Returns how many were added."""
    if not path.exists():
        return 0
    snapshot = json.loads(path.read_text())
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        in_catalog = set(session.scalars(select(Item.item_id)))
        tagged = set(session.scalars(select(ItemTag.item_id).where(ItemTag.source == SOURCE)))
        rows = [ItemTag(item_id=item_id, source=SOURCE, probabilities={}, model_version=snapshot["model_version"],
                        tagged_at=now, **tags)
                for item_id, tags in snapshot["items"].items() if item_id in in_catalog and item_id not in tagged]
        session.add_all(rows)
    engine.dispose()
    return len(rows)


def main() -> None:
    added = restore()
    print(f"Restored CLIP tags for {added} items from {CLIP_TAGS_JSON.name}." if added else
          f"No CLIP tags to restore (every item is tagged, or {CLIP_TAGS_JSON.name} is missing).")


if __name__ == "__main__":
    main()
