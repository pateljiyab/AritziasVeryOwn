"""The REST API (FR-API-1, -2): sessions, chat messages, button actions, and catalog lookups.

Run (from the repo root, after ingest.py and tag.py):
    uvicorn --factory --app-dir backend/src stylist.api.app:create_app --env-file .env --reload
Then open http://127.0.0.1:8000/docs for the OpenAPI docs.

Environment:
    ANTHROPIC_API_KEY        parse chat with Claude Haiku 5.5 (otherwise LLM_* or keyword rules;
                             see adapters/llm_parser.make_parser)
    STYLIST_NO_CLIP=1        don't load CLIP (no PyTorch needed; outfits skip query relevance)
    STYLIST_CORS_ORIGINS     comma-separated origins allowed to call the API
                             (default: the Vite dev server, http://localhost:5173)
    STYLIST_MESSAGES_PER_MINUTE
                             chat messages allowed per client IP per minute (default 20), so a
                             script can't spend the LLM budget. Behind a proxy, run uvicorn with
                             --proxy-headers so the client IP is the user's, not the proxy's.

Every turn is saved to data/sessions.db and logged as one JSON line (NFR-6).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from stylist.adapters.catalog_store import load_catalog
from stylist.adapters.clip_text import TextEmbedder
from stylist.adapters.llm_parser import make_parser
from stylist.adapters.session_store import SessionStore
from stylist.api.schemas import (
    BudgetOut, ItemOut, MessageIn, OutfitOut, ResolutionOut, SessionCreated, SessionOut, TurnOut, TurnSummary,
)
from stylist.core import keyword_parser
from stylist.core.catalog import Catalog
from stylist.core.feedback import SessionState, outfit_of
from stylist.core.models import FeedbackIntent, Slot
from stylist.core.names import resolve
from stylist.core.session import Embed, Parse, Reply, handle_action, handle_message

log = logging.getLogger("stylist.turns")
if not log.handlers:  # uvicorn only configures its own loggers; print one JSON line per turn to stderr
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False


class RateLimiter:
    """At most `limit` calls per `window` seconds for each key. In memory and per process, so it
    resets on restart: enough to stop one client from spending the LLM budget."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.calls: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self.clock()
        calls = self.calls[key]
        while calls and calls[0] <= now - self.window:
            calls.popleft()
        if len(calls) >= self.limit:
            return False
        calls.append(now)
        return True


