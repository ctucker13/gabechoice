"""Smoke tests for the LangGraph pipeline."""

import time
import pytest
from unittest.mock import AsyncMock

from gabechoice.clients.steam import SteamClient, AppDetailsRaw, OwnedGameRaw, WishlistEntryRaw
from gabechoice.cache import GameCache
from gabechoice.graph.builder import build_graph
from gabechoice.models import Game, TasteProfile


def _library_game(appid: int, name: str, playtime: int) -> Game:
    return Game(
        appid=appid,
        name=name,
        playtime_minutes=playtime,
        source="library",
        store_url=f"https://store.steampowered.com/app/{appid}/",
    )


def _wishlist_game(appid: int) -> Game:
    return Game(
        appid=appid,
        name="",
        playtime_minutes=0,
        source="wishlist",
        store_url=f"https://store.steampowered.com/app/{appid}/",
    )


@pytest.fixture
async def cache(tmp_path):
    c = GameCache(str(tmp_path / "graph_test.sqlite"))
    await c.init_db()
    return c


@pytest.fixture
def mock_steam():
    steam = AsyncMock(spec=SteamClient)
    steam.get_trending_appids.return_value = []  # empty by default; override per-test
    steam.get_owned_games.return_value = [
        OwnedGameRaw(appid=570, name="Dota 2", playtime_forever=60000),
        OwnedGameRaw(appid=730, name="CS2", playtime_forever=30000),
    ]
    steam.get_wishlist.return_value = [
        WishlistEntryRaw(appid=1245620, priority=0),
    ]
    steam.enrich_many.return_value = {
        570: AppDetailsRaw(appid=570, name="Dota 2", genres=["Free to Play"], tags=["Multi-player"]),
        730: AppDetailsRaw(appid=730, name="Counter-Strike 2", genres=["Action"], tags=["Multi-player"]),
        1245620: AppDetailsRaw(
            appid=1245620,
            name="ELDEN RING",
            genres=["Action", "RPG"],
            metacritic_score=96,
            metacritic_url="https://www.metacritic.com/game/pc/elden-ring",
            price_cents=5999,
            currency="USD",
        ),
    }
    return steam


async def test_graph_fetch_and_enrich(mock_steam, cache):
    graph = build_graph(mock_steam, cache)
    result = await graph.ainvoke({"run_start": time.monotonic()})

    games: list[Game] = result["enriched_games"]
    assert len(games) == 3

    by_appid = {g.appid: g for g in games}

    # library game has playtime preserved
    assert by_appid[570].playtime_minutes == 60000
    assert by_appid[570].source == "library"
    assert by_appid[570].genres == ["Free to Play"]

    # wishlist game has enrichment merged
    assert by_appid[1245620].source == "wishlist"
    assert by_appid[1245620].metacritic_score == 96
    assert by_appid[1245620].price_cents == 5999

    assert result["cache_hits"] == 0
    assert result["cache_misses"] == 3


async def test_graph_cache_hit_on_second_run(mock_steam, cache):
    graph = build_graph(mock_steam, cache)

    # First run populates the cache.
    await graph.ainvoke({"run_start": time.monotonic()})

    # Second run should hit the cache for all three games.
    result2 = await graph.ainvoke({"run_start": time.monotonic()})
    assert result2["cache_hits"] == 3
    assert result2["cache_misses"] == 0
    # enrich_many should not have been called a second time.
    assert mock_steam.enrich_many.call_count == 1


async def test_graph_deduplicates_overlap(mock_steam, cache):
    # Make appid 570 appear in both library AND wishlist.
    mock_steam.get_wishlist.return_value = [
        WishlistEntryRaw(appid=570, priority=0),   # already in library
        WishlistEntryRaw(appid=1245620, priority=1),
    ]
    mock_steam.enrich_many.return_value = {
        570: AppDetailsRaw(appid=570, name="Dota 2"),
        730: AppDetailsRaw(appid=730, name="CS2"),
        1245620: AppDetailsRaw(appid=1245620, name="ELDEN RING"),
    }

    graph = build_graph(mock_steam, cache)
    result = await graph.ainvoke({"run_start": time.monotonic()})

    games: list[Game] = result["enriched_games"]
    appids = [g.appid for g in games]
    assert len(appids) == len(set(appids)), "duplicate appids in enriched_games"

    # The library entry wins: source should be "library", not "wishlist".
    by_appid = {g.appid: g for g in games}
    assert by_appid[570].source == "library"


# ── Taste profile node ────────────────────────────────────────────────

_FAKE_PROFILE = TasteProfile(
    preferred_genres=["Action RPG", "Roguelike"],
    preferred_mechanics=["Deep progression", "Precision combat"],
    vibes=["Atmospheric", "Challenging"],
    wildcard_picks=["Precision platformer", "Rhythm game"],
    summary="Test taste profile summary.",
)


@pytest.fixture
def mock_llm():
    llm = AsyncMock()

    async def _fake(system, user, schema):
        if schema is TasteProfile:
            return _FAKE_PROFILE
        # _PickList schema for rank_and_explain — return empty picks to keep tests simple
        return schema.model_validate({"picks": []})

    llm.complete_json.side_effect = _fake
    return llm


