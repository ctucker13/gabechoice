# GabeChoice — Agent Architecture

This document describes the LangGraph pipeline, each node's role, the prompting strategy, signal weighting, and how to extend the system.

---

## Pipeline overview

The pipeline is a directed acyclic graph compiled by LangGraph. Each node is an async function that receives the shared `PipelineState` and returns a partial state update.

```
START
  ├── fetch_library
  └── fetch_wishlist
        └── enrich_games
              └── fetch_trending
                    └── build_taste_profile
                          └── rank_and_explain
                                └── END
```

`fetch_library` and `fetch_wishlist` run in **parallel** from START. LangGraph fans them into `enrich_games` once both complete. All subsequent nodes are sequential.

**Entry point:** `gabechoice/graph/builder.py::build_graph(steam, cache, llm, steam_id)`

**State schema:** `gabechoice/graph/state.py::PipelineState`

---

## Node reference

### `fetch_library`
**File:** `nodes.py::make_fetch_library`

Calls `IPlayerService/GetOwnedGames` with `include_appinfo=1` and `include_played_free_games=1`. Returns a list of `Game` objects with `source="library"`, `playtime_minutes` (all-time), and `playtime_2weeks` (last 14 days).

`playtime_2weeks` is the key recency signal — it is used by downstream nodes to identify games the user is actively playing even if total hours are low.

**Output state key:** `library_games`

---

### `fetch_wishlist`
**File:** `nodes.py::make_fetch_wishlist`

Calls `IWishlistService/GetWishlist`. Returns `Game` objects with `source="wishlist"` and empty playtime fields. Name and metadata are filled in by `enrich_games`.

**Output state key:** `wishlist_games`

---

### `enrich_games`
**File:** `nodes.py::make_enrich_games`

Merges library and wishlist into a unique appid set (library takes precedence on overlap — it carries real playtime). For each appid:

1. Check SQLite cache (`cache.get_many`)
2. For cache misses, call `SteamClient.enrich_many` which hits `store.steampowered.com/api/appdetails`
3. Write new enrichment to cache with a `cached_at` timestamp
4. Merge enrichment into the `Game` object, preserving `source`, `playtime_minutes`, and `playtime_2weeks`

The cache stores enrichment data only (genres, tags, Metacritic score, description, price, header image). Per-run fields like playtime are always live from Steam.

**Output state keys:** `enriched_games`, `cache_hits`, `cache_misses`

---

### `fetch_trending`
**File:** `nodes.py::make_fetch_trending`

Calls `store.steampowered.com/api/featuredcategories` (no auth required) and extracts appids from `top_sellers` and `new_releases`. Filters out any appid already in the user's library or wishlist, then enriches up to 15 cache misses.

Trending games appear in the taste profile prompt as a **gap context** signal — not for direct recommendation, but to show the LLM what popular games the user is missing and whether that absence is meaningful.

**Output state key:** `trending_games` (list of enrichment dicts, not `Game` objects)

---

### `build_taste_profile`
**File:** `nodes.py::make_build_taste_profile`

**LLM node.** Constructs a multi-section prompt from five data sources and calls `llm.complete_json` to produce a structured `TasteProfile`.

#### Signal sources (in priority order)

| Section | Source | Weight |
|---|---|---|
| RECENTLY PLAYED | `playtime_2weeks > 0` | Strongest current signal |
| TOP PLAYED | Top 15 by `playtime_minutes` | Strong all-time signal |
| OWNED UNPLAYED | `playtime_minutes == 0`, source=library | Purchase-intent signal |
| WISHLISTED | source=wishlist | Aspiration signal |
| TRENDING / NEW | `fetch_trending` output, not owned | Gap context |

The system prompt instructs the model to weight signals in this order, with special emphasis on recently-played games as the most reliable indicator of current taste direction.

#### Output schema

```python
class TasteProfile(BaseModel):
    preferred_genres: list[str]       # e.g. "Tactical FPS", "Roguelike Deckbuilder"
    preferred_mechanics: list[str]    # e.g. "Aim training", "Deckbuilding with probability"
    vibes: list[str]                  # e.g. "Mastery-driven", "Social but selective"
    wildcard_picks: list[str]         # genres outside their pattern that could genuinely click
    summary: str                      # 3-5 sentence prose synthesis of all five signals
```

`wildcard_picks` (formerly `anti_patterns`) uses positive framing — these are suggestions for genres the user hasn't tried but whose underlying mechanics map to proven motivations in their profile.

**Output state key:** `taste_profile`

---

