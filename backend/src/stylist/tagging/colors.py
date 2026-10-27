"""Assign each product its main color tag from the color taxonomy (PRD Appendix A).

Two signals, in order:
1. The image alt text, e.g. "Flat lay of the navy blue cable knit sweater". On-model shots
   name other garments too ("black tank top and beige wide-leg pants" for the pants), so
   the color is read from the words just before the noun for the item's own slot, back to
   the nearest "and" / "with" / comma. If that noun isn't there, the first color word
   before " with " is used. A pattern word (striped, floral...) in that phrase means `multi`.
2. Channel3's dominant hex color, mapped to the nearest taxonomy color in CIELAB space,
   where straight-line distance roughly matches how different two colors look to a person.
   It is measured over the whole photo, so a model's trousers or the background can win;
   that's why it's only the fallback. It never returns `metallic` or `multi`.
"""

from __future__ import annotations

import math
import re

# Color words that map to each tag (from the "Covers" column of Appendix A).
COLOR_WORDS: dict[str, list[str]] = {
    "black": ["black", "jet", "onyx"],
    "white": ["white"],
    "cream": ["cream", "ivory", "off-white", "oat", "oatmeal", "bone", "ecru"],
    "beige": ["beige", "sand", "stone", "taupe", "khaki"],
    "camel": ["camel", "tan", "cognac", "caramel"],
    "brown": ["brown", "chocolate", "mocha", "espresso", "chestnut"],
    "grey": ["grey", "gray", "heather", "charcoal", "slate"],
    "navy": ["navy", "midnight"],
    "denim": ["denim", "wash", "indigo", "chambray"],
    "olive": ["olive", "army", "moss"],
    "red": ["red", "cherry", "tomato", "scarlet"],
    "burgundy": ["burgundy", "wine", "oxblood", "merlot", "maroon"],
    "pink": ["pink", "blush", "rose", "fuchsia", "magenta"],
    "orange": ["orange", "rust", "terracotta", "coral"],
    "yellow": ["yellow", "butter", "mustard", "lemon"],
    "green": ["green", "sage", "mint", "emerald", "forest"],
    "teal": ["teal", "turquoise", "aqua"],
    "blue": ["blue", "cobalt", "sky", "powder"],
    "purple": ["purple", "lilac", "lavender", "plum", "violet"],
    "metallic": ["gold", "silver", "bronze", "pewter", "metallic"],
    "multi": ["striped", "stripes", "floral", "plaid", "printed", "print", "checkered", "gingham",
              "leopard", "polka"],
}
_WORD_TO_TAG = {word: tag for tag, words in COLOR_WORDS.items() for word in words}
_COLOR_WORD_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _WORD_TO_TAG), key=len, reverse=True)) + r")\b",
                            re.IGNORECASE)

# Several reference shades per tag, so e.g. both "sage" and "emerald" land on green.
REFERENCE_SHADES: dict[str, list[str]] = {
    "black": ["#000000", "#1a1a1a", "#262626"],
    "white": ["#ffffff", "#f7f7f5"],
    "cream": ["#f3ead8", "#ece2cc", "#e6dcc8"],
    "beige": ["#d8c8b0", "#c8b89a", "#b9a995"],
    "camel": ["#c19a6b", "#b5835a", "#a0703f"],
    "brown": ["#6b4423", "#4a2c1a", "#7b5b44"],
    "grey": ["#808080", "#a9a9a9", "#5a5a5a", "#c8c8c8"],
    "navy": ["#1f2a44", "#14213d", "#2b3550"],
    "denim": ["#5b7fa6", "#3e5f8a", "#8aa9c9"],
    "olive": ["#6b6b3a", "#556b2f", "#7a7a52"],
    "red": ["#c0392b", "#d62828", "#a52a2a"],
    "burgundy": ["#6d1a2a", "#800020", "#5a1f2b"],
    "pink": ["#f4a6b8", "#e75480", "#f8d0da", "#d1477a"],
    "orange": ["#e67e22", "#c65d3b", "#ff7f50"],
    "yellow": ["#f4d03f", "#f6e7a1", "#d4a017"],
    "green": ["#2e8b57", "#9caf88", "#50c878", "#1f5f3f"],
    "teal": ["#008080", "#40c0c0", "#1f6f6f"],
    "blue": ["#2a6fdb", "#87ceeb", "#0047ab", "#a7c7e7"],
    "purple": ["#7d3c98", "#c8a2c8", "#b57edc", "#5d3954"],
}


