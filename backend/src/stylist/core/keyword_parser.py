"""Turn a chat message into a ParsedRequest or a FeedbackIntent with keyword rules (FR-REQ-2).

This is the fallback for when the LLM parser fails or there's no API key, and the parser the
CLI uses until the LLM one exists. It only knows the words listed here, so it is predictable
rather than clever: anything it doesn't recognize is still embedded with CLIP, so relevance
covers what the keywords miss.

With an outfit on screen, a message is read as feedback first ("different shoes",
"dressier", "keep the coat"). If no feedback rule matches, it is a new request.
"""

from __future__ import annotations

import re

from stylist.core.models import FeedbackIntent, ParsedRequest

SLOT_WORDS = {
    "top": ["top", "shirt", "sweater", "tee", "t-shirt", "blouse", "hoodie", "cardigan", "tank", "bodysuit",
            "sweatshirt", "turtleneck", "polo", "cami", "camisole"],
    "bottom": ["bottoms", "bottom", "pants", "pant", "jeans", "jean", "skirt", "trousers", "shorts", "leggings",
               "sweatpants", "joggers"],
    "dress": ["dress", "jumpsuit", "romper"],
    "outerwear": ["coat", "jacket", "blazer", "trench", "vest", "outerwear", "layer", "parka", "puffer"],
    "shoes": ["shoes", "shoe", "sneakers", "sneaker", "loafers", "loafer", "boots", "boot", "heels", "flats", "sandals"],
    "accessory": ["accessory", "bag", "tote", "scarf", "belt", "hat", "beanie", "fedora", "purse"],
}
_WORD_TO_SLOT = {w: slot for slot, words in SLOT_WORDS.items() for w in words}
_SLOT = r"(?P<noun>" + "|".join(sorted(map(re.escape, _WORD_TO_SLOT), key=len, reverse=True)) + r")\b"
_DET = r"(?:(?:the|that|this|those|these|my|a|an)\s+)?"

VIBE_WORDS = {
    "cozy": ["cozy", "cosy", "comfy", "snug", "lounge"],
    "polished": ["polished", "office", "work", "meeting", "professional", "interview", "business"],
    "going-out": ["going out", "going-out", "party", "night out", "date", "dinner", "birthday", "rooftop", "club",
                  "drinks", "concert"],
    "cute": ["cute", "playful", "fun", "girly"],
    "off-duty": ["off duty", "off-duty", "errands", "weekend", "casual", "sunday", "saturday", "brunch", "laid back",
                 "laid-back", "everyday"],
    "chic": ["chic", "elegant", "elevated", "sophisticated", "luxe"],
    "athleisure": ["athleisure", "sporty", "gym", "workout", "yoga", "pilates", "hike", "run"],
    "minimal": ["minimal", "minimalist", "simple", "clean", "basic"],
    "romantic": ["romantic", "floral", "feminine", "wedding", "flowy"],
    "edgy": ["edgy", "leather", "grunge", "moody"],
}
# Comparatives used in nudges ("make it edgier").
VIBE_COMPARATIVES = {"cozier": "cozy", "comfier": "cozy", "cuter": "cute", "edgier": "edgy", "sportier": "athleisure",
                     "simpler": "minimal", "chicer": "chic"}

# An explicit style word sets the formality; otherwise the most formal occasion mentioned does.
STYLE_FORMALITY = {"black tie": 5, "formal": 5, "gala": 5, "dressy": 4, "smart casual": 3, "casual": 2,
                   "lounging": 1, "lounge": 1}
OCCASION_FORMALITY = {"wedding": 5, "interview": 4, "office": 4, "work": 4, "meeting": 4, "business": 4, "dinner": 3,
                      "date": 3, "birthday": 3, "party": 3, "rooftop": 3, "brunch": 2, "errands": 2, "weekend": 2,
                      "sunday": 2, "saturday": 2, "gym": 1, "workout": 1, "yoga": 1, "hike": 1}

SEASON_WORDS = {"spring": "spring", "summer": "summer", "fall": "fall", "autumn": "fall", "winter": "winter"}
WEATHER_PATTERNS = {
    "cold": r"\b(?:cold|freezing|frigid|snow(?:y|ing)?)\b",
    "cool": r"\b(?:chilly|brisk|cool (?:day|evening|night|weather))\b",
    "warm": r"\bwarm (?:day|evening|night|weather)\b",
    "hot": r"\b(?:hot|heat(?:wave)?|humid|scorching)\b",
    "rainy": r"\b(?:rain(?:y|ing)?|drizzl\w*|storm\w*|wet)\b",
}