### `rank_and_explain`
**File:** `nodes.py::make_rank_and_explain`

**LLM node.** Takes the taste profile and all enriched games, splits them into candidate pools, and asks the LLM to rank the top N.

#### Candidate pools

- **Owned unplayed** — library games with `playtime_minutes == 0`, sorted by Metacritic score descending (top 60 sent)
- **Wishlisted** — all wishlist games, sorted by Metacritic score descending (top 60 sent)

The prompt also includes a CURRENTLY ACTIVE section listing any `playtime_2weeks > 0` games so the LLM has momentum context when reasoning about adjacent recommendations.

#### Scoring

The LLM is instructed to return a **pure taste-fit score** (0–100) without factoring in Metacritic. After the LLM responds, a guaranteed mathematical blend is applied server-side:

```
final_score = 0.70 × taste_fit + 0.30 × metacritic
```

When no Metacritic score is available, 65 is used as a neutral baseline (doesn't penalise unknown indie games). The final list is re-sorted by blended score before ranks are assigned.

The LLM is over-fetched (`n * 2` picks requested) so that after filtering out any hallucinated appids, at least `n` valid recommendations remain.

#### Output schema

```python
class Recommendation(BaseModel):
    game: Game
    score: float    # blended 0-100
    rationale: str  # 1-2 sentences citing specific profile signals
    rank: int
```

**Output state key:** `recommendations`

---

## LLM provider abstraction

**File:** `gabechoice/llm/base.py`

```python
class LLMProvider(Protocol):
    async def complete_json(
        self,
        system: str,
        user: str,
        response_model: type[BaseModel],
    ) -> BaseModel: ...
```

Both providers use native structured output:
- **Anthropic:** tool-forcing (`tool_choice={"type":"tool","name":"respond"}`) with the Pydantic schema as the tool input schema. System prompt uses `cache_control: ephemeral` for prompt caching.
- **OpenAI:** `response_format={"type": "json_schema", ...}` with the Pydantic schema.

Provider is selected at startup from `settings.llm_provider`. Neither node knows which provider is active.

---

## State schema

```python
class PipelineState(TypedDict, total=False):
    library_games: list[Game]
    wishlist_games: list[Game]
    enriched_games: list[Game]
    taste_profile: Optional[TasteProfile]
    trending_games: list                  # enrichment dicts, not Game objects
    recommendations: list[Recommendation]
    cache_hits: int
    cache_misses: int
    run_start: float
    error: Optional[str]
```

All keys are optional (`total=False`). Nodes return partial dicts; LangGraph merges them into the running state.

---

## Extending the pipeline

### Adding a new node

1. Write a factory function in `nodes.py`: `def make_my_node(...) -> Callable`
2. The inner async function receives `state: PipelineState` and returns a `dict` of state updates
3. Register it in `builder.py`: `builder.add_node("my_node", make_my_node(...))`
4. Wire edges: `builder.add_edge("previous_node", "my_node")`

### Adding a new signal source

Add it to the `build_taste_profile` prompt sections and update `_TASTE_SYSTEM` to describe its weight relative to existing signals.

### Swapping the LLM

Implement `complete_json` in a new file under `gabechoice/llm/`, add a branch in `gabechoice/llm/__init__.py::get_llm()`, and set `LLM_PROVIDER` in `.env`.

### Changing scoring weights

The blend formula is in `rank_and_explain` in `nodes.py`:

```python
blended = round(0.70 * pick.score + 0.30 * mc, 1)
```

Adjust the coefficients directly. They must sum to 1.0.

---

## Rate limiting

**File:** `gabechoice/rate_limit.py`

A token bucket with a virtual-clock pattern. On deficit, `self._last` is advanced by the wait duration rather than sleeping — this prevents cascading waits when multiple concurrent callers arrive during a deficit period. Each caller calculates their own wait from the shared virtual clock.

Steam's `appdetails` endpoint is the only rate-limited call. Default: 0.5 req/s (~150/5min, safely under Steam's undocumented ~200/5min limit).

---

## Testing

Tests use `pytest-asyncio` in `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed).

- **`test_cache.py`** — SQLite cache CRUD and TTL pruning
- **`test_steam_client.py`** — httpx mock fixtures covering owned games, wishlist, appdetails, and 429 retry
- **`test_graph.py`** — full pipeline with `AsyncMock` Steam client and LLM; covers cache hits, deduplication, taste profile signals, trending filtering, rank output, and hallucinated appid handling

The `mock_llm` fixture uses `side_effect` to return the correct schema type per call position (taste profile on call 1, `_PickList` on call 2).
