"""Score the tagger against the hand-labeled gold set and write eval/tagging_report.md (FR-TAG-4).

Usage (from the repo root, after jobs/tag.py has run):
    python backend/src/stylist/jobs/tagging_report.py

Metrics (PRD §7, G3):
- Season and vibe "top-1": the model's first tag is one of the gold tags. Also reported is the
  stricter "first tag matches": the model's first tag equals the gold first tag.
- Formality: within ±1 of the gold score, plus exact match and mean absolute error.
- Color: the model's main color equals the gold main color (the PRD sets no target).
An item the tagger skipped (no tags row, or no photo so no CLIP tags) counts as a miss.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Run as a plain script: make `import stylist...` work from backend/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from stylist.jobs.ingest import DB_URL, ROOT, SLOTS, ItemTag  # noqa: E402
from stylist.jobs.tag import SOURCE  # noqa: E402
from stylist.tagging.clip_tagger import FORMALITY_PHRASES, SEASON_PHRASES, VIBE_PHRASES  # noqa: E402
from stylist.tagging.colors import COLOR_WORDS  # noqa: E402

GOLD_CSV = ROOT / "eval" / "gold_set.csv"
REPORT_MD = ROOT / "eval" / "tagging_report.md"

VOCAB = {"season": set(SEASON_PHRASES), "vibe": set(VIBE_PHRASES), "color": set(COLOR_WORDS)}
MAX_TAGS = {"season": 4, "vibe": 3, "color": 3}

# (key, axis, metric, PRD target or None). The key matches item_results().
METRICS = [
    ("season_top1", "Season", "Top-1", 0.75),
    ("season_first", "Season", "First tag matches", None),
    ("vibe_top1", "Vibe", "Top-1", 0.75),
    ("vibe_first", "Vibe", "First tag matches", None),
    ("formality_within_1", "Formality", "Within ±1", 0.85),
    ("formality_exact", "Formality", "Exact", None),
    ("color_main", "Color", "Main color matches", None),
]


@dataclass
class Tags:
    seasons: list[str]
    vibes: list[str]
    colors: list[str]
    formality: int | None


@dataclass
class GoldItem:
    item_id: str
    slot: str
    name: str
    tags: Tags


# ------------------------------------------------------------------- inputs

def split_tags(raw: str, axis: str, line_no: int) -> list[str]:
    tags = [t for t in raw.strip().split(";") if t]
    unknown = [t for t in tags if t not in VOCAB[axis]]
    if unknown or not 1 <= len(tags) <= MAX_TAGS[axis] or len(set(tags)) != len(tags):
        raise ValueError(f"gold_set.csv line {line_no}: bad {axis} {raw!r}")
    return tags


def read_gold(csv_path: Path = GOLD_CSV) -> list[GoldItem]:
    """The gold labels. Raises ValueError on any label outside the PRD taxonomy, so a typo
    can't quietly count as a model miss."""
    items = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):  # line 1 is the header
            formality = int(row["formality"]) if row["formality"].strip().isdigit() else None
            if formality not in FORMALITY_PHRASES:
                raise ValueError(f"gold_set.csv line {line_no}: bad formality {row['formality']!r}")
            items.append(GoldItem(row["item_id"], row["slot"], row["name"], Tags(
                seasons=split_tags(row["season"], "season", line_no),
                vibes=split_tags(row["vibe"], "vibe", line_no),
                colors=split_tags(row["color"], "color", line_no),
                formality=formality,
            )))
    return items


