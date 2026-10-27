"""One chat turn end to end: parse, resolve names, generate, refine, accept."""

from factories import make_item, vector
from stylist.core.catalog import Catalog
from stylist.core.models import FeedbackIntent
from stylist.core.session import handle_action, handle_message


def catalog():
    items = [make_item(f"{slot}-{n}", slot, formality=f, colors=(c,))
             for slot in ("top", "bottom", "dress", "outerwear", "shoes", "accessory")
             for n, (f, c) in enumerate([(2, "black"), (3, "cream"), (4, "navy")])]
    items.append(make_item("effortless", "bottom", name="The Effortless Pant", formality=3))
    items.append(make_item("cozy-dress", "dress", name="Cozy Knit Dress", vibes=("cozy",), formality=2))
    return Catalog(items)


def embed(text):
    return vector(text) if text else None


def test_request_produces_an_outfit():
    reply = handle_message(catalog(), None, "cozy fall Sunday, running errands", embed)

    assert reply.outfit is not None and reply.state.current
    assert reply.notes == []


def test_named_item_is_locked_into_its_slot():  # Flow C
    reply = handle_message(catalog(), None, "build something around the Effortless Pant", embed)

    assert reply.outfit.pieces["bottom"].item_id == "effortless"
    assert reply.state.locked == {"bottom": "effortless"}


def test_unknown_named_item_is_reported_and_the_outfit_still_comes():  # FR-NAME-2
    reply = handle_message(catalog(), None, "build something around the Zebra Cape", embed)

    assert reply.outfit is not None
    assert "couldn't find \"Zebra Cape\"" in reply.notes[0]


def test_named_items_that_cant_go_together_keep_the_first():
    reply = handle_message(catalog(), None, "style the Effortless Pant, featuring the Cozy Knit Dress", embed)

    assert reply.state.locked == {"bottom": "effortless"}
    assert any("left it out" in note for note in reply.notes)


def test_chat_feedback_refines_the_outfit_and_accept_ends_it():
    c = catalog()
    reply = handle_message(c, None, "dinner", embed)
    shoes = reply.outfit.pieces["shoes"].item_id

    reply = handle_message(c, reply.state, "different shoes", embed)
    assert reply.outfit.pieces["shoes"].item_id != shoes

    reply = handle_message(c, reply.state, "perfect", embed)
    assert reply.state.accepted and reply.summary["total_price"] == reply.outfit.total_price


def test_button_and_chat_reach_the_same_state():  # FR-FB-7
    c = catalog()
    start = handle_message(c, None, "dinner", embed)

    by_chat = handle_message(c, start.state, "keep the shoes", embed)
    by_button = handle_action(c, start.state, FeedbackIntent(action="lock", item_id=start.outfit.pieces["shoes"].item_id),
                              embed)

    assert by_chat.state == by_button.state


def test_impossible_feedback_is_a_note_not_a_crash():
    c = catalog()
    start = handle_message(c, None, "dinner", embed)
    missing = "top" if "dress" in start.outfit.pieces else "dress"  # a slot this outfit can't have

    reply = handle_message(c, start.state, f"keep the {missing}", embed)

    assert reply.state == start.state and reply.outfit.item_ids == start.outfit.item_ids
    assert f"no {missing}" in reply.notes[0]


def test_feedback_before_any_outfit_asks_for_a_request():
    reply = handle_action(catalog(), None, FeedbackIntent(action="swap", slot="shoes"), embed)
    assert reply.outfit is None and "no outfit yet" in reply.notes[0]


def test_blank_message_gets_a_prompt():
    assert handle_message(catalog(), None, "  ", embed).notes
