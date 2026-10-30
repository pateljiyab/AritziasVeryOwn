"""Types shared by the outfit engine. Plain data: no database, model, or network access."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

Slot = Literal["top", "bottom", "dress", "outerwear", "shoes", "accessory"]
SLOTS: list[Slot] = ["top", "bottom", "dress", "outerwear", "shoes", "accessory"]

Season = Literal["spring", "summer", "fall", "winter"]
Vibe = Literal["cozy", "polished", "going-out", "cute", "off-duty", "chic", "athleisure", "minimal", "romantic", "edgy"]
Weather = Literal["cold", "cool", "mild", "warm", "hot", "rainy"]
# The color tags items carry (tagging/colors.py, PRD Appendix A); "multi" covers prints.
Color = Literal["black", "white", "cream", "beige", "camel", "brown", "grey", "navy", "denim", "olive", "red",
                "burgundy", "pink", "orange", "yellow", "green", "teal", "blue", "purple", "metallic", "multi"]

# Weather that makes outerwear required (FR-REQ-4 names cold and rain; Flow B expects a
# layer for "might get chilly", so cool counts too).
OUTERWEAR_WEATHER = {"cold", "cool", "rainy"}


@dataclass(frozen=True, eq=False)
class Item:
    """One catalog listing with its tags and CLIP image embedding (None if it had no photo)."""

    item_id: str
    name: str
    brand_line: str
    slot: Slot
    price: float
    currency: str
    image_url: str
    product_url: str
    product_key: str  # listings of the same product (colorways, renamed copies) share this
    seasons: tuple[str, ...] = ()
    vibes: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    formality: int | None = None
    embedding: np.ndarray | None = None

    @property
    def main_color(self) -> str | None:
        return self.colors[0] if self.colors else None


class ParsedRequest(BaseModel):
    """What the user asked for, as structured constraints (FR-REQ-2). Produced by the LLM parser
    or the keyword fallback; every field is optional so a vague request still works."""

    text: str = ""  # embedded with CLIP for query relevance
    vibes: list[Vibe] = Field(default_factory=list)
    formality: int | None = Field(default=None, ge=1, le=5)
    season: Season | None = None
    weather: list[Weather] = Field(default_factory=list)
    named_items: list[str] = Field(default_factory=list)  # product names, resolved by core.names
    max_total: float | None = Field(default=None, gt=0)  # budget for the whole outfit ("under $200")
    max_item: float | None = Field(default=None, gt=0)  # most any one piece may cost ("nothing over $80")
    colors: dict[Slot, Color] = Field(default_factory=dict)  # slot -> the color asked for ("black shoes")

    @property
    def needs_outerwear(self) -> bool:
        return bool(OUTERWEAR_WEATHER.intersection(self.weather))

    @property
    def has_budget(self) -> bool:
        return self.max_total is not None or self.max_item is not None


class FeedbackIntent(BaseModel):
    """A change to the current outfit (Flow D), from a button or a parsed chat message (FR-FB-7).
    A piece is named by item_id (buttons) or by slot (chat: "keep the coat").

    budget sets the session's budget: either limit or both, and neither clears it. cheaper with a
    piece swaps that piece for a cheaper one; without one, it makes the whole outfit cheaper.
    swap with a color ("black shoes") swaps in a piece of that color and keeps to it; "any" drops it."""

    action: Literal["lock", "unlock", "reject", "swap", "nudge", "accept", "budget", "cheaper"]
    slot: Slot | None = None
    item_id: str | None = None
    formality: Literal[-1, 1] | None = None  # nudge: dressier (+1) or more relaxed (-1)
    vibe: Vibe | None = None  # nudge toward this vibe
    max_total: float | None = Field(default=None, gt=0)  # budget: the whole outfit
    max_item: float | None = Field(default=None, gt=0)  # budget: each piece
    color: Color | Literal["any"] | None = None  # swap: a piece in this color

    @model_validator(mode="after")
    def has_a_target(self) -> FeedbackIntent:
        if self.action in {"lock", "unlock", "reject", "swap"} and not (self.slot or self.item_id):
            raise ValueError(f"{self.action} needs a slot or an item_id")
        if self.color is not None and self.action != "swap":
            raise ValueError("only a swap takes a color")
        if self.action == "nudge" and self.formality is None and self.vibe is None:
            raise ValueError("nudge needs a formality direction or a vibe")
        return self


@dataclass
class Constraints:
    """Hard rules for one generation (FR-GEN-4)."""

    locked: dict[str, str] = field(default_factory=dict)  # slot -> item_id that must appear
    required: set[str] = field(default_factory=set)  # slots that must be filled (locked slots always are)
    rejected: set[str] = field(default_factory=set)  # product_keys that must not appear
    avoid: set[frozenset[str]] = field(default_factory=set)  # item_id sets already shown (FR-GEN-5)
    below: dict[str, float] = field(default_factory=dict)  # slot -> its piece must cost less than this
    empty: set[str] = field(default_factory=set)  # optional slots to leave out


@dataclass
class Outfit:
    pieces: dict[str, Item]  # slot -> item, in SLOTS order
    score: float
    breakdown: dict[str, float]  # score term -> value in [0, 1], for the API response (FR-GEN-3)

    @property
    def item_ids(self) -> frozenset[str]:
        return frozenset(item.item_id for item in self.pieces.values())

    @property
    def total_price(self) -> float:
        return round(sum(item.price for item in self.pieces.values()), 2)
