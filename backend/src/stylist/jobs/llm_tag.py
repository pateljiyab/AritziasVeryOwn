"""Tag every catalog item's vibe with Claude Haiku 5.5, as a second signal next to CLIP (FR-TAG-5).

Usage (from the repo root, after ingest.py, and after tag.py so the photos are cached):
    python backend/src/stylist/jobs/llm_tag.py               # tag what isn't cached, save every item
    python backend/src/stylist/jobs/llm_tag.py --limit 5     # trial run on the first 5 items

CLIP misses the vibe target (43% top-1 vs. 75%), so vibe is tagged first. Season and formality
stay with CLIP, which meets their targets. Each item is sent once with its photo (when cached)
and its name, slot, fabric, description, and alt text, and gets 1-3 vibes back.

Answers are cached in data/llm_vibes.json, keyed by item and by a hash of what was sent, so a
re-run only tags new or changed items. The cache is committed: without an API key the job still
saves the cached vibes, so anyone can rebuild the database without paying for the calls.

The prompt is the PRD's vibe definitions (Appendix A) plus the vibe rules from
eval/gold_labeling.md, the same instructions the gold set was labeled with. It was written
without looking at any gold item, and it isn't tuned on the gold set.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

# Run as a plain script: make `import stylist...` work from backend/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import create_engine, delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from stylist.core.models import Vibe  # noqa: E402
from stylist.jobs.ingest import DB_URL, ROOT, Base, Item, ItemTag  # noqa: E402
from stylist.jobs.tag import image_path  # noqa: E402

SOURCE = "llm"
MODEL = "claude-haiku-5-5"
PROMPT_VERSION = "vibes-v1"
MODEL_VERSION = f"{MODEL}/{PROMPT_VERSION}"
CACHE_JSON = ROOT / "data" / "llm_vibes.json"
PHOTO_SIZE = 512  # px on the long side: enough to read the look, and about 350 input tokens
WORKERS = 4

SYSTEM_PROMPT = """\
You tag clothing and accessories from Aritzia's catalog with style vibes, for an outfit stylist. \
Look at the product photo and read the details, then give 1 to 3 vibes, the most defining first. \
Ask "what would a stylist call this?" Stop at 1 or 2 unless a third is clearly true. Use minimal \
for plain neutral pieces, not as a fallback.

