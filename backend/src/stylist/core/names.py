"""Resolve a typed product name to a catalog product (FR-NAME-1, -2, -3).

Both the query and each product name go through the same clean-up as product grouping
(core.catalog.product_key), then become word lists: "the"/"a" are dropped, plurals are made
singular ("pants" -> "pant"), common spellings are unified ("low" -> "lo", as in "Lo-Rise"),
and misspellings of "Aritzia" are dropped like the brand name itself.

Each typed word is matched to its closest word in the name (rapidfuzz ratio), so typos still
count. Two typed words may also match one name word, or the reverse, which handles
"sweat fleece" vs "Sweatfleece" and "vneck" vs "V-neck". The score is mostly how much of
the query the name covers, so the words someone typed have to be there; a smaller part is
how much of the name the query covers, which ranks the closest variant first. (A symmetric
measure would give a short name like "Cashmere Scarf" full marks for any query that
contains it.)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from stylist.core.catalog import Catalog, product_key
from stylist.core.models import Item

MATCH_THRESHOLD = 75  # below this, nothing matches (FR-NAME-2)
AMBIGUITY_MARGIN = 5  # a runner-up this close to the best also counts as a match (FR-NAME-3)
MAX_SUGGESTIONS = 3

QUERY_COVERAGE_WEIGHT = 0.7  # the rest goes to name coverage
WORD_MATCH = 0.7  # a word that matches less well than this counts as missing
JOINED_MATCH = 0.8  # two typed words must match this well to count as one name word
STOPWORDS = {"the", "a", "an"}
SPELLINGS = {"low": "lo", "high": "hi"}  # how the catalog spells them ("Lo-Rise", "Hi-rise")


@dataclass
class Resolution:
    item: Item | None  # the best match, or None if nothing is close enough
    score: float
    choices: list[Item] = field(default_factory=list)  # near-ties with the best match, best first
    suggestions: list[Item] = field(default_factory=list)  # closest products when nothing matched

    @property
    def ambiguous(self) -> bool:
        return len(self.choices) > 1


def words(text: str) -> list[str]:
    out = []
    for word in product_key(text).split():
        if word in STOPWORDS or fuzz.ratio(word, "aritzia") >= 80:  # "artizia" is still the brand
            continue
        word = SPELLINGS.get(word, word)
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]  # applied to both sides, so "tennis" -> "tenni" still matches itself
        out.append(word)
    return out


def _best(word: str, candidates: list[str]) -> float:
    return max((fuzz.ratio(word, c) for c in candidates), default=0.0) / 100


def coverage(source: list[str], target: list[str]) -> float:
    """How much of `source` is found in `target`, from 0 to 1. Typos earn partial credit;
    two adjacent source words can match one target word, and one source word can match two
    adjacent target words joined together."""
    if not source:
        return 0.0
    candidates = target + [a + b for a, b in zip(target, target[1:])]
    total, i = 0.0, 0
    while i < len(source):
        single = _best(source[i], candidates)
        if i + 1 < len(source):
            joined = _best(source[i] + source[i + 1], candidates)
            if joined > single and joined >= JOINED_MATCH:
                total, i = total + 2 * joined, i + 2
                continue
        total, i = total + (single if single >= WORD_MATCH else 0.0), i + 1
    return total / len(source)


def similarity(query_words: list[str], name_words: list[str]) -> float:
    """0-100: mostly how much of the query is in the name, partly how much of the name is in the query."""
    return 100 * (QUERY_COVERAGE_WEIGHT * coverage(query_words, name_words)
                  + (1 - QUERY_COVERAGE_WEIGHT) * coverage(name_words, query_words))


def representatives(catalog: Catalog, slot: str | None = None) -> list[Item]:
    """One listing per product (the first by item_id), so colorways don't count as ties."""
    seen: dict[str, Item] = {}
    for item in sorted(catalog.items.values(), key=lambda i: i.item_id):
        if slot is None or item.slot == slot:
            seen.setdefault(item.product_key, item)
    return list(seen.values())


def resolve(catalog: Catalog, query: str, slot: str | None = None) -> Resolution:
    """The product a user meant by `query`, optionally only within one slot."""
    query_words = words(query)
    ranked = sorted(((similarity(query_words, words(item.name)), item) for item in representatives(catalog, slot)),
                    key=lambda pair: (-pair[0], pair[1].product_key))
    if not ranked or not query_words:
        return Resolution(item=None, score=0.0)

    best_score, best = ranked[0]
    if best_score < MATCH_THRESHOLD:
        return Resolution(item=None, score=round(best_score, 1),
                          suggestions=[item for _, item in ranked[:MAX_SUGGESTIONS]])
    if best_score == 100:  # the full name, typed exactly: never ask "which one?"
        return Resolution(item=best, score=100.0, choices=[best])
    choices = [item for score, item in ranked if score >= max(MATCH_THRESHOLD, best_score - AMBIGUITY_MARGIN)]
    return Resolution(item=best, score=round(best_score, 1), choices=choices[:MAX_SUGGESTIONS])