def read_predictions(db_url: str = DB_URL, source: str = SOURCE) -> tuple[dict[str, Tags], set[str]]:
    """Model tags by item_id, plus the model versions they came from."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        rows = list(session.scalars(select(ItemTag).where(ItemTag.source == source)))
    engine.dispose()
    predictions = {r.item_id: Tags(r.seasons, r.vibes, r.colors, r.formality) for r in rows}
    return predictions, {r.model_version for r in rows}


# ------------------------------------------------------------------ scoring

def item_results(gold: Tags, pred: Tags | None) -> dict[str, bool]:
    """Hit or miss on every metric for one item. A missing tag is a miss."""
    pred = pred or Tags([], [], [], None)
    return {
        "season_top1": bool(pred.seasons) and pred.seasons[0] in gold.seasons,
        "season_first": bool(pred.seasons) and pred.seasons[0] == gold.seasons[0],
        "vibe_top1": bool(pred.vibes) and pred.vibes[0] in gold.vibes,
        "vibe_first": bool(pred.vibes) and pred.vibes[0] == gold.vibes[0],
        "formality_within_1": pred.formality is not None and abs(pred.formality - gold.formality) <= 1,
        "formality_exact": pred.formality == gold.formality,
        "color_main": bool(pred.colors) and pred.colors[0] == gold.colors[0],
    }


def ratio(hits: int, total: int) -> str:
    return f"{hits}/{total} ({hits / total:.0%})" if total else "n/a"


def cell(text: str) -> str:
    """Safe inside a Markdown table: some product names contain '|'."""
    return text.replace("|", "\\|")


def build_report(gold: list[GoldItem], predictions: dict[str, Tags], model_versions: set[str]) -> str:
    results = {g.item_id: item_results(g.tags, predictions.get(g.item_id)) for g in gold}
    n = len(gold)

    def hits(key: str, items: list[GoldItem] = gold) -> int:
        return sum(results[g.item_id][key] for g in items)

    scored = [(predictions[g.item_id].formality, g.tags.formality) for g in gold
              if g.item_id in predictions and predictions[g.item_id].formality is not None]
    mae = sum(abs(p - t) for p, t in scored) / len(scored) if scored else None
    untagged = [g for g in gold if g.item_id not in predictions or not predictions[g.item_id].vibes]

    lines = [
        "# Tagging report",
        "",
        f"The tagger ({', '.join(f'`{v}`' for v in sorted(model_versions)) or 'no tags found'}) scored against "
        f"the {n}-item hand-labeled gold set in [`gold_set.csv`](gold_set.csv). The labeling rules are in "
        "[`gold_labeling.md`](gold_labeling.md).",
        "",
        "Generated by `backend/src/stylist/jobs/tagging_report.py`. Re-run it after every tagger change.",
        "",
        "## Summary",
        "",
        "| Axis | Metric | Result | Target | Met |",
        "|---|---|---|---|---|",
    ]
    for key, axis, metric, target in METRICS:
        met = "" if target is None else ("yes" if hits(key) >= target * n else "**no**")
        lines.append(f"| {axis} | {metric} | {ratio(hits(key), n)} | {f'≥ {target:.0%}' if target else ''} | {met} |")
        if key == "formality_exact":
            lines.append(f"| Formality | Mean absolute error | {f'{mae:.2f}' if mae is not None else 'n/a'} | | |")
    lines += [
        "",
        "- **Top-1** (season, vibe): the model's first tag is one of the gold tags. This is the PRD's G3 metric.",
        "- **First tag matches**: the model's first tag is the gold *first* tag, i.e. the most defining one.",
        "- An item the tagger skipped counts as a miss"
        + (f" ({len(untagged)} here: {', '.join(cell(g.name) for g in untagged)})." if untagged else " (none here)."),
        "- The gold set is the test set. Tune prompts on other items, or these numbers will overstate accuracy.",
        "",
        "## By slot",
        "",
        "| Slot | Season top-1 | Vibe top-1 | Formality ±1 | Color |",
        "|---|---|---|---|---|",
    ]
    for slot in SLOTS:
        in_slot = [g for g in gold if g.slot == slot]
        row = [ratio(hits(k, in_slot), len(in_slot)) for k in ("season_top1", "vibe_top1", "formality_within_1", "color_main")]
        lines.append(f"| {slot} | {' | '.join(row)} |")

    gold_first = Counter(g.tags.vibes[0] for g in gold)
    model_first = Counter(predictions[g.item_id].vibes[0] for g in gold if g not in untagged)
    lines += [
        "",
        "## First vibe: gold vs. model",
        "",
        "How often each vibe is the first tag, across the gold items.",
        "",
        "| Vibe | Gold | Model |",
        "|---|---|---|",
    ]
    for vibe in sorted(VIBE_PHRASES, key=lambda v: (-gold_first[v], -model_first[v], v)):
        lines.append(f"| {vibe} | {gold_first[vibe]} | {model_first[vibe]} |")

    lines += [
        "",
        "## Every item",
        "",
        "Each cell is *model / gold*. ✗ marks a miss on the column's headline metric (top-1, ±1, main color).",
        "",
        "<details><summary>Show all items</summary>",
        "",
        "| Slot | Item | Season | Vibe | Formality | Color |",
        "|---|---|---|---|---|---|",
    ]
    for g in gold:
        p = predictions.get(g.item_id) or Tags([], [], [], None)
        r = results[g.item_id]

        def pair(model: list[str] | int | None, truth: list[str] | int, key: str) -> str:
            show = (lambda v: ";".join(v) or "–") if isinstance(truth, list) else (lambda v: "–" if v is None else str(v))
            return f"{'' if r[key] else '✗ '}{show(model)} / {show(truth)}"

        lines.append(" | ".join([
            f"| {g.slot}", cell(g.name),
            pair(p.seasons, g.tags.seasons, "season_top1"),
            pair(p.vibes, g.tags.vibes, "vibe_top1"),
            pair(p.formality, g.tags.formality, "formality_within_1"),
            pair(p.colors, g.tags.colors, "color_main"),
        ]) + " |")
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_URL, help="SQLAlchemy URL (default: data/catalog.db)")
    parser.add_argument("--source", default=SOURCE, help=f"which tagger's rows to score (default: {SOURCE})")
    parser.add_argument("--out", type=Path, default=REPORT_MD)
    args = parser.parse_args()

    gold = read_gold()
    predictions, versions = read_predictions(args.db, args.source)
    if not predictions:
        raise SystemExit(f"No '{args.source}' tags in the database. Run jobs/tag.py first.")
    args.out.write_text(build_report(gold, predictions, versions), encoding="utf-8")

    for key, axis, metric, target in METRICS:
        hits = sum(item_results(g.tags, predictions.get(g.item_id))[key] for g in gold)
        goal = f"   (target {target:.0%})" if target else ""
        print(f"  {axis:<9} {metric:<19} {ratio(hits, len(gold))}{goal}")
    print(f"\nWrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")


if __name__ == "__main__":
    main()
