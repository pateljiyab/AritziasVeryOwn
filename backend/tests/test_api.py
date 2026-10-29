"""The REST API through FastAPI's test client: one test per endpoint, plus the FR-API-2 errors."""

import pytest
from fastapi.testclient import TestClient

from factories import make_item, vector
from stylist.adapters.session_store import SessionStore
from stylist.api.app import RateLimiter, create_app
from stylist.core.catalog import Catalog

SLOTS = ("top", "bottom", "dress", "outerwear", "shoes", "accessory")


def small_catalog() -> Catalog:
    items = [make_item(f"{slot}-{n}", slot, formality=f, colors=(c,))
             for slot in SLOTS for n, (f, c) in enumerate([(2, "black"), (3, "cream"), (4, "navy")])]
    items.append(make_item("effortless", "bottom", name="The Effortless Pant", formality=3))
    return Catalog(items)


@pytest.fixture
def store(tmp_path):
    return SessionStore(f"sqlite:///{tmp_path / 'sessions.db'}")


@pytest.fixture
def client(store):
    return TestClient(create_app(catalog=small_catalog(), embed=lambda text: vector(text) if text else None,
                                 store=store))


@pytest.fixture
def session_id(client):
    return client.post("/api/v1/sessions").json()["session_id"]


def say(client, session_id, text):
    return client.post(f"/api/v1/sessions/{session_id}/messages", json={"text": text})


def act(client, session_id, **intent):
    return client.post(f"/api/v1/sessions/{session_id}/actions", json=intent)


def piece(turn, slot):
    return next(p for p in turn["outfit"]["pieces"] if p["slot"] == slot)


# ------------------------------------------------------------- endpoints

def test_health(client):
    assert client.get("/api/v1/health").json() == {"status": "ok", "items": len(small_catalog()),
                                                   "parser": "keyword rules"}  # no LLM key in tests


def test_create_session(client, store):
    response = client.post("/api/v1/sessions")

    assert response.status_code == 201
    assert store.exists(response.json()["session_id"])


def test_message_returns_an_outfit_with_its_score_breakdown(client, session_id):  # FR-GEN-3
    turn = say(client, session_id, "cozy fall Sunday, running errands").json()

    assert turn["outfit"]["pieces"] and turn["outfit"]["breakdown"]
    assert turn["parsed"]["vibes"] == ["cozy", "off-duty"]
    assert turn["outfit"]["total_price"] == round(sum(p["price"] for p in turn["outfit"]["pieces"]), 2)


def test_named_item_comes_back_locked(client, session_id):  # Flow C
    turn = say(client, session_id, "build something around the Effortless Pant").json()

    assert piece(turn, "bottom")["item_id"] == "effortless" and piece(turn, "bottom")["locked"]


def test_action_lock_then_swap_keeps_the_locked_piece(client, session_id):  # FR-FB-1, -3
    first = say(client, session_id, "dinner").json()
    shoes = piece(first, "shoes")["item_id"]

    assert act(client, session_id, action="lock", item_id=shoes).status_code == 200
    turn = act(client, session_id, action="swap", slot="bottom" if "bottom" in
               {p["slot"] for p in first["outfit"]["pieces"]} else "dress").json()

    assert piece(turn, "shoes")["item_id"] == shoes and piece(turn, "shoes")["locked"]


def test_button_and_chat_produce_the_same_outfit(client, store):  # FR-FB-7
    by_chat, by_button = (client.post("/api/v1/sessions").json()["session_id"] for _ in range(2))
    for sid in (by_chat, by_button):
        say(client, sid, "dinner")

    say(client, by_chat, "different shoes")
    act(client, by_button, action="swap", slot="shoes")

    assert store.load(by_chat).current == store.load(by_button).current


def test_accept_returns_the_summary(client, session_id):  # FR-FB-5
    say(client, session_id, "dinner")

    turn = act(client, session_id, action="accept").json()

    assert turn["accepted"] and turn["summary"]["total_price"] == turn["outfit"]["total_price"]
    assert all(p["product_url"] for p in turn["summary"]["pieces"])


def test_get_session_shows_the_outfit_and_every_turn(client, session_id):
    say(client, session_id, "dinner")
    act(client, session_id, action="swap", slot="shoes")

    session = client.get(f"/api/v1/sessions/{session_id}").json()

    assert [t["kind"] for t in session["turns"]] == ["message", "action"]
    assert session["outfit"]["pieces"] and not session["accepted"]


