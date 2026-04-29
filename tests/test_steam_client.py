import pytest
from unittest.mock import AsyncMock, MagicMock
import httpx

from gabechoice.clients.steam import SteamClient, AppDetailsRaw

OWNED_GAMES_RESP = {
    "response": {
        "game_count": 2,
        "games": [
            {"appid": 570, "name": "Dota 2", "playtime_forever": 1000},
            {"appid": 730, "name": "CS2", "playtime_forever": 500},
        ],
    }
}

WISHLIST_RESP = {
    "response": {
        "items": [
            {"appid": 1245620, "priority": 0, "date_added": 1609459200},
        ]
    }
}

APPDETAILS_RESP = {
    "570": {
        "success": True,
        "data": {
            "name": "Dota 2",
            "short_description": "Battle your way to glory.",
            "genres": [{"id": "37", "description": "Free to Play"}],
            "categories": [{"id": 1, "description": "Multi-player"}],
            "metacritic": None,
            "header_image": "https://cdn.cloudflare.steamstatic.com/steam/apps/570/header.jpg",
            "price_overview": None,
        },
    }
}

APPDETAILS_WITH_META = {
    "1245620": {
        "success": True,
        "data": {
            "name": "ELDEN RING",
            "short_description": "The seminal RPG.",
            "genres": [{"id": "1", "description": "Action"}, {"id": "25", "description": "RPG"}],
            "categories": [{"id": 2, "description": "Single-player"}],
            "metacritic": {"score": 96, "url": "https://www.metacritic.com/game/pc/elden-ring"},
            "header_image": "https://cdn.cloudflare.steamstatic.com/steam/apps/1245620/header.jpg",
            "price_overview": {"final": 5999, "currency": "USD"},
        },
    }
}


def _mock_client(json_data: dict, status_code: int = 200) -> AsyncMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    mock = AsyncMock(spec=httpx.AsyncClient)
    mock.get.return_value = resp
    return mock


async def test_get_owned_games():
    steam = SteamClient("key", _client=_mock_client(OWNED_GAMES_RESP))
    games = await steam.get_owned_games("123")
    assert len(games) == 2
    assert games[0].appid == 570
    assert games[0].name == "Dota 2"
    assert games[0].playtime_forever == 1000


async def test_get_wishlist():
    steam = SteamClient("key", _client=_mock_client(WISHLIST_RESP))
    entries = await steam.get_wishlist("123")
    assert len(entries) == 1
    assert entries[0].appid == 1245620


async def test_get_wishlist_empty_returns_empty_list():
    steam = SteamClient("key", _client=_mock_client({"response": {}}))
    entries = await steam.get_wishlist("123")
    assert entries == []


async def test_get_appdetails_basic():
    steam = SteamClient("key", _client=_mock_client(APPDETAILS_RESP))
    details = await steam.get_appdetails(570)
    assert details is not None
    assert details.name == "Dota 2"
    assert details.genres == ["Free to Play"]
    assert details.tags == ["Multi-player"]
    assert details.metacritic_score is None


async def test_get_appdetails_with_metacritic():
    steam = SteamClient("key", _client=_mock_client(APPDETAILS_WITH_META))
    details = await steam.get_appdetails(1245620)
    assert details is not None
    assert details.metacritic_score == 96
    assert details.price_cents == 5999
    assert details.currency == "USD"
    assert "metacritic" in details.metacritic_url


async def test_get_appdetails_not_found():
    resp_data = {"99999": {"success": False}}
    steam = SteamClient("key", _client=_mock_client(resp_data))
    details = await steam.get_appdetails(99999)
    assert details is None


async def test_enrichment_dict_excludes_none():
    raw = AppDetailsRaw(appid=570, name="Dota 2", genres=["Free to Play"])
    d = raw.to_enrichment_dict()
    assert d["name"] == "Dota 2"
    assert d["genres"] == ["Free to Play"]
    # None values are present but the consumer filters them out.
    assert "metacritic_score" in d