def create_app(catalog: Catalog | None = None, embed: Embed | None = None, store: SessionStore | None = None,
               parse: Parse | None = None, limiter: RateLimiter | None = None) -> FastAPI:
    """The app. With no arguments it loads the real catalog, CLIP, data/sessions.db, and the
    parser from the environment; tests pass small fakes instead."""
    catalog = catalog if catalog is not None else load_catalog()
    embed = embed if embed is not None else TextEmbedder(enabled=os.environ.get("STYLIST_NO_CLIP") != "1")
    store = store if store is not None else SessionStore()
    parse = parse if parse is not None else make_parser()
    limiter = limiter or RateLimiter(int(os.environ.get("STYLIST_MESSAGES_PER_MINUTE", "20")))

    app = FastAPI(title="AI Outfit Stylist", version="0.3.0",
                  description="Conversational outfit stylist over the Aritzia catalog.")
    origins = os.environ.get("STYLIST_CORS_ORIGINS", "http://localhost:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins if o.strip()],
                       allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)

    api = APIRouter(prefix="/api/v1")

    def load_state(session_id: str) -> SessionState | None:
        try:
            return store.load(session_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No session {session_id}.") from None

    def run_turn(session_id: str, kind: str, given: dict, apply: Callable[[SessionState | None], Reply]) -> Reply:
        state = load_state(session_id)
        started = time.perf_counter()
        reply = apply(state)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        new_state = reply.state
        shown = {slot: item.item_id for slot, item in reply.outfit.pieces.items()} if reply.outfit else None
        turn = {
            "kind": kind, "input": given, "latency_ms": latency_ms, "outfit": shown, "notes": reply.notes,
            "parsed": reply.parsed.model_dump(mode="json") if reply.parsed else None,
            "locked": dict(new_state.locked) if new_state else {},
            "rejected": sorted(new_state.rejected) if new_state else [],
            "score": reply.outfit.score if reply.outfit else None,
            "breakdown": reply.outfit.breakdown if reply.outfit else None,
            "error": reply.error,
        }
        store.record(session_id, new_state, turn)
        log.info(json.dumps({"session_id": session_id, **turn}))
        return reply

    @api.get("/health")
    def health() -> dict:
        parser = getattr(parse, "name", "keyword rules" if parse is keyword_parser.parse else "custom")
        return {"status": "ok", "items": len(catalog), "parser": parser}

    @api.post("/sessions", status_code=status.HTTP_201_CREATED, response_model=SessionCreated)
    def create_session() -> SessionCreated:
        return SessionCreated(session_id=store.create())

    @api.get("/sessions/{session_id}", response_model=SessionOut)
    def get_session(session_id: str) -> SessionOut:
        state = load_state(session_id)
        outfit = OutfitOut.of(outfit_of(catalog, state), state) if state and state.current else None
        turns = [TurnSummary(kind=t.kind, input=t.input, parsed=t.parsed, error=t.error, outfit=t.outfit,
                             notes=t.notes, latency_ms=t.latency_ms)
                 for t in store.turns(session_id)]
        return SessionOut(session_id=session_id, outfit=outfit, accepted=bool(state and state.accepted),
                          budget=BudgetOut.of(state), omitted=sorted(state.omitted) if state else [], turns=turns)

    @api.post("/sessions/{session_id}/messages", response_model=TurnOut,
              responses={429: {"description": "Too many messages from this client"}})
    def send_message(session_id: str, body: MessageIn, request: Request) -> TurnOut:
        """A chat message: a new request, or feedback on the outfit on screen. A message that
        can't be applied ("keep the dress" with no dress) comes back as a note, not an error."""
        if not limiter.allow(request.client.host if request.client else "unknown"):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "That's a lot of messages. Wait a minute, then try again.")
        reply = run_turn(session_id, "message", {"text": body.text},
                         lambda state: handle_message(catalog, state, body.text, embed, parse))
        return TurnOut.of(session_id, reply)

    @api.post("/sessions/{session_id}/actions", response_model=TurnOut,
              responses={404: {"description": "Unknown session or item"},
                         409: {"description": "The action doesn't fit the current outfit"}})
    def send_action(session_id: str, intent: FeedbackIntent) -> TurnOut:
        """A button click (lock, unlock, reject, swap, nudge, accept, budget, cheaper, remove). Skips the
        parser (FR-FB-7). A budget can be set before the first outfit; the rest need one on screen."""
        if intent.item_id and catalog.get(intent.item_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No item {intent.item_id}.")
        reply = run_turn(session_id, "action", intent.model_dump(mode="json", exclude_none=True),
                         lambda state: handle_action(catalog, state, intent, embed))
        if reply.error:
            raise HTTPException(status.HTTP_409_CONFLICT, reply.error)
        return TurnOut.of(session_id, reply)

    @api.get("/catalog/items", response_model=list[ItemOut])
    def list_items(slot: Slot | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100,
                   offset: Annotated[int, Query(ge=0)] = 0) -> list[ItemOut]:
        items = catalog.in_slot(slot) if slot else sorted(catalog.items.values(), key=lambda i: i.item_id)
        return [ItemOut.of(i) for i in items[offset:offset + limit]]

    @api.get("/catalog/items/{item_id}", response_model=ItemOut, responses={404: {"description": "Unknown item"}})
    def get_item(item_id: str) -> ItemOut:
        item = catalog.get(item_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No item {item_id}.")
        return ItemOut.of(item)

    @api.get("/catalog/resolve", response_model=ResolutionOut)
    def resolve_name(name: Annotated[str, Query(min_length=1, max_length=200)],
                     slot: Slot | None = None) -> ResolutionOut:
        """Find the product a typed name refers to (FR-NAME): the match, near-ties, or suggestions."""
        return ResolutionOut.of(resolve(catalog, name, slot))

    app.include_router(api)
    return app


def install_error_handlers(app: FastAPI) -> None:
    """Every error is {"detail": "..."}; bad input is a 4xx, never a raw 500 (FR-API-2)."""

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = jsonable_encoder(exc.errors())
        first = errors[0] if errors else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        detail = f"Invalid request: {where + ': ' if where else ''}{first.get('msg', 'bad input')}"
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": detail, "errors": errors})

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("stylist.api").exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            content={"detail": "Something went wrong on our side."})
