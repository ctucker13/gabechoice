from langgraph.graph import StateGraph, START, END
from typing import Optional

from gabechoice.clients.steam import SteamClient
from gabechoice.cache import GameCache
from gabechoice.config import settings
from gabechoice.llm.base import LLMProvider
from gabechoice.graph.state import PipelineState
from gabechoice.graph.nodes import (
    make_fetch_library,
    make_fetch_wishlist,
    make_enrich_games,
    make_fetch_trending,
    make_build_taste_profile,
    make_rank_and_explain,
)


def build_graph(
    steam: SteamClient,
    cache: GameCache,
    llm: Optional[LLMProvider] = None,
    steam_id: Optional[str] = None,
):
    """
    Build the LangGraph pipeline.

    steam_id: per-user Steam ID 64. Falls back to settings.steam_id_64 (CLI usage).
    Without llm: fetch + enrich only (used by tests).
    With llm:    full pipeline including taste profile and ranked recommendations.
    """
    sid = steam_id or settings.steam_id_64
    if not sid:
        raise ValueError("steam_id must be provided or set via STEAM_ID_64 in .env")

    builder = StateGraph(PipelineState)

    builder.add_node("fetch_library", make_fetch_library(steam, sid))
    builder.add_node("fetch_wishlist", make_fetch_wishlist(steam, sid))
    builder.add_node("enrich_games", make_enrich_games(steam, cache))

    builder.add_edge(START, "fetch_library")
    builder.add_edge(START, "fetch_wishlist")
    builder.add_edge("fetch_library", "enrich_games")
    builder.add_edge("fetch_wishlist", "enrich_games")

    if llm is not None:
        builder.add_node("fetch_trending", make_fetch_trending(steam, cache))
        builder.add_node("build_taste_profile", make_build_taste_profile(llm))
        builder.add_node("rank_and_explain", make_rank_and_explain(llm))
        builder.add_edge("enrich_games", "fetch_trending")
        builder.add_edge("fetch_trending", "build_taste_profile")
        builder.add_edge("build_taste_profile", "rank_and_explain")
        builder.add_edge("rank_and_explain", END)
    else:
        builder.add_edge("enrich_games", END)

    return builder.compile()
