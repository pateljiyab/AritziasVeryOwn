"""Chat with the stylist in the terminal (the Day 2 milestone: Flows A-D without the web app).

Usage (from the repo root, after ingest.py and tag.py have run):
    python backend/src/stylist/cli.py                      # interactive
    python backend/src/stylist/cli.py "cozy fall Sunday" "different shoes" "perfect"
    python backend/src/stylist/cli.py --demo               # play Flows A-D from the PRD
    python backend/src/stylist/cli.py --no-clip ...        # skip CLIP (no torch needed; no relevance term)

Messages are parsed with keyword rules (core/keyword_parser.py) until the LLM parser exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Run as a plain script: make `import stylist...` work from backend/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stylist.adapters.catalog_store import load_catalog  # noqa: E402
from stylist.adapters.clip_text import TextEmbedder  # noqa: E402
from stylist.core.catalog import Catalog  # noqa: E402
from stylist.core.session import Reply, handle_message  # noqa: E402

DEMO = {
    "Flow A: mood request": ["cozy fall Sunday, running errands", "perfect"],
    "Flow B: occasion + weather": ["rooftop birthday dinner, it might get chilly"],
    "Flow C: named item": ["build something around the Effortless Pant"],
    "Flow D: refinement loop": ["weekend brunch with friends", "different shoes", "not those shoes",
                                "keep the shoes", "dressier", "make it edgier", "looks good"],
}


def show(reply: Reply) -> None:
    for note in reply.notes:
        print(f"  ! {note}")
    if reply.summary:
        print("  Accepted. Your outfit:")
        for piece in reply.summary["pieces"]:
            print(f"    {piece['slot']:<10} {piece['name']}  ${piece['price']:.2f}\n               {piece['product_url']}")
        print(f"    {'total':<10} ${reply.summary['total_price']:.2f} {reply.summary['currency']}")
        return
    if reply.outfit is None:
        return
    state = reply.state
    for slot, item in reply.outfit.pieces.items():
        lock = " [locked]" if state and state.locked.get(slot) == item.item_id else ""
        tags = f"{'/'.join(item.vibes) or '?'}, formality {item.formality or '?'}, {item.main_color or '?'}"
        print(f"  {slot:<10} {item.name}  ${item.price:.2f}{lock}\n             ({tags})")
    terms = "  ".join(f"{term} {value:.2f}" for term, value in reply.outfit.breakdown.items())
    print(f"  score {reply.outfit.score:.2f}   {terms}   total ${reply.outfit.total_price:.2f}")


def converse(catalog: Catalog, embed: TextEmbedder, messages: list[str] | None) -> None:
    """Run the given messages in order, or read them from the keyboard if there are none."""
    state = None
    lines = iter(messages) if messages is not None else None
    while True:
        if lines is None:
            try:
                text = input("\nyou> ")
            except EOFError:
                return
        else:
            text = next(lines, None)
            if text is None:
                return
            print(f"\nyou> {text}")
        if text.strip().lower() in {"quit", "exit"}:
            return
        reply = handle_message(catalog, state, text, embed)
        show(reply)
        state = None if reply.summary else reply.state  # accepting ends the session


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("messages", nargs="*", help="messages to send in order (default: interactive)")
    parser.add_argument("--demo", action="store_true", help="play Flows A-D from the PRD")
    parser.add_argument("--no-clip", action="store_true", help="don't load CLIP (skips the relevance term)")
    args = parser.parse_args()

    catalog = load_catalog()
    if not len(catalog):
        raise SystemExit("The catalog is empty. Run jobs/ingest.py and jobs/tag.py first.")
    embed = TextEmbedder(enabled=not args.no_clip)

    if args.demo:
        for title, messages in DEMO.items():
            print(f"\n=== {title} ===")
            converse(catalog, embed, messages)
        return
    if not args.messages:
        print(f"{len(catalog)} items loaded. Describe what you're dressing for (\"quit\" to stop).")
    converse(catalog, embed, args.messages or None)


if __name__ == "__main__":
    main()
