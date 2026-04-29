from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, Literal


class Game(BaseModel):
    appid: int
    name: str
    playtime_minutes: int = 0
    playtime_2weeks: int = 0  # minutes played in last 14 days; 0 if not recently active
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    short_description: Optional[str] = None
    metacritic_score: Optional[int] = None
    metacritic_url: Optional[HttpUrl] = None
    header_image: Optional[HttpUrl] = None
    store_url: HttpUrl  # always synthesizable from appid
    price_cents: Optional[int] = None
    currency: Optional[str] = None
    source: Literal["library", "wishlist"]
    cached_at: Optional[str] = None  # ISO timestamp


class TasteProfile(BaseModel):
    preferred_genres: list[str]
    preferred_mechanics: list[str]
    vibes: list[str]            # e.g. "atmospheric", "competitive", "cozy"
    wildcard_picks: list[str]   # genres/mechanics outside their pattern that could genuinely surprise them
    summary: str                # 2-3 sentence prose summary


class Recommendation(BaseModel):
    game: Game
    score: float                # 0-100, blended
    rationale: str              # 1-2 sentences
    rank: int


class RunResult(BaseModel):
    taste_profile: TasteProfile
    recommendations: list[Recommendation]
    games_considered: int
    cache_hits: int
    cache_misses: int
    run_seconds: float
