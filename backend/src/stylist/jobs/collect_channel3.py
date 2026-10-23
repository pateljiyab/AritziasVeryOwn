"""Collect Aritzia products from the Channel3 API into data/seed_catalog.csv.

Usage (from the repo root, one line each):
    py api/src/stylist/jobs/collect_channel3.py --inspect      # 1 call: print one product
    py api/src/stylist/jobs/collect_channel3.py                # collect slots that are still short
    py api/src/stylist/jobs/collect_channel3.py --remap        # rebuild the CSV from saved data, 0 calls

Needs CHANNEL3_API_KEY in .env. Free tier is 1,000 searches/month; a run uses at
most --max-calls searches (default 60). Raw responses accumulate across runs in
data/raw/channel3_products.jsonl, and the CSV is always rebuilt from all of them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from channel3_sdk import Channel3  
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[4]
OUT_CSV = ROOT / "data" / "seed_catalog.csv"
RAW_JSONL = ROOT / "data" / "raw" / "channel3_products.jsonl"

ARITZIA_BRAND_ID = "sxfc"
SLOTS = ["top", "bottom", "dress", "outerwear", "shoes", "accessory"]

# Several queries per slot so the catalog covers different styles and formality levels.
QUERIES: dict[str, list[str]] = {
    "top": ["knit sweater", "t-shirt", "blouse", "bodysuit", "cardigan"],
    "bottom": ["trousers", "jeans", "skirt", "sweatpants", "shorts"],
    "dress": ["midi dress", "mini dress", "maxi dress", "jumpsuit"],
    "outerwear": ["wool coat", "puffer jacket", "blazer", "trench coat", "leather jacket"],
    "shoes": ["boots", "sneakers", "loafers", "heels", "sandals"],
    "accessory": ["bag", "scarf", "belt", "hat", "jewelry"],
}

# Checked in this order, so "shirt jacket" becomes outerwear rather than top.
SLOT_PATTERNS: list[tuple[str, str]] = [
    ("dress", r"\b(dress(es)?|jumpsuits?|rompers?)\b"),
    ("outerwear", r"\b(coats?|jackets?|blazers?|parkas?|puffers?|trench|vests?|shackets?|outerwear)\b"),
    ("shoes", r"\b(shoes?|boots?|sneakers?|loafers?|heels?|sandals?|flats?|mules?|slides?|footwear)\b"),
    ("accessory", r"\b(bags?|handbags?|totes?|belts?|scarf|scarves|hats?|beanies?|caps?|jewelry|"
                  r"earrings?|necklaces?|bracelets?|sunglasses|gloves|mittens)\b"),
    ("bottom", r"\b(pants?|trousers?|jeans?|skirts?|shorts|leggings?|sweatpants?|joggers?)\b"),
    ("top", r"\b(shirts?|tees?|t-shirts?|tops?|blouses?|sweaters?|cardigans?|tanks?|"
            r"bodysuits?|hoodies?|sweatshirts?|turtlenecks?|camis?|camisoles?|polos?|bustiers?|corsets?)\b"),
]

# Top-level categories that can hold outfit pieces (handbags live under "Luggage & Bags").
APPAREL_ROOTS = {"Apparel & Accessories", "Luggage & Bags"}

# Never part of an outfit, even when Aritzia sells them.
EXCLUDE_PATTERN = (r"\b(tights|hosiery|pantyhose|socks?|bras?|bralettes?|underwear|panties|"
                   r"garment|storage|cases?|snowboards?|skis?|charms?|pajamas?|sleepwear)\b")

# Preferred product photo for CLIP: flat lay first (no model in the shot), then the main image.
SHOT_PREFERENCE = ["flat_lay", "product", "on_model"]

CSV_COLUMNS = [
    "item_id", "name", "brand_line", "slot", "color_hex", "color_name",
    "price", "currency", "fabric", "description", "image_url", "product_url",
    "image_alt_text", "category_path", "gender", "slot_source", "channel3_id",
]


# ---------------------------------------------------------------- API helpers

def to_dict(obj: Any) -> Any:
    """Recursively turn SDK model objects into plain JSON-compatible dicts and lists."""
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):  # pydantic v2
        return to_dict(obj.model_dump(mode="json"))
    if hasattr(obj, "dict"):  # pydantic v1
        return to_dict(obj.dict())
    return str(obj)


def search(client: Channel3, query: str, limit: int) -> Any:
    """First page of results. The SDK returns a pager with .items / .has_next / .get_next()."""
    return client.products.search(
        query=query,
        limit=limit,
        filters={"brand_ids": [ARITZIA_BRAND_ID]},
    )


def has_next(page: Any) -> bool:
    flag = getattr(page, "has_next", False)
    return bool(flag() if callable(flag) else flag)


# ------------------------------------------------------------ field mapping

def main_color_hex(product: dict) -> str:
    colors = product.get("colors") or []
    if not colors:
        return ""
    return max(colors, key=lambda c: c.get("percentage") or 0).get("hex") or ""


def best_image(product: dict) -> dict:
    images = product.get("images") or []
    if not images:
        return {}
    for shot in SHOT_PREFERENCE:
        for img in images:
            if img.get("shot_type") == shot and img.get("cleaned_url"):
                return img
    return next((img for img in images if img.get("is_main_image")), images[0])


def guess_slot(product: dict) -> tuple[str, str] | None:
    """Slot from the product's category (most specific level first), then its title.
    Returns None when nothing matches: guessing from the search query put tights
    and snowboards into "shoes"."""
    category = product.get("category") or {}
    levels = [category.get("title")] + [ref.get("title") for ref in reversed(category.get("path") or [])]
    candidates = [("category", level) for level in levels if level]
    candidates.append(("title", product.get("title") or ""))

    for source, text in candidates:
        for slot, pattern in SLOT_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return slot, source
    return None


def skip_reason(product: dict) -> str | None:
    """Why a product doesn't belong in the catalog, or None if it does."""
    category = product.get("category") or {}
    path = category.get("path") or []
    if path and path[0].get("title") not in APPAREL_ROOTS:
        return "not apparel"
    # Only the most specific category and the title: camisoles sit under "Underwear & Socks".
    text = f"{category.get('title') or ''} {product.get('title') or ''}"
    if re.search(EXCLUDE_PATTERN, text, re.IGNORECASE):
        return "excluded type"
    if product.get("gender") == "male":
        return "menswear"
    return None