async def test_taste_profile_node_called(mock_steam, cache, mock_llm):
    graph = build_graph(mock_steam, cache, llm=mock_llm)
    result = await graph.ainvoke({"run_start": time.monotonic()})

    assert "taste_profile" in result
    profile = result["taste_profile"]
    assert isinstance(profile, TasteProfile)
    assert profile.preferred_genres == ["Action RPG", "Roguelike"]
    # taste_profile + rank_and_explain both call complete_json
    assert mock_llm.complete_json.call_count == 2


async def test_taste_profile_includes_all_signals(mock_steam, cache, mock_llm):
    graph = build_graph(mock_steam, cache, llm=mock_llm)
    await graph.ainvoke({"run_start": time.monotonic()})

    # First call is build_taste_profile; second is rank_and_explain.
    taste_prompt: str = mock_llm.complete_json.call_args_list[0][0][1]
    # Played library games appear in TOP PLAYED section.
    assert "TOP PLAYED" in taste_prompt
    assert "Dota 2" in taste_prompt
    # Wishlist games appear in WISHLISTED section (aspiration signal).
    assert "WISHLISTED" in taste_prompt
    assert "ELDEN RING" in taste_prompt
    # Played games should NOT appear under WISHLISTED.
    wishlist_section = taste_prompt.split("WISHLISTED")[1] if "WISHLISTED" in taste_prompt else ""
    assert "Dota 2" not in wishlist_section


async def test_no_llm_skips_taste_profile(mock_steam, cache):
    graph = build_graph(mock_steam, cache, llm=None)
    result = await graph.ainvoke({"run_start": time.monotonic()})
    assert result.get("taste_profile") is None


async def test_trending_games_included_in_profile_context(mock_steam, cache, mock_llm):
    # Trending game not in library/wishlist
    mock_steam.get_trending_appids.return_value = [9999999]
    mock_steam.enrich_many.return_value = {
        570: AppDetailsRaw(appid=570, name="Dota 2"),
        730: AppDetailsRaw(appid=730, name="CS2"),
        1245620: AppDetailsRaw(appid=1245620, name="ELDEN RING"),
        9999999: AppDetailsRaw(appid=9999999, name="Trending Game", genres=["Puzzle"]),
    }

    graph = build_graph(mock_steam, cache, llm=mock_llm)
    await graph.ainvoke({"run_start": time.monotonic()})

    taste_prompt: str = mock_llm.complete_json.call_args_list[0][0][1]
    assert "TRENDING" in taste_prompt
    assert "Trending Game" in taste_prompt


async def test_owned_trending_games_filtered_out(mock_steam, cache, mock_llm):
    # Appid 570 is in library — should not appear in trending section
    mock_steam.get_trending_appids.return_value = [570, 9999999]
    mock_steam.enrich_many.return_value = {
        570: AppDetailsRaw(appid=570, name="Dota 2"),
        730: AppDetailsRaw(appid=730, name="CS2"),
        1245620: AppDetailsRaw(appid=1245620, name="ELDEN RING"),
        9999999: AppDetailsRaw(appid=9999999, name="Trending Game"),
    }

    graph = build_graph(mock_steam, cache, llm=mock_llm)
    result = await graph.ainvoke({"run_start": time.monotonic()})

    trending = result.get("trending_games", [])
    trending_appids = [t["appid"] for t in trending]
    assert 570 not in trending_appids


# ── Rank and explain node ─────────────────────────────────────────────────────

@pytest.fixture
def mock_llm_with_picks():
    llm = AsyncMock()

    async def _fake(system, user, schema):
        if schema is TasteProfile:
            return _FAKE_PROFILE
        # _PickList — return ELDEN RING as the top pick (it's a wishlist game in the fixture)
        return schema.model_validate({
            "picks": [
                {"appid": 1245620, "rationale": "Your RPG taste and Balatro depth hours align perfectly.", "score": 92.0},
            ]
        })

    llm.complete_json.side_effect = _fake
    return llm


async def test_rank_and_explain_returns_recommendations(mock_steam, cache, mock_llm_with_picks):
    graph = build_graph(mock_steam, cache, llm=mock_llm_with_picks)
    result = await graph.ainvoke({"run_start": time.monotonic()})

    recs = result.get("recommendations", [])
    assert len(recs) == 1
    assert recs[0].rank == 1
    assert recs[0].game.appid == 1245620
    # blended: 0.70 * 92.0 (taste fit) + 0.30 * 96 (MC) = 93.2
    assert recs[0].score == pytest.approx(93.2)
    assert "RPG" in recs[0].rationale


async def test_rank_skips_unknown_appids(mock_steam, cache, mock_llm_with_picks):
    # LLM returns an appid that doesn't exist in enriched_games — should be silently skipped.
    async def _fake_with_hallucination(system, user, schema):
        if schema is TasteProfile:
            return _FAKE_PROFILE
        return schema.model_validate({
            "picks": [
                {"appid": 9999999, "rationale": "Hallucinated game.", "score": 80.0},
                {"appid": 1245620, "rationale": "Valid pick.", "score": 75.0},
            ]
        })

    mock_llm_with_picks.complete_json.side_effect = _fake_with_hallucination

    graph = build_graph(mock_steam, cache, llm=mock_llm_with_picks)
    result = await graph.ainvoke({"run_start": time.monotonic()})

    recs = result.get("recommendations", [])
    appids = [r.game.appid for r in recs]
    assert 9999999 not in appids
    assert 1245620 in appids
