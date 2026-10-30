"""Turn a chat message into a ParsedRequest or a FeedbackIntent with keyword rules (FR-REQ-2).

This is the fallback for when the LLM parser fails or there's no API key, and the parser the
CLI uses until the LLM one exists. It only knows the words listed here, so it is predictable
rather than clever: anything it doesn't recognize is still embedded with CLIP, so relevance
covers what the keywords miss.

With an outfit on screen, a message is read as feedback first ("different shoes",
"dressier", "keep the coat"). If no feedback rule matches, it is a new request.

Prices given as limits set a budget (FR-REQ-5): "under $200" for the whole outfit, "nothing over
$80" for each piece. With an outfit on screen, a budget on its own changes the budget, and
"cheaper" asks for a cheaper piece or a cheaper outfit (FR-FB-8, -9).
"""

from __future__ import annotations

import re

from stylist.core.models import FeedbackIntent, ParsedRequest
from stylist.tagging.colors import COLOR_WORDS

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

# Colors for one piece ("black shoes", "shoes in black"), with the tagger's color words.
_COLOR_TAG = {word: tag for tag, words in COLOR_WORDS.items() for word in words}
_COLOR_WORD = "|".join(sorted(map(re.escape, _COLOR_TAG), key=len, reverse=True))
_COLOR = r"(?P<color>" + _COLOR_WORD + r")"
_STYLE = (r"(?:(?:leather|suede|knit|satin|silk|wool|ankle|chunky|strappy|mini|midi|maxi|long|short|cropped|"
          r"wide-leg|straight-leg)\s+)?")
_ADJ = r"(?:(?:" + _COLOR_WORD + r")\s+)?" + _STYLE  # "keep the black shoes" is still a lock
_COLOR_THEN_SLOT = re.compile(r"\b" + _COLOR + r"\s+" + _STYLE + _SLOT)  # "black shoes", "black leather boots"
_SLOT_THEN_COLOR = re.compile(r"\b" + _SLOT + r"\s+(?:(?:in|be|to|that are|that is)\s+)?" + _COLOR + r"\b")
_LOOSE_COLOR = re.compile(r"\b" + _COLOR + r"\b")  # "swap the shoes for black ones"
_ANY_COLOR = re.compile(r"\b(?:any|whatever|other|different)\s+colou?r(?:ed)?\b|\bin\s+(?:any|another|a different)\s+colou?r\b")

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

# Budgets (FR-REQ-5): "$200", "200 dollars", "200 bucks", or a bare number right after a budget
# word ("keep it under 200"), but not "under 20 degrees".
_MONEY = re.compile(
    r"\$\s?(?P<dollar>\d[\d,]*(?:\.\d{1,2})?)"
    r"|\b(?P<unit>\d[\d,]*(?:\.\d{1,2})?)\s*(?:dollars|bucks|usd)\b"
    r"|\b(?:under|below|less than|no more than|at most|max(?:imum)?|up to|budget(?: of| is)?)\s+(?P<bare>\d{2,}(?:\.\d{1,2})?)\b"
    r"(?!\s*(?:degrees|deg|°|f\b|c\b|min|minutes|hours?|km|miles|people|guests|%|percent|years?))")
# A price only counts as a budget when its clause says it's a limit ("I got a $50 gift card" isn't).
_LIMIT_CUE = re.compile(r"\b(?:under|below|less than|no more than|not more than|at most|max(?:imum)?|up to|within|"
                        r"budget|spend(?:ing)?|cheaper than|nothing (?:over|above|more than)|"
                        r"no (?:one )?(?:piece|item) (?:over|above)|or (?:less|under|below)|tops|limit|cap)\b")
_PER_PIECE_CUE = re.compile(r"\b(?:each|apiece|a piece|per (?:piece|item)|every (?:piece|item)|"
                            r"nothing (?:over|above|more than)|no (?:one )?(?:piece|item)|any (?:one )?(?:piece|item)|"
                            r"single (?:piece|item))\b")
_NO_BUDGET = re.compile(r"\b(?:no budget|forget (?:about )?the budget|ignore the budget|drop the budget|"
                        r"remove the budget|price doesn'?t matter|money is no object|don'?t care (?:about|what) "
                        r"(?:the )?(?:price|it costs)|any price)\b")
_CHEAPER = re.compile(r"\b(?:cheaper|less expensive|more affordable|budget[- ]friendly|lower[- ]priced|"
                      r"cost(?:s)? less|too (?:expensive|pricey)|over ?priced)\b")
# Each price is read with its own part of the sentence: "under $300 total but nothing over $100 each".
_CLAUSE_BREAK = re.compile(r"[.,;!?\n]|\b(?:but|and|while|though|although)\b")