The vibes:
- cozy: soft, warm, relaxed textures: knits, fleece, oversized cuts
- polished: tailored, clean lines, classic office basics, office-ready
- going-out: evening, statement, a little dressy or bold
- cute: playful and youthful: bright or pastel colours, fun details (bows, puff sleeves, mini lengths)
- off-duty: casual, effortless, weekend
- chic: elevated and fashion-forward: a strong silhouette or luxe fabric meant to look expensive \
(satin, leather, a sharp cut)
- athleisure: sporty, technical, activewear-adjacent
- minimal: neutral palette, simple silhouettes, no embellishment
- romantic: soft details: florals, ruffles, lace, delicate fabrics
- edgy: leather, darker palette, sharper silhouettes"""


class VibeTags(BaseModel):
    vibes: list[Vibe]  # most defining first


def details(item: Item) -> str:
    lines = [f"Slot: {item.slot}", f"Name: {item.name}", f"Brand line: {item.brand_line}"]
    lines += [f"{label}: {value}" for label, value in
              (("Fabric", item.fabric), ("Description", item.description), ("Photo alt text", item.image_alt_text))
              if value]
    return "\n".join(lines)


def photo_jpeg(item: Item) -> bytes | None:
    """The cached photo (from tag.py), shrunk to a small JPEG. None if it isn't cached or can't be read."""
    path = image_path(item)
    if not path.exists():
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((PHOTO_SIZE, PHOTO_SIZE))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85)
            return out.getvalue()
    except Exception:  # no Pillow, or a corrupt file: tag from the text alone
        return None


def content_hash(item: Item, photo: bytes | None) -> str:
    sent = json.dumps([MODEL_VERSION, SYSTEM_PROMPT, details(item), hashlib.sha1(photo or b"").hexdigest()])
    return hashlib.sha1(sent.encode()).hexdigest()[:16]


def ask(client, item: Item, photo: bytes | None) -> list[str]:
    content: list[dict] = []
    if photo:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode(photo).decode()}})
    content.append({"type": "text", "text": details(item)})
    response = client.messages.parse(
        model=MODEL, max_tokens=1024, system=SYSTEM_PROMPT, messages=[{"role": "user", "content": content}],
        output_format=VibeTags, output_config={"effort": "low"},
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise ValueError(f"no answer (stop reason {response.stop_reason})")
    vibes = list(dict.fromkeys(response.parsed_output.vibes))[:3]
    if not vibes:
        raise ValueError("no vibes in the answer")
    return vibes


def read_cache(path: Path = CACHE_JSON) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return data["items"] if data.get("model_version") == MODEL_VERSION else {}


def write_cache(cache: dict[str, dict], path: Path = CACHE_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"model_version": MODEL_VERSION, "items": dict(sorted(cache.items()))}
    path.write_text(json.dumps(payload, indent=1) + "\n")


def tag_vibes(db_url: str = DB_URL, client=None, limit: int | None = None, cache_path: Path = CACHE_JSON) -> None:
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        items = list(session.scalars(select(Item).order_by(Item.item_id)))
    if limit is not None:
        items = items[:limit]

    cache = read_cache(cache_path)
    photos = {item.item_id: photo_jpeg(item) for item in items}
    hashes = {item.item_id: content_hash(item, photos[item.item_id]) for item in items}
    todo = [item for item in items if cache.get(item.item_id, {}).get("hash") != hashes[item.item_id]]
    print(f"{len(items) - len(todo)} of {len(items)} items are cached; {len(todo)} to tag "
          f"({sum(photos[i.item_id] is not None for i in todo)} with a photo).")

    failed: list[str] = []
    if todo and client is None:
        print("No ANTHROPIC_API_KEY: saving the cached vibes only. The rest keep their CLIP vibes.")
    elif todo:
        def one(item: Item) -> tuple[Item, list[str] | Exception]:
            try:
                return item, ask(client, item, photos[item.item_id])
            except Exception as exc:  # one bad item shouldn't stop the batch; it keeps its CLIP vibes
                return item, exc

        with ThreadPoolExecutor(WORKERS) as pool:
            for n, (item, result) in enumerate(pool.map(one, todo), start=1):
                if isinstance(result, Exception):
                    failed.append(f"{item.name}: {type(result).__name__}: {result}")
                else:
                    cache[item.item_id] = {"hash": hashes[item.item_id], "vibes": result}
                print(f"  tagged {n}/{len(todo)}", end="\r")
        print()
        write_cache(cache, cache_path)

    now = datetime.now(UTC)
    rows = [ItemTag(item_id=item.item_id, source=SOURCE, seasons=[], vibes=cache[item.item_id]["vibes"], colors=[],
                    formality=None, color_source="none", probabilities={}, model_version=MODEL_VERSION, tagged_at=now)
            for item in items if item.item_id in cache]
    stale = delete(ItemTag).where(ItemTag.source == SOURCE)
    if limit is not None:  # a trial run only replaces the items it looked at
        stale = stale.where(ItemTag.item_id.in_([item.item_id for item in items]))
    with Session(engine) as session, session.begin():
        session.execute(stale)
        session.add_all(rows)
    engine.dispose()

    print(f"Saved LLM vibes for {len(rows)} of {len(items)} items.")
    for line in failed:
        print(f"  ! {line}")


def positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=positive_int, help="tag only the first N items (a trial run)")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    client = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        client = anthropic.Anthropic(max_retries=5, timeout=60.0)  # a batch job can wait out rate limits
    tag_vibes(client=client, limit=args.limit)


if __name__ == "__main__":
    main()
