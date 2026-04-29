from typing import TypedDict, Optional
from gabechoice.models import Game, TasteProfile, Recommendation


class PipelineState(TypedDict, total=False):
    library_games: list[Game]
    wishlist_games: list[Game]
    enriched_games: list[Game]
    taste_profile: Optional[TasteProfile]
    trending_games: list  # enrichment dicts for trending/new games not in library or wishlist
    recommendations: list[Recommendation]
    cache_hits: int
    cache_misses: int
    run_start: float
    error: Optional[str]
