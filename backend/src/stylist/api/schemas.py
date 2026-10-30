"""Request and response bodies for /api/v1. The React client is typed against these."""

from __future__ import annotations

from pydantic import BaseModel, Field

from stylist.core.feedback import SessionState, over_budget
from stylist.core.models import Item, Outfit
from stylist.core.names import Resolution
from stylist.core.session import Reply


class ItemOut(BaseModel):
    item_id: str
    slot: str
    name: str
    brand_line: str
    price: float
    currency: str
    image_url: str
    product_url: str
    vibes: list[str]
    formality: int | None
    colors: list[str]

    @classmethod
    def of(cls, item: Item) -> ItemOut:
        return cls(item_id=item.item_id, slot=item.slot, name=item.name, brand_line=item.brand_line,
                   price=item.price, currency=item.currency, image_url=item.image_url,
                   product_url=item.product_url, vibes=list(item.vibes), formality=item.formality,
                   colors=list(item.colors))


class PieceOut(ItemOut):
    locked: bool


class BudgetOut(BaseModel):
    max_total: float | None  # the whole outfit
    max_item: float | None  # each piece

    @classmethod
    def of(cls, state: SessionState | None) -> BudgetOut | None:
        request = state.request if state else None
        return cls(max_total=request.max_total, max_item=request.max_item) if request and request.has_budget else None


class OutfitOut(BaseModel):
    pieces: list[PieceOut]  # in slot order: top, bottom, dress, outerwear, shoes, accessory
    score: float
    breakdown: dict[str, float]  # score term -> value in [0, 1] (FR-GEN-3)
    total_price: float
    currency: str
    budget: BudgetOut | None  # the session's budget, if it has one (FR-REQ-5)
    over_budget: bool  # nothing fit the budget, so this is the closest outfit

    @classmethod
    def of(cls, outfit: Outfit, state: SessionState | None) -> OutfitOut:
        locked = state.locked if state else {}
        pieces = [PieceOut(**ItemOut.of(item).model_dump(), locked=locked.get(slot) == item.item_id)
                  for slot, item in outfit.pieces.items()]
        currency = pieces[0].currency if pieces else "USD"
        request = state.request if state else None
        return cls(pieces=pieces, score=outfit.score, breakdown=outfit.breakdown,
                   total_price=outfit.total_price, currency=currency, budget=BudgetOut.of(state),
                   over_budget=bool(request and over_budget(outfit, request, locked)))


class TurnOut(BaseModel):
    """The reply to a message or an action."""

    session_id: str
    outfit: OutfitOut | None
    notes: list[str]  # things to tell the user: no match, ambiguous names, errors
    choices: dict[str, list[ItemOut]]  # typed name -> close matches (FR-NAME-3)
    accepted: bool
    summary: dict | None  # links and total price, once accepted (FR-FB-5)
    parsed: dict | None  # what the message was understood as
    error: str | None  # set when a message's change couldn't be applied; the outfit is unchanged
    budget: BudgetOut | None  # the session's budget, also before there's an outfit
    omitted: list[str]  # optional slots the user asked to leave out

    @classmethod
    def of(cls, session_id: str, reply: Reply) -> TurnOut:
        return cls(
            session_id=session_id,
            budget=BudgetOut.of(reply.state),
            omitted=sorted(reply.state.omitted) if reply.state else [],
            outfit=OutfitOut.of(reply.outfit, reply.state) if reply.outfit else None,
            notes=reply.notes,
            choices={name: [ItemOut.of(i) for i in items] for name, items in reply.choices.items()},
            accepted=bool(reply.state and reply.state.accepted),
            summary=reply.summary,
            parsed=reply.parsed.model_dump(mode="json") if reply.parsed else None,
            error=reply.error,
        )


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class SessionCreated(BaseModel):
    session_id: str


class TurnSummary(BaseModel):
    kind: str
    input: dict
    parsed: dict | None  # what a message was understood as, so a reloaded chat can word replies right
    error: str | None
    outfit: dict | None
    notes: list[str]
    latency_ms: float


class SessionOut(BaseModel):
    session_id: str
    outfit: OutfitOut | None
    accepted: bool
    budget: BudgetOut | None  # the session's budget, also before there's an outfit
    omitted: list[str]  # optional slots the user asked to leave out
    turns: list[TurnSummary]


class ResolutionOut(BaseModel):
    match: ItemOut | None
    score: float
    choices: list[ItemOut]
    suggestions: list[ItemOut]

    @classmethod
    def of(cls, found: Resolution) -> ResolutionOut:
        return cls(match=ItemOut.of(found.item) if found.item else None, score=found.score,
                   choices=[ItemOut.of(i) for i in found.choices] if found.ambiguous else [],
                   suggestions=[ItemOut.of(i) for i in found.suggestions])
