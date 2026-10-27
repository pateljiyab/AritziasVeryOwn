"""Score items and outfits against a request (FR-GEN-3). Pure functions over core.models.

An outfit's score is a weighted mean of terms, each in [0, 1]:
- relevance: CLIP similarity between the request text and each piece's photo
- vibe: how well each piece's vibe tags match the requested vibes
- formality: how close each piece's formality is to the target
- season: whether each piece is tagged for the requested season (a soft term; see generator)
- color: how well the pieces' main colors go together (PRD Appendix A)
A term that doesn't apply (no request text, no vibes asked for, ...) is left out and the
remaining weights are renormalized, so a vague request is scored on what it does say.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np

from stylist.core.models import Item, ParsedRequest

# Hand-set starting weights, not tuned: there is no labeled outfit data yet (G1 is rated later).
WEIGHTS = {"relevance": 0.35, "vibe": 0.25, "formality": 0.2, "color": 0.15, "season": 0.05}
ITEM_TERMS = ["relevance", "vibe", "formality", "season"]

# Score for an item missing the tag a term needs (e.g. no photo, so no CLIP tags).
UNKNOWN = 0.5

# PRD Appendix A. Neutrals pair with every color; other colors are compared by hue angle.
NEUTRALS = {"black", "white", "cream", "beige", "camel", "brown", "grey", "navy", "denim", "olive", "metallic"}
HUE = {"red": 0, "burgundy": 345, "pink": 330, "orange": 25, "yellow": 50, "green": 130, "teal": 180,
       "blue": 215, "purple": 275}
MULTI_VS_COLOR = 0.6


# ------------------------------------------------------------------ color

def hue_distance(a: int, b: int) -> int:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def color_pair(a: str | None, b: str | None) -> float:
    """How well two main colors go together, in [0, 1]."""
    if a is None or b is None:
        return UNKNOWN
    if a in NEUTRALS or b in NEUTRALS:
        return 1.0
    if a == "multi" or b == "multi":
        return MULTI_VS_COLOR  # two multis never get here: the generator allows at most one
    d = hue_distance(HUE[a], HUE[b])
    if d <= 30:
        return 0.9  # same color family (tonal)
    if d <= 60:
        return 0.75  # analogous
    if d >= 150:
        return 0.7  # complementary
    return 0.3  # in between: tends to clash


def color_harmony(items: list[Item]) -> float:
    pairs = list(combinations([item.main_color for item in items], 2))
    return sum(color_pair(a, b) for a, b in pairs) / len(pairs) if pairs else 1.0


# ------------------------------------------------------------- item terms

# Requested vibes count less the further down the list they are, so a nudge ("make it edgier"),
# which moves a vibe to the front, actually changes the outfit.
REQUESTED_VIBE_WEIGHTS = [1.0, 0.7, 0.5]


def vibe_match(item: Item, wanted: list[str]) -> float:
    """1.0 when the item's first vibe is the first one asked for. An item's later vibes count
    0.6, and later requested vibes count less (REQUESTED_VIBE_WEIGHTS)."""
    if not item.vibes:
        return UNKNOWN
    best = 0.0
    for rank, vibe in enumerate(wanted[:len(REQUESTED_VIBE_WEIGHTS)]):
        if vibe in item.vibes:
            best = max(best, REQUESTED_VIBE_WEIGHTS[rank] * (1.0 if item.vibes[0] == vibe else 0.6))
    return best


def formality_match(item: Item, target: int) -> float:
    """1 at the target, 0.5 one step away, 0 two or more away."""
    if item.formality is None:
        return UNKNOWN
    return max(0.0, 1 - abs(item.formality - target) / 2)


def season_match(item: Item, season: str) -> float:
    if not item.seasons:
        return UNKNOWN
    return 1.0 if season in item.seasons else 0.2


def item_terms(item: Item, request: ParsedRequest, relevance: float | None, formality: int | None) -> dict[str, float]:
    """The per-item score terms that apply to this request. `relevance` is the item's CLIP
    similarity already scaled to [0, 1] (see relevance_scores); `formality` is the target
    after any nudges."""
    terms = {}
    if relevance is not None:
        terms["relevance"] = relevance
    if request.vibes:
        terms["vibe"] = vibe_match(item, request.vibes)
    if formality is not None:
        terms["formality"] = formality_match(item, formality)
    if request.season:
        terms["season"] = season_match(item, request.season)
    return terms


def relevance_scores(items: list[Item], query: np.ndarray | None) -> dict[str, float]:
    """CLIP cosine similarity to the query, min-max scaled within these items to [0, 1].
    Raw text-to-image similarities sit in a narrow band (about 0.15-0.35), so unscaled they'd
    barely move the total. Pass a whole slot, not a filtered shortlist, so a score doesn't
    change when other items are rejected. Items without an embedding get UNKNOWN. Empty if
    there's no query."""
    if query is None:
        return {}
    with_vec = [item for item in items if item.embedding is not None]
    scores = {item.item_id: UNKNOWN for item in items}
    if with_vec:
        sims = np.stack([item.embedding for item in with_vec]) @ query
        low, high = float(sims.min()), float(sims.max())
        for item, sim in zip(with_vec, sims):
            scores[item.item_id] = (float(sim) - low) / (high - low) if high > low else 1.0
    return scores


def weighted(terms: dict[str, float]) -> float:
    total = sum(WEIGHTS[t] for t in terms)
    return sum(WEIGHTS[t] * v for t, v in terms.items()) / total if total else 0.0


# ---------------------------------------------------------------- outfits

def outfit_breakdown(item_scores: list[dict[str, float]], items: list[Item]) -> dict[str, float]:
    """Each item term averaged over the pieces, plus color harmony across them."""
    breakdown = {}
    for term in ITEM_TERMS:
        values = [scores[term] for scores in item_scores if term in scores]
        if values:
            breakdown[term] = sum(values) / len(values)
    breakdown["color"] = color_harmony(items)
    return breakdown