def to_row(product: dict) -> dict | None:
    offer = (product.get("offers") or [{}])[0]
    price = offer.get("price") or {}
    name = (product.get("title") or "").strip()
    channel3_id = product.get("id") or ""
    if not name or not channel3_id or skip_reason(product):
        return None
    slot_guess = guess_slot(product)
    if slot_guess is None:
        return None

    slot, slot_source = slot_guess
    image = best_image(product)
    category = product.get("category") or {}
    brands = product.get("brands") or [{}]

    return {
        # Hash of the Channel3 id: stable across runs (the offer URL has tracking params that change).
        "item_id": hashlib.sha1(f"channel3:{channel3_id}".encode()).hexdigest(),
        "name": name,
        "brand_line": brands[0].get("name") or "Aritzia",
        "slot": slot,
        "color_hex": main_color_hex(product),
        "color_name": "",  # Channel3 gives hex only; mapped to the color taxonomy during tagging
        "price": price.get("price") or "",
        "currency": price.get("currency") or "",
        "fabric": ", ".join(product.get("materials") or []),
        "description": re.sub(r"\s+", " ", product.get("description") or "").strip(),
        "image_url": image.get("cleaned_url") or image.get("url") or "",
        "product_url": offer.get("url") or "",  # Channel3 buy link; keep the full URL
        "image_alt_text": image.get("alt_text") or "",
        "category_path": " > ".join(ref.get("title", "") for ref in category.get("path") or []),
        "gender": product.get("gender") or "",
        "slot_source": slot_source,
        "channel3_id": channel3_id,
    }


# ---------------------------------------------------------------- main flows

def inspect(client: Channel3) -> None:
    page = search(client, "sweater", limit=1)
    products = [to_dict(p) for p in getattr(page, "items", None) or []]
    if not products:
        print("No products returned. Raw response:", page)
        return
    print(json.dumps(products[0], indent=2)[:4000])
    print("\nMapped row:", json.dumps(to_row(products[0]), indent=2))


def load_raw() -> tuple[dict[str, dict], set[str], dict[str, int]]:
    """Rebuild rows from every saved raw response. Returns (rows, queries already run, skip counts)."""
    rows: dict[str, dict] = {}  # item_id -> row, so duplicates across queries and runs collapse
    queries_done: set[str] = set()
    skipped: dict[str, int] = {}
    if not RAW_JSONL.exists():
        return rows, queries_done, skipped
    with RAW_JSONL.open(encoding="utf-8") as raw:
        for line in raw:
            record = json.loads(line)
            queries_done.add(record["query"])
            row = to_row(record["product"])
            if row:
                rows.setdefault(row["item_id"], row)
            else:
                reason = skip_reason(record["product"]) or "no slot match"
                skipped[reason] = skipped.get(reason, 0) + 1
    return rows, queries_done, skipped