def budget(text: str) -> tuple[float | None, float | None]:
    """(max_total, max_item) from the prices a message gives as limits; None when it gives none."""
    t = text.lower()
    total: float | None = None
    each: float | None = None
    for match in _MONEY.finditer(t):
        amount = float((match.group("dollar") or match.group("unit") or match.group("bare")).replace(",", ""))
        start = max((b.end() for b in _CLAUSE_BREAK.finditer(t, 0, match.start())), default=0)
        end = next((b.start() for b in _CLAUSE_BREAK.finditer(t, match.end())), len(t))
        clause = t[start:end]
        if amount <= 0 or not (_LIMIT_CUE.search(clause) or match.group("bare")):
            continue
        if _PER_PIECE_CUE.search(clause):
            each = amount if each is None else min(each, amount)
        else:
            total = amount if total is None else min(total, amount)
    return total, each


def budget_feedback(text: str) -> FeedbackIntent | None:
    """A budget change for the outfit on screen: clear it, set it, or make something cheaper."""
    t = text.lower()
    if _NO_BUDGET.search(t):
        return FeedbackIntent(action="budget")  # neither limit: clears the budget
    max_total, max_item = budget(t)
    if max_total or max_item:
        request = parse_request(text)
        if request.vibes or request.formality or request.season or request.weather or request.named_items:
            return None  # a budget for a new request ("a wedding instead, under $300")
        return FeedbackIntent(action="budget", max_total=max_total, max_item=max_item)
    if _CHEAPER.search(t):
        slots = {_WORD_TO_SLOT[m.group("noun")] for m in re.finditer(r"\b" + _SLOT, t)}
        if len(slots) == 1:  # "cheaper shoes", "swap the coat for something cheaper"
            return FeedbackIntent(action="cheaper", slot=slots.pop())
        return FeedbackIntent(action="cheaper")
    return None


_UNLOCK = re.compile(r"\b(?:unlock|unpin|let go of)\s+" + _DET + _ADJ + _SLOT)
_LOCK = re.compile(r"\b(?:keep|lock|love)\s+" + _DET + _ADJ + _SLOT)  # not "like": "I'd like a jacket" isn't a lock
_REJECT = re.compile(r"\b(?:not|no|hate|don'?t like|remove|ditch|lose|drop|not a fan of)\s+" + _DET + _ADJ + _SLOT)
_SWAP = re.compile(r"\b(?:different|another|other|new|swap|change|switch|replace)\s+(?:out\s+)?" + _DET + _ADJ + _SLOT)


def slot_colors(text: str) -> dict[str, str]:
    """The colors asked for particular pieces: slot -> color tag ("black shoes" gives shoes: black)."""
    t = text.lower()
    found: dict[str, str] = {}
    for pattern in (_COLOR_THEN_SLOT, _SLOT_THEN_COLOR):
        for match in pattern.finditer(t):
            found.setdefault(_WORD_TO_SLOT[match.group("noun")], _COLOR_TAG[match.group("color")])
    return found


def _has(text: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


def parse_feedback(text: str) -> FeedbackIntent | None:
    """A refinement of the outfit on screen, or None if the message isn't one."""
    t = text.lower()
    if intent := budget_feedback(text):  # before swap: "swap the coat for something cheaper" is a cheaper coat
        return intent
    for pattern, action in ((_UNLOCK, "unlock"), (_REJECT, "reject"), (_SWAP, "swap"), (_LOCK, "lock")):
        if match := pattern.search(t):
            slot = _WORD_TO_SLOT[match.group("noun")]
            if action != "swap":
                return FeedbackIntent(action=action, slot=slot)
            loose = _LOOSE_COLOR.search(t)  # "swap the shoes for black ones"
            color = "any" if _ANY_COLOR.search(t) else slot_colors(t).get(slot) or (loose and _COLOR_TAG[loose.group("color")])
            return FeedbackIntent(action="swap", slot=slot, color=color or None)
    if colors := slot_colors(t):  # "black shoes", "can I get the coat in navy"
        rest = _SLOT_THEN_COLOR.sub(" ", _COLOR_THEN_SLOT.sub(" ", t))  # "leather" in "black leather boots" isn't a vibe
        request = parse_request(rest)
        if not (request.vibes or request.formality or request.season or request.weather):
            slot, color = next(iter(colors.items()))
            return FeedbackIntent(action="swap", slot=slot, color=color)
    if (_ANY_COLOR.search(t) and (match := re.search(r"\b" + _SLOT, t))):  # "any color shoes"
        return FeedbackIntent(action="swap", slot=_WORD_TO_SLOT[match.group("noun")], color="any")
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
    max_total, max_item = budget(t)
    return ParsedRequest(text=text.strip(), vibes=vibes, formality=formality, season=seasons[0] if seasons else None,
                         weather=weather, named_items=named_items(text), max_total=max_total, max_item=max_item,
                         colors=slot_colors(t))


def parse(text: str, has_outfit: bool) -> ParsedRequest | FeedbackIntent:
    """Feedback if there's an outfit to refine and the message reads as feedback; else a request."""
    if has_outfit and (intent := parse_feedback(text)):
        return intent
    return parse_request(text)
