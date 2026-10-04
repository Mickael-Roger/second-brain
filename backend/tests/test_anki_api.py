"""Exercise Anki API gating, validation and AnkiConnect failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.anki import AnkiConnectError, AnkiDeck
from app.api import anki
from app.auth import current_user, optional_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        anki, "get_settings", lambda: SimpleNamespace(anki=SimpleNamespace(enabled=True))
    )
    app = FastAPI()
    app.include_router(anki.router)
    app.dependency_overrides[current_user] = lambda: "test-user"
    with TestClient(app) as client:
        yield client


def test_disabled_config_and_operations(client, monkeypatch):
    monkeypatch.setattr(
        anki, "get_settings", lambda: SimpleNamespace(anki=SimpleNamespace(enabled=False))
    )
    add = AsyncMock()
    monkeypatch.setattr(anki, "add_card", add)
    assert client.get("/api/anki/config").json() == {"enabled": False}
    assert client.get("/api/anki/decks").status_code == 403
    assert (
        client.post("/api/anki/cards", json={"deck": "Test", "front": "Q", "back": "A"}).status_code
        == 403
    )
    add.assert_not_awaited()


def test_authentication_required(client):
    client.app.dependency_overrides.clear()
    client.app.dependency_overrides[optional_current_user] = lambda: None
    assert client.get("/api/anki/config").status_code == 401
    assert client.get("/api/anki/decks").status_code == 401
    assert (
        client.post("/api/anki/cards", json={"deck": "Test", "front": "Q", "back": "A"}).status_code
        == 401
    )


def test_list_decks(client, monkeypatch):
    monkeypatch.setattr(anki, "list_decks", AsyncMock(return_value=[AnkiDeck(1, "Test")]))
    assert client.get("/api/anki/decks").json() == [{"id": 1, "name": "Test"}]


@pytest.mark.parametrize("mode,count", [("normal", 1), ("reverse", 2)])
def test_create_note(client, monkeypatch, mode, count):
    add = AsyncMock(return_value=123)
    monkeypatch.setattr(anki, "add_card", add)
    response = client.post(
        "/api/anki/cards",
        json={"deck": " Test ", "front": " Q ", "back": " A ", "mode": mode, "tags": [" tag ", ""]},
    )
    assert response.status_code == 201
    assert response.json() == {"note_id": 123, "cards_created": count}
    add.assert_awaited_once_with(deck="Test", front="Q", back="A", mode=mode, tags=["tag"])


@pytest.mark.parametrize(
    "field,value", [("deck", " "), ("front", " "), ("back", ""), ("mode", "cloze")]
)
def test_reject_invalid_card(client, monkeypatch, field, value):
    add = AsyncMock()
    monkeypatch.setattr(anki, "add_card", add)
    payload = {"deck": "Test", "front": "Q", "back": "A", field: value}
    assert client.post("/api/anki/cards", json=payload).status_code == 422
    add.assert_not_awaited()


def test_connect_error_is_reported(client, monkeypatch):
    monkeypatch.setattr(
        anki, "list_decks", AsyncMock(side_effect=AnkiConnectError("Anki unreachable"))
    )
    response = client.get("/api/anki/decks")
    assert response.status_code == 502
    assert response.json() == {"detail": "Anki unreachable"}
    monkeypatch.setattr(anki, "add_card", AsyncMock(side_effect=AnkiConnectError("Duplicate note")))
    response = client.post("/api/anki/cards", json={"deck": "Test", "front": "Q", "back": "A"})
    assert response.status_code == 502
    assert response.json() == {"detail": "Duplicate note"}