# Product names: "build something around the Effortless Pant", "style my Only Coat".
_NAMED = re.compile(r"\b(?:around|featuring|styl(?:e|ing)|based on|pair(?:ed|ing)? with)\s+" + _DET +
                    r"(?P<name>[^.,!?;]+?)\s*(?:[.,!?;]|\band\b|\bfor\b|$)", re.IGNORECASE)
# "with the ..." only counts when it names a kind of garment ("dinner with the girls" doesn't).
_WITH_NAMED = re.compile(r"\bwith\s+(?:the|my)\s+(?P<name>[^.,!?;]+?)\s*(?:[.,!?;]|\band\b|\bfor\b|$)", re.IGNORECASE)

_ACCEPT = re.compile(r"\b(?:perfect|love it|i'?ll take it|that'?s it|looks (?:great|good)|accept|done|sold)\b")
_DRESSIER = re.compile(r"\b(?:dressier|fancier|more (?:formal|dressy|elevated)|less casual|dress (?:it|this) up)\b")
_RELAXED = re.compile(r"\b(?:more (?:relaxed|casual|comfortable)|less (?:formal|dressy)|dress (?:it|this) down)\b")
_UNLOCK = re.compile(r"\b(?:unlock|unpin|let go of)\s+" + _DET + _SLOT)
_LOCK = re.compile(r"\b(?:keep|lock|love)\s+" + _DET + _SLOT)  # not "like": "I'd like a jacket" isn't a lock
_REJECT = re.compile(r"\b(?:not|no|hate|don'?t like|remove|ditch|lose|drop|not a fan of)\s+" + _DET + _SLOT)
_SWAP = re.compile(r"\b(?:different|another|other|new|swap|change|switch|replace)\s+(?:out\s+)?" + _DET + _SLOT)


def _has(text: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


def parse_feedback(text: str) -> FeedbackIntent | None:
    """A refinement of the outfit on screen, or None if the message isn't one."""
    t = text.lower()
    for pattern, action in ((_UNLOCK, "unlock"), (_REJECT, "reject"), (_SWAP, "swap"), (_LOCK, "lock")):
        if match := pattern.search(t):
            return FeedbackIntent(action=action, slot=_WORD_TO_SLOT[match.group("noun")])
    if _DRESSIER.search(t):
        return FeedbackIntent(action="nudge", formality=1)
    if _RELAXED.search(t):
        return FeedbackIntent(action="nudge", formality=-1)
    for word, vibe in VIBE_COMPARATIVES.items():
        if _has(t, word):
            return FeedbackIntent(action="nudge", vibe=vibe)
    if match := re.search(r"\bmore\s+(" + "|".join(map(re.escape, VIBE_WORDS)) + r"|sporty|off duty|going out)\b", t):
        vibe = {"sporty": "athleisure", "off duty": "off-duty", "going out": "going-out"}.get(match.group(1), match.group(1))
        return FeedbackIntent(action="nudge", vibe=vibe)
    if _ACCEPT.search(t):
        return FeedbackIntent(action="accept")
    return None


def named_items(text: str) -> list[str]:
    names = [m.group("name").strip() for m in _NAMED.finditer(text)]
    for m in _WITH_NAMED.finditer(text):
        if re.search(_SLOT, m.group("name").lower()) and m.group("name").strip() not in names:
            names.append(m.group("name").strip())
    filler = {"something", "it", "this", "that", "for", "to", "of", "in", "on", "at", "me"}
    return [n for n in names if n and n.lower().split()[0] not in filler]


def parse_request(text: str) -> ParsedRequest:
    t = text.lower()
    found = [(t.find(w), vibe) for vibe, words in VIBE_WORDS.items() for w in words if _has(t, w)]
    vibes = list(dict.fromkeys(vibe for _, vibe in sorted(found)))[:3]  # in the order they're mentioned

    styles = [level for word, level in STYLE_FORMALITY.items() if _has(t, word)]
    occasions = [level for word, level in OCCASION_FORMALITY.items() if _has(t, word)]
    formality = styles[0] if styles else (max(occasions) if occasions else None)

    seasons = [season for word, season in SEASON_WORDS.items() if _has(t, word)]
    weather = [w for w, pattern in WEATHER_PATTERNS.items() if re.search(pattern, t)]
    return ParsedRequest(text=text.strip(), vibes=vibes, formality=formality, season=seasons[0] if seasons else None,
                         weather=weather, named_items=named_items(text))


def parse(text: str, has_outfit: bool) -> ParsedRequest | FeedbackIntent:
    """Feedback if there's an outfit to refine and the message reads as feedback; else a request."""
    if has_outfit and (intent := parse_feedback(text)):
        return intent
    return parse_request(text)