def hex_to_lab(hex_color: str) -> tuple[float, float, float]:
    """sRGB hex (e.g. "#2e384d") to CIELAB (D65 white point)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"not a 6-digit hex color: {hex_color!r}")

    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


_REFERENCE_LAB = [(tag, hex_to_lab(shade)) for tag, shades in REFERENCE_SHADES.items() for shade in shades]


def nearest_color(hex_color: str | None) -> str | None:
    """The taxonomy tag closest to this hex color, or None if there is no color."""
    if not hex_color:
        return None
    lab = hex_to_lab(hex_color)
    return min(_REFERENCE_LAB, key=lambda ref: math.dist(lab, ref[1]))[0]


SLOT_NOUNS: dict[str, str] = {
    "top": r"sweaters?|tops?|tees?|t-shirts?|shirts?|tanks?|camisoles?|camis?|bodysuits?|hoodies?|"
           r"sweatshirts?|cardigans?|turtlenecks?|longsleeves?|blouses?|polos?|mocknecks?|bustiers?|corsets?",
    "bottom": r"pants?|trousers|jeans?|skirts?|shorts|leggings?|sweatpants?|joggers?|chinos?",
    "dress": r"dress(?:es)?|jumpsuits?|rompers?",
    "outerwear": r"coats?|jackets?|blazers?|parkas?|puffers?|trench|vests?|shackets?",
    "shoes": r"shoes?|boots?|sneakers?|loafers?|heels?|sandals?|flats?(?!\s+lay)|mules?|joggers?",  # not "Flat lay"
    "accessory": r"bags?|totes?|belts?|scarf|scarves|hats?|beanies?|caps?|fedoras?|earrings?|necklaces?|gloves",
}
_PHRASE_BREAK = re.compile(r",|\band\b|\bwith\b|\bwearing\b|\bof\b", re.IGNORECASE)


def _tag_in(phrase: str) -> str | None:
    """`multi` if the phrase has a pattern word, else the first color word's tag."""
    tags = [_WORD_TO_TAG[m.group(1).lower()] for m in _COLOR_WORD_RE.finditer(phrase)]
    if "multi" in tags:
        return "multi"
    return tags[0] if tags else None


_ENDS_WITH_COLOR = re.compile(_COLOR_WORD_RE.pattern + r"\s*$", re.IGNORECASE)
_STARTS_WITH_COLOR = re.compile(r"^\s*" + _COLOR_WORD_RE.pattern, re.IGNORECASE)
# "Pant™ in beige twill", "Scarf in a muted brown print", "Scarf featuring an intricate beige..."
_IN_COLOR_AFTER_NOUN = re.compile(r"[^\w\s]*\s+(?:in|featuring)\s+(?:[\w-]+\s+){0,3}?" + _COLOR_WORD_RE.pattern,
                                  re.IGNORECASE)
_MATCHING = re.compile(r"\bmatching\b|\bset\b", re.IGNORECASE)


def _color_before_noun(before: str) -> str | None:
    """Color of the phrase that ends at the item's noun, back to the nearest "and"/"with"/comma.
    "black and white pants" is one two-color item, so it's `multi`, not `white`."""
    breaks = list(_PHRASE_BREAK.finditer(before))
    phrase = before[breaks[-1].end():] if breaks else before
    tag = _tag_in(phrase)
    if tag and breaks and breaks[-1].group().lower() == "and":
        joined_by_and = before[:breaks[-1].start()]
        if _ENDS_WITH_COLOR.search(joined_by_and) and _STARTS_WITH_COLOR.search(phrase):
            return "multi"
    return tag


def color_from_text(text: str | None, slot: str | None = None) -> str | None:
    """The product's color tag from alt text, or None if no color word applies."""
    if not text:
        return None
    if slot in SLOT_NOUNS:
        noun = re.search(rf"\b({SLOT_NOUNS[slot]})\b", text, re.IGNORECASE)
        if noun:
            before = text[:noun.start()]
            if tag := _color_before_noun(before):
                return tag
            after = text[noun.end():]
            if in_color := _IN_COLOR_AFTER_NOUN.match(after):  # "pants in navy"
                return _tag_in(_PHRASE_BREAK.split(after[in_color.start():])[0])
            # "...sweatshirt and matching sweatpants", "...top and pants set": the pieces match,
            # so the item has the color of the garment named first.
            if _MATCHING.search(_PHRASE_BREAK.split(before)[-1]) or _MATCHING.match(after.strip()):
                return _first_color_before_with(text)
            # Otherwise the item's color isn't named; other garments' colors don't count.
            return None
    return _first_color_before_with(text)


def _first_color_before_with(text: str) -> str | None:
    return _tag_in(re.split(r"\bwith\b", text, maxsplit=1, flags=re.IGNORECASE)[0])


def main_color(alt_text: str | None, hex_color: str | None, slot: str | None = None) -> tuple[str | None, str]:
    """(color tag, source), where source is "alt_text", "hex", or "none"."""
    if tag := color_from_text(alt_text, slot):
        return tag, "alt_text"
    if tag := nearest_color(hex_color):
        return tag, "hex"
    return None, "none"
