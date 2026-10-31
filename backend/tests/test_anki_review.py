from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest
from app.anki import client


@pytest.mark.asyncio
async def test_stats_and_review_wire_format(monkeypatch):
    invoke = AsyncMock(
        side_effect=[
            {"Test": 42},
            {
                "42": {
                    "name": "Test",
                    "review_count": 2,
                    "new_count": 1,
                    "learn_count": 3,
                    "total_in_deck": 42,
                }
            },
            [True],
        ]
    )
    monkeypatch.setattr(client, "_invoke", invoke)
    assert [asdict(deck) for deck in await client.deck_stats()] == [
        {
            "deck_id": 42,
            "name": "Test",
            "review_count": 2,
            "new_count": 1,
            "learn_count": 3,
            "total_in_deck": 42,
        }
    ]
    assert await client.answer_card(123, 4)
    assert invoke.await_args_list[1].kwargs == {"decks": ["Test"]}
    assert invoke.await_args_list[2].kwargs == {"answers": [{"cardId": 123, "ease": 4}]}


@pytest.mark.asyncio
async def test_due_cards_escape_deck_and_preserve_rendered_sides(monkeypatch):
    invoke = AsyncMock(
        side_effect=[
            [1],
            [
                {
                    "cardId": 1,
                    "note": 2,
                    "question": "Reverse question",
                    "answer": "Reverse answer",
                    "fields": {"Front": {"value": "Original front"}},
                }
            ],
        ]
    )
    monkeypatch.setattr(client, "_invoke", invoke)
    cards = await client.list_due_cards('A"B')
    assert invoke.await_args_list[0].kwargs == {"query": 'deck:"A\\"B" is:due'}
    assert cards[0].question == "Reverse question"
    assert cards[0].answer == "Reverse answer"


@pytest.mark.asyncio
async def test_deck_totals_for_older_anki(monkeypatch):
    invoke = AsyncMock(
        side_effect=[
            {"Empty": 1, "Has cards": 2},
            {
                "1": {"new_count": 0, "learn_count": 0, "review_count": 0},
                "2": {"new_count": 1, "learn_count": 1, "review_count": 1},
            },
            [],
            [11, 12, 13],
        ]
    )
    monkeypatch.setattr(client, "_invoke", invoke)
    decks = await client.deck_stats()
    assert len(decks) == 2
    assert decks[0].total_in_deck == 0
    assert decks[1].total_in_deck == 3
    assert invoke.await_args_list[3].kwargs == {"query": 'deck:"Has cards"'}


@pytest.mark.asyncio
async def test_incomplete_stats_are_reported_not_zeroed(monkeypatch):
    invoke = AsyncMock(side_effect=[{"Test": 1}, {}])
    monkeypatch.setattr(client, "_invoke", invoke)
    with pytest.raises(client.AnkiConnectError, match="incomplete deck statistics"):
        await client.deck_stats()


@pytest.mark.asyncio
async def test_due_new_cards_pagination_and_deleted_cards(monkeypatch):
    invoke = AsyncMock(side_effect=[[1, 2, 3], [{}, {"cardId": 3, "css": ".card {color:red}"}]])
    monkeypatch.setattr(client, "_invoke", invoke)
    cards = await client.list_due_cards("Test", limit=2, offset=1, include_new=True)
    assert invoke.await_args_list[0].kwargs == {
        "query": 'deck:"Test" (is:due OR is:new) -is:suspended -is:buried'
    }
    assert invoke.await_args_list[1].kwargs == {"cards": [2, 3]}
    assert len(cards) == 1
    assert cards[0].card_id == 3
    assert cards[0].css == ".card {color:red}"