def test_every_turn_is_saved_with_its_state(client, store, session_id):  # NFR-6, G2
    say(client, session_id, "dinner")
    act(client, session_id, action="lock", slot="shoes")
    act(client, session_id, action="reject", slot="top")  # may 409 if the outfit is a dress

    turns = store.turns(session_id)

    assert len(turns) == 3
    assert turns[1].locked and turns[0].parsed["formality"] == 3
    assert all(t.latency_ms >= 0 for t in turns)


def test_list_items_filters_by_slot(client):
    shoes = client.get("/api/v1/catalog/items", params={"slot": "shoes"}).json()

    assert {i["slot"] for i in shoes} == {"shoes"} and len(shoes) == 3
    assert len(client.get("/api/v1/catalog/items", params={"limit": 2}).json()) == 2


def test_get_item(client):
    assert client.get("/api/v1/catalog/items/effortless").json()["name"] == "The Effortless Pant"


def test_resolve_name(client):
    found = client.get("/api/v1/catalog/resolve", params={"name": "efortless pnt"}).json()
    assert found["match"]["item_id"] == "effortless"

    missing = client.get("/api/v1/catalog/resolve", params={"name": "zebra cape"}).json()
    assert missing["match"] is None and missing["suggestions"]  # FR-NAME-2


def test_openapi_docs_are_served(client):
    assert client.get("/docs").status_code == 200
    assert "/api/v1/sessions/{session_id}/actions" in client.get("/openapi.json").json()["paths"]


# -------------------------------------------------------- errors (FR-API-2)

def test_unknown_session_is_404(client):
    for response in (client.get("/api/v1/sessions/nope"), say(client, "nope", "dinner"),
                     act(client, "nope", action="accept")):
        assert response.status_code == 404 and response.json()["detail"] == "No session nope."


def test_invalid_slot_is_422(client, session_id):
    for response in (act(client, session_id, action="swap", slot="hat"),
                     client.get("/api/v1/catalog/items", params={"slot": "hat"})):
        assert response.status_code == 422
        assert response.json()["detail"].startswith("Invalid request")


def test_unknown_item_is_404(client, session_id):
    say(client, session_id, "dinner")

    for response in (act(client, session_id, action="lock", item_id="zzz"), client.get("/api/v1/catalog/items/zzz")):
        assert response.status_code == 404 and response.json()["detail"] == "No item zzz."


def test_action_that_doesnt_fit_the_outfit_is_409(client, session_id):
    first = say(client, session_id, "dinner").json()
    missing = "top" if "dress" in {p["slot"] for p in first["outfit"]["pieces"]} else "dress"

    response = act(client, session_id, action="lock", slot=missing)

    assert response.status_code == 409 and f"no {missing}" in response.json()["detail"]


def test_action_before_any_outfit_is_409(client, session_id):
    assert act(client, session_id, action="swap", slot="shoes").status_code == 409


def test_malformed_bodies_are_422_not_500(client, session_id):
    for response in (client.post(f"/api/v1/sessions/{session_id}/messages", json={}),
                     say(client, session_id, ""),
                     act(client, session_id, action="lock"),  # no slot or item
                     act(client, session_id, action="explode"),
                     client.post(f"/api/v1/sessions/{session_id}/messages", content="not json",
                                 headers={"content-type": "application/json"})):
        assert response.status_code == 422, response.text


def test_chat_message_that_doesnt_fit_is_a_note_not_an_error(client, session_id):
    first = say(client, session_id, "dinner").json()
    missing = "top" if "dress" in {p["slot"] for p in first["outfit"]["pieces"]} else "dress"

    response = say(client, session_id, f"keep the {missing}")

    assert response.status_code == 200 and response.json()["notes"]


def test_too_many_messages_is_429(store):
    client = TestClient(create_app(catalog=small_catalog(), embed=lambda text: None, store=store,
                                   limiter=RateLimiter(limit=2)))
    sid = client.post("/api/v1/sessions").json()["session_id"]

    codes = [say(client, sid, "dinner").status_code for _ in range(3)]

    assert codes == [200, 200, 429]
    assert act(client, sid, action="swap", slot="shoes").status_code != 429  # buttons make no LLM call


def test_rate_limiter_window_slides():
    now = [0.0]
    limiter = RateLimiter(limit=1, window=60, clock=lambda: now[0])

    assert limiter.allow("a") and not limiter.allow("a") and limiter.allow("b")
    now[0] = 60.0
    assert limiter.allow("a")


def test_unexpected_error_is_a_clean_500(store):
    def broken_embed(text):
        raise RuntimeError("boom")

    client = TestClient(create_app(catalog=small_catalog(), embed=broken_embed, store=store),
                        raise_server_exceptions=False)
    sid = client.post("/api/v1/sessions").json()["session_id"]

    response = say(client, sid, "dinner")

    assert response.status_code == 500 and response.json() == {"detail": "Something went wrong on our side."}
