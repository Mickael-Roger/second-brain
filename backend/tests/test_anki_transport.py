"""Exercise the actual HTTP bridge from authenticated API routes to AnkiConnect."""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from app.anki import client as anki_client
from app.api import anki
from app.auth import current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def bridge(monkeypatch):
    settings = SimpleNamespace(
        anki=SimpleNamespace(
            enabled=True,
            url="http://anki.test:8765",
            api_key="test-key",
            timeout_seconds=5,
        )
    )
    monkeypatch.setattr(anki, "get_settings", lambda: settings)
    monkeypatch.setattr(anki_client, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(anki.router)
    app.dependency_overrides[current_user] = lambda: "test"
    with TestClient(app) as client:
        yield client


@respx.mock
def test_full_anki_http_bridge(bridge):
    calls = []
    answered = False

    def plugin(request):
        nonlocal answered
        payload = json.loads(request.content)
        assert payload["version"] == 6
        assert payload["key"] == "test-key"
        action = payload["action"]
        params = payload.get("params", {})
        calls.append(action)
        if action == "deckNamesAndIds":
            result = {"Test": 1}
        elif action == "getDeckStats":
            assert params == {"decks": ["Test"]}
            result = {
                "1": {
                    "name": "Test",
                    "new_count": 0,
                    "learn_count": 0,
                    "review_count": 0 if answered else 1,
                    "total_in_deck": 7,
                }
            }
        elif action == "findCards":
            assert params["query"] == 'deck:"Test" (is:due OR is:new) -is:suspended -is:buried'
            result = [] if answered else [123]
        elif action == "cardsInfo":
            assert params["cards"] == [123]
            result = [
                {
                    "cardId": 123,
                    "note": 456,
                    "queue": 2,
                    "type": 2,
                    "question": "<b>Reverse question</b>",
                    "answer": "Reverse answer",
                    "css": ".card {font-size:20px}",
                    "fields": {},
                }
            ]
        elif action == "answerCards":
            assert params == {"answers": [{"cardId": 123, "ease": 3}]}
            answered = True
            result = [True]
        elif action == "addNote":
            assert params["note"]["modelName"] == "Basic (and reversed card)"
            assert params["note"]["deckName"] == "Test"
            result = 789
        elif action == "retrieveMediaFile":
            assert params == {"filename": "picture.png"}
            result = base64.b64encode(b"image-data").decode()
        else:
            pytest.fail(f"Unexpected AnkiConnect action: {action}")
        return httpx.Response(200, json={"result": result, "error": None})

    respx.post("http://anki.test:8765").mock(side_effect=plugin)
    stats = bridge.get("/api/anki/deck-stats")
    assert stats.status_code == 200
    assert stats.json()[0]["total_in_deck"] == 7
    assert stats.json()[0]["review_count"] == 1
    cards = bridge.get("/api/anki/due", params={"deck": "Test"})
    assert cards.status_code == 200
    assert cards.json()[0]["question"] == "<b>Reverse question</b>"
    assert cards.json()[0]["state"] == "review"
    assert bridge.post("/api/anki/cards/123/answer", json={"ease": 3}).json() == {"answered": True}
    assert bridge.get("/api/anki/due?deck=Test").json() == []
    assert bridge.get("/api/anki/deck-stats").json()[0]["review_count"] == 0
    created = bridge.post(
        "/api/anki/cards",
        json={
            "deck": "Test",
            "front": "Q",
            "back": "A",
            "mode": "reverse",
            "tags": ["test"],
        },
    )
    assert created.status_code == 201
    assert created.json() == {"note_id": 789, "cards_created": 2}
    assert bridge.get("/api/anki/media?filename=picture.png").content == b"image-data"
    assert {
        "getDeckStats",
        "findCards",
        "cardsInfo",
        "answerCards",
        "addNote",
        "retrieveMediaFile",
    } <= set(calls)


@respx.mock
@pytest.mark.parametrize("failure", ["offline", "plugin", "invalid-json"])
def test_anki_failures_are_gateway_errors(bridge, failure):
    route = respx.post("http://anki.test:8765")
    if failure == "offline":
        route.mock(side_effect=httpx.ConnectError("offline"))
    elif failure == "plugin":
        route.respond(200, json={"result": None, "error": "Anki error"})
    else:
        route.respond(200, text="not JSON")
    response = bridge.get("/api/anki/deck-stats")
    assert response.status_code == 502
    assert "Anki" in response.json()["detail"]
