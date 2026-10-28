"""One chat turn: message in, outfit and notes out. The CLI uses this now; the API will too.

The parser and the text embedder are passed in, so the same turn logic runs with the keyword
parser or the LLM one, and with real CLIP or a test fake.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from stylist.core import feedback, keyword_parser
from stylist.core.catalog import Catalog
from stylist.core.feedback import FeedbackError, SessionState
from stylist.core.generator import NoOutfitError
from stylist.core.models import FeedbackIntent, Item, Outfit, ParsedRequest
from stylist.core.names import resolve

Embed = Callable[[str], "np.ndarray | None"]
Parse = Callable[[str, bool], "ParsedRequest | FeedbackIntent"]


@dataclass
class Reply:
    state: SessionState | None
    outfit: Outfit | None
    notes: list[str] = field(default_factory=list)  # things to tell the user (no match, errors, ...)
    choices: dict[str, list[Item]] = field(default_factory=dict)  # typed name -> close matches (FR-NAME-3)
    summary: dict | None = None  # set when the user accepts (FR-FB-5)
    parsed: ParsedRequest | FeedbackIntent | None = None  # what the message was understood as, for logs
    error: str | None = None  # set when an action couldn't be applied; the state is unchanged


def lock_named_items(catalog: Catalog, names: list[str]) -> tuple[dict[str, str], list[str], dict[str, list[Item]]]:
    """Resolve the products a request names into locks (Flow C)."""
    locked: dict[str, str] = {}
    notes: list[str] = []
    choices: dict[str, list[Item]] = {}
    for name in names:
        found = resolve(catalog, name)
        if found.item is None:
            hint = f" Did you mean: {', '.join(i.name for i in found.suggestions)}?" if found.suggestions else ""
            notes.append(f"I couldn't find \"{name}\" in the catalog.{hint}")  # FR-NAME-2
            continue
        if found.ambiguous:
            choices[name] = found.choices
            others = ", ".join(i.name for i in found.choices[1:])
            notes.append(f"\"{name}\" matches a few products. I went with {found.item.name} (others: {others}).")
        slot = found.item.slot
        clash = slot in locked or (slot == "dress" and locked.keys() & {"top", "bottom"}) or \
            (slot in {"top", "bottom"} and "dress" in locked)
        if clash:
            notes.append(f"I can't fit {found.item.name} in as well, so I left it out.")
            continue
        locked[slot] = found.item.item_id
    return locked, notes, choices


def handle_message(catalog: Catalog, state: SessionState | None, text: str, embed: Embed,
                   parse: Parse = keyword_parser.parse) -> Reply:
    """Apply one chat message to a session (None starts a new one)."""
    if not text.strip():
        return Reply(state, None, ["Tell me what you're dressing for, like \"cozy fall Sunday, running errands\"."])
    live = state is not None and bool(state.current) and not state.accepted
    parsed = parse(text, live)

    if isinstance(parsed, FeedbackIntent):
        return handle_action(catalog, state, parsed, embed)

    locked, notes, choices = lock_named_items(catalog, parsed.named_items)
    try:
        new_state, outfit = feedback.start(catalog, parsed, embed(parsed.text), locked)
    except NoOutfitError as exc:
        return Reply(state, None, [*notes, f"I couldn't build an outfit: {exc}."], choices, parsed=parsed)
    return Reply(new_state, outfit, notes, choices, parsed=parsed)


def handle_action(catalog: Catalog, state: SessionState | None, intent: FeedbackIntent, embed: Embed) -> Reply:
    """Apply a button click or a parsed feedback message (FR-FB-7: both come through here)."""
    if state is None or not state.current:
        message = "There's no outfit yet. Tell me what you're dressing for first."
        return Reply(state, None, [message], parsed=intent, error=message)
    try:
        new_state, outfit = feedback.apply_feedback(catalog, state, intent, embed(state.request.text))
    except (FeedbackError, NoOutfitError) as exc:
        message = f"{str(exc)[:1].upper()}{str(exc)[1:]}."
        return Reply(state, feedback.outfit_of(catalog, state), [message], parsed=intent, error=message)
    summary = feedback.summary(outfit) if new_state.accepted else None
    return Reply(new_state, outfit, [], summary=summary, parsed=intent)