def collect(client: Channel3, per_slot: int, page_size: int, max_calls: int) -> None:
    rows, queries_done, _ = load_raw()
    calls = 0
    RAW_JSONL.parent.mkdir(parents=True, exist_ok=True)

    def slot_count(slot: str) -> int:
        return sum(r["slot"] == slot for r in rows.values())

    with RAW_JSONL.open("a", encoding="utf-8") as raw:
        for query_slot in SLOTS:
            for query in QUERIES[query_slot]:
                if query in queries_done:
                    continue
                page = None
                while calls < max_calls and slot_count(query_slot) < per_slot:
                    try:
                        page = search(client, query, page_size) if page is None else page.get_next()
                    except Exception as exc:  # keep going on one bad query; stop if the first call fails
                        print(f"  ! '{query}' failed: {exc!r}")
                        if calls == 0:
                            sys.exit("First call failed. Check CHANNEL3_API_KEY and the SDK version.")
                        break
                    calls += 1

                    products = [to_dict(p) for p in getattr(page, "items", None) or []]
                    for product in products:
                        raw.write(json.dumps({"query": query, "product": product}) + "\n")
                        row = to_row(product)
                        if row:
                            rows.setdefault(row["item_id"], row)
                    print(f"[{calls:>3}] {query_slot:<9} '{query}': {len(products)} products")

                    if not products or not has_next(page):
                        break
                    time.sleep(0.5)  # be polite to the API

    print(f"\nUsed {calls} API calls this run.")
    write_csv()


# Main colors closer than this (RGB distance) count as the same colorway.
SAME_COLOR_DISTANCE = 20


def normalized_name(name: str) -> str:
    """Channel3 lists some products several times under different ids, with small spelling
    differences ("Women's G.H.BASS x Aritzia Spence…" vs "G.h.bass X Aritzia Spence…")."""
    name = name.lower()
    name = re.sub(r"\bwomen'?s\b|\bx aritzia\b|g\.?h\.? ?bass|sperry", " ", name)
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def hex_to_rgb(hex_color: str) -> tuple[int, int, int] | None:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) if len(h) == 6 else None


def is_duplicate(row: dict, seen: dict[str, list[tuple[int, int, int] | None]]) -> bool:
    """Same normalized name + nearly the same main color = same item. Other colorways stay."""
    name, rgb = normalized_name(row["name"]), hex_to_rgb(row["color_hex"])
    for other in seen.get(name, []):
        if rgb is None or other is None:
            if rgb is other:
                return True
        elif sum((a - b) ** 2 for a, b in zip(rgb, other)) ** 0.5 < SAME_COLOR_DISTANCE:
            return True
    seen.setdefault(name, []).append(rgb)
    return False


def write_csv() -> None:
    """Write the CSV from all saved raw responses and print a coverage report."""
    all_rows, _, skipped = load_raw()
    rows: dict[str, dict] = {}
    seen: dict[str, list[tuple[int, int, int] | None]] = {}
    for item_id, row in all_rows.items():
        if is_duplicate(row, seen):
            skipped["duplicate listing"] = skipped.get("duplicate listing", 0) + 1
            continue
        rows[item_id] = row
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(sorted(rows.values(), key=lambda r: (SLOTS.index(r["slot"]), r["name"])))

    print(f"Wrote {len(rows)} items to {OUT_CSV.relative_to(ROOT)} (from {RAW_JSONL.relative_to(ROOT)}).\n")
    for slot in SLOTS:
        n = sum(r["slot"] == slot for r in rows.values())
        print(f"  {slot:<10} {n:>3}{'   <-- below 10, add items by hand' if n < 10 else ''}")
    for column in ("color_hex", "description", "image_url", "price"):
        missing = sum(not r[column] for r in rows.values())
        if missing:
            print(f"  note: {missing} items have no {column}")
    for reason, n in sorted(skipped.items()):
        print(f"  skipped {n} raw results: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inspect", action="store_true", help="fetch one product and print its fields")
    parser.add_argument("--remap", action="store_true", help="rebuild the CSV from saved raw data (no API calls)")
    parser.add_argument("--per-slot", type=int, default=20, help="stop collecting a slot after this many items")
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--max-calls", type=int, default=60, help="hard cap on API calls for this run")
    args = parser.parse_args()

    if args.remap:
        write_csv()
        return

    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("CHANNEL3_API_KEY")
    if not api_key:
        sys.exit("CHANNEL3_API_KEY is not set. Add it to .env in the repo root.")
    client = Channel3(api_key=api_key)

    if args.inspect:
        inspect(client)
    else:
        collect(client, args.per_slot, args.page_size, args.max_calls)


if __name__ == "__main__":
    main()
