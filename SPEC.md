# GabeChoice — Build Spec

A personal agentic tool that reads a user's Steam library and wishlist, enriches each game with Metacritic scores and metadata via Steam's own APIs, builds a taste profile from play history, and produces ranked recommendations with LLM-generated reasoning and Steam store links.

Tagline: *Gaben picks your next game.*

This document is a complete handoff spec. It is intended to be pasted into Claude Code (or a similar tool) as the seed prompt for an iterative build.

---

## 1. Goals and non-goals

**Goals**
- Surface games from the user's wishlist worth playing next, ranked by quality + taste fit
- Surface games from the user's library that are similar in spirit to recently-played favorites (rediscovery)
- Produce a short, plain-language "why you'd like this" for each recommendation
- Link out to the Steam store for one-click access
- Cache aggressively so reruns are fast and cheap

**Non-goals (v1)**
- Discovery of games outside the user's library and wishlist (IGDB integration is stubbed for v2)
- Multi-user support, auth, or hosting — this is a single-user local tool
- Price tracking, sale alerts, deal hunting (out of scope)
- Mobile UI (desktop browser only)

---

## 2. Architecture overview

Agentic pipeline orchestrated with LangGraph. FastAPI exposes a single endpoint that triggers a run; a minimal vanilla-JS frontend renders the results.

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────┐
│  Frontend   │────▶│  FastAPI        │────▶│  LangGraph   │
│ (vanilla JS)│     │  /api/recommend │     │  pipeline    │
└─────────────┘     └─────────────────┘     └──────┬───────┘
                                                   │
                    ┌──────────────────────────────┼─────────────────────────────┐
                    ▼                              ▼                             ▼
             ┌─────────────┐              ┌─────────────────┐            ┌──────────────┐
             │ Steam API   │              │  SQLite cache   │            │  LLM         │
             │ (library,   │              │  (enriched      │            │ (Anthropic   │
             │ wishlist,   │              │  game data)     │            │  or OpenAI)  │
             │ appdetails) │              └─────────────────┘            └──────────────┘
             └─────────────┘
```

### LangGraph node flow

1. **`fetch_library`** — calls `IPlayerService/GetOwnedGames`. Returns appids + playtime.
2. **`fetch_wishlist`** — calls `IWishlistService/GetWishlist`. Returns appids + priority.
3. *(1 and 2 run in parallel via LangGraph's parallel-edge pattern.)*
4. **`enrich_games`** — for every appid not already cached, call Steam Store `appdetails` to get name, genre, tags, short description, price, header image, store URL, and embedded Metacritic score. Write everything to SQLite. Honor Steam's rate limit (~200 req / 5min) with a token-bucket limiter.
5. **`build_taste_profile`** — LLM node. Input: top 20 most-played library games (name, genre, tags, playtime). Output: structured `TasteProfile` (preferred genres, mechanics, vibes, anti-patterns) as JSON.
6. **`rank_and_explain`** — LLM node. Input: full candidate set (wishlist + a sample of library games for rediscovery) + taste profile + Metacritic scores. Output: top-N ranked list, each with a 1–2 sentence rationale. Ranking blends LLM judgment with the Metacritic score; the LLM is instructed to weigh both but prioritize taste fit.
7. **`format_output`** — packages results into the response schema.

### Why this shape

- Parallel fetches save wall-clock time on the slow part (network).
- Enrichment is the expensive step; caching by appid means subsequent runs only hit Steam for new wishlist additions or new library games.
- Splitting taste profile from ranking lets the taste profile be inspected, tweaked, or even pinned manually if the user wants.
- Keeping discovery (IGDB) out of v1 means the core loop ships fast and the user has something usable before any external dependency is added.

---

## 3. Tech stack

- **Python 3.12+**
- **uv** for package management and venvs
- **Pydantic v2** for all data models and config
- **LangGraph** for orchestration
- **httpx** (async) for all HTTP calls
- **FastAPI** + **uvicorn** for the API
- **SQLite** (via `sqlite3` stdlib or `aiosqlite` if we want async) for the cache
- **Anthropic SDK** and **OpenAI SDK** behind a thin provider abstraction
- **python-dotenv** for local config
- Vanilla HTML/CSS/JS for the frontend (no build step, served as static files by FastAPI)

No LangChain runtime needed — LangGraph alone is enough.

---

## 4. Project structure

```
gabechoice/
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
├── README.md
├── SPEC.md                      # this file
├── src/gabechoice/
│   ├── __init__.py
│   ├── config.py                # Settings (Pydantic BaseSettings)
│   ├── models.py                # Game, TasteProfile, Recommendation, RunResult
│   ├── cache.py                 # SQLite wrapper, schema, get/set/list
│   ├── rate_limit.py            # token bucket for Steam appdetails
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py              # LLMProvider protocol
│   │   ├── anthropic_provider.py
│   │   └── openai_provider.py
│   ├── clients/
│   │   ├── __init__.py
│   │   ├── steam.py             # owned games, wishlist, appdetails
│   │   └── igdb.py              # stubbed for v2
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── state.py             # PipelineState TypedDict
│   │   ├── nodes.py             # all node functions
│   │   └── builder.py           # graph construction + compile
│   ├── api/
│   │   ├── __init__.py
│   │   └── main.py              # FastAPI app, /api/recommend, static mount
│   └── cli.py                   # optional: run pipeline from terminal for debugging
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
└── tests/
    ├── test_cache.py
    ├── test_steam_client.py     # uses httpx mocks
    └── test_graph.py            # smoke test with stubbed clients
```

Package import path: `from gabechoice.config import settings`, etc.

---

## 5. Data models

All in `src/gabechoice/models.py`. Pydantic v2.

```python
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, Literal

class Game(BaseModel):
    appid: int
    name: str
    playtime_minutes: int = 0
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
    anti_patterns: list[str]    # things the user seems to avoid
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
```

---

## 6. Configuration

`src/gabechoice/config.py` using `pydantic-settings`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    steam_api_key: str
    steam_id_64: str

    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    openai_model: str = "gpt-4o"

    cache_db_path: str = "./cache.sqlite"
    cache_ttl_days: int = 30

    top_n_recommendations: int = 10
    library_sample_for_rediscovery: int = 15
    steam_appdetails_rps: float = 1.0  # ~200/5min = 0.66/s, leave headroom

settings = Settings()
```

`.env.example`:

```
STEAM_API_KEY=
STEAM_ID_64=
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

`.gitignore` should include at minimum: `.env`, `cache.sqlite`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`. Commit `uv.lock` for reproducibility.

---

## 7. SQLite cache

Single table, keyed by appid. Stale entries (older than `cache_ttl_days`) are re-fetched.

```sql
CREATE TABLE IF NOT EXISTS games (
    appid INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,         -- JSON-serialized Game (without source/playtime)
    cached_at TEXT NOT NULL        -- ISO timestamp
);
CREATE INDEX IF NOT EXISTS idx_cached_at ON games(cached_at);
```

The cache stores the *enrichment* (genres, metacritic, description, etc.), not the per-run fields like `playtime_minutes` or `source`. Those are merged in at request time.

API surface in `cache.py`:

- `get(appid: int) -> dict | None`
- `set(appid: int, enrichment: dict) -> None`
- `get_many(appids: list[int]) -> dict[int, dict]`
- `prune_stale(ttl_days: int) -> int` (returns count pruned)

---

## 8. Steam client

`src/gabechoice/clients/steam.py`. All methods async, all use a shared `httpx.AsyncClient`.

```python
class SteamClient:
    BASE = "https://api.steampowered.com"
    STORE = "https://store.steampowered.com/api"

    async def get_owned_games(self, steam_id: str) -> list[OwnedGameRaw]: ...
    async def get_wishlist(self, steam_id: str) -> list[WishlistEntryRaw]: ...
    async def get_appdetails(self, appid: int) -> AppDetailsRaw | None: ...
    async def enrich_many(self, appids: list[int]) -> dict[int, AppDetailsRaw]: ...
```

Endpoints:
- `GET {BASE}/IPlayerService/GetOwnedGames/v1/?key={key}&steamid={id}&include_appinfo=1&include_played_free_games=1`
- `GET {BASE}/IWishlistService/GetWishlist/v1/?key={key}&steamid={id}`
- `GET {STORE}/appdetails?appids={appid}&cc=us&l=en`

Rate-limit `appdetails` calls with a token bucket at `settings.steam_appdetails_rps`. Retry with exponential backoff on 429s.

The `metacritic` block in `appdetails` looks like:
```json
"metacritic": { "score": 96, "url": "https://www.metacritic.com/game/pc/elden-ring" }
```
Both fields are optional and frequently absent. Treat missing as `None`, not as zero.

Store URL is always `https://store.steampowered.com/app/{appid}/`.

---

## 9. LLM provider abstraction

`src/gabechoice/llm/base.py`:

```python
from typing import Protocol
from pydantic import BaseModel

class LLMProvider(Protocol):
    async def complete_json(
        self,
        system: str,
        user: str,
        response_model: type[BaseModel],
    ) -> BaseModel: ...
```

Both providers implement structured-output via their respective tool/JSON-mode features. Anthropic: use a tool with the response schema and force tool use. OpenAI: use `response_format={"type": "json_schema", ...}`.

The graph nodes only ever call `complete_json` — they don't know which provider is behind it. Provider is selected at startup based on `settings.llm_provider`.

---

## 10. Graph nodes (key behaviors)

### `fetch_library`
- Calls Steam, returns list of `Game(source="library")` with appid, name, playtime_minutes only.
- All other fields filled in by `enrich_games`.

### `fetch_wishlist`
- Same pattern, `source="wishlist"`.

### `enrich_games`
- Merges library + wishlist into a unique appid set.
- Cache lookup first; only hit Steam for misses.
- Updates `state.cache_hits` and `state.cache_misses` for the run summary.

### `build_taste_profile`
- Takes the top N most-played library games (where N = `library_sample_for_rediscovery`).
- Sends to LLM with a system prompt like:

  > You are analyzing a Steam user's play history to build a taste profile. Given the games below with playtime, identify the genres, mechanics, and vibes the user gravitates toward and any clear anti-patterns. Be specific — "tactical FPS" is more useful than "shooter". Return JSON matching the TasteProfile schema.

- Output is a `TasteProfile`.

### `rank_and_explain`
- Candidate set = full wishlist + a sample of library games not in the "top played" set (for rediscovery).
- Sends to LLM with the taste profile and the candidate list including Metacritic scores and short descriptions.
- System prompt:

  > Rank these games for the user based on their taste profile and the available quality signals. Metacritic score is a useful signal but secondary to taste fit — a 75-rated game that perfectly matches their taste should beat an 88-rated game in a genre they don't play. For each of the top {top_n} games, write a 1-2 sentence rationale grounded in the taste profile. Return JSON matching the schema.

- Output is `list[Recommendation]`.

### `format_output`
- Assembles the `RunResult`, including run timing and cache stats.

---

## 11. FastAPI surface

`src/gabechoice/api/main.py`:

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="GabeChoice")

@app.post("/api/recommend")
async def recommend() -> RunResult:
    """Trigger a full pipeline run and return ranked recommendations."""

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
```

For v1, the endpoint takes no parameters — it uses the configured Steam ID. v2 could parameterize on user.

---

## 12. Frontend (minimal)

Single `index.html` with an "Ask Gaben" button. On click, POST to `/api/recommend`, render a list of cards: header image, title, Metacritic score badge, rationale, and a "View on Steam" link.

Show a loading spinner while the pipeline runs (it can take 30–90s on a cold cache).

Display the taste profile summary at the top so the user can see what the LLM thinks of them — useful debug surface and a fun feature.

No framework. Plain `fetch()`, plain DOM manipulation. The whole thing should be under 200 lines.

Page title: "GabeChoice — your next game, blessed by Gaben."

---

## 13. Build order (suggested)

Build the working slice end-to-end before adding polish.

1. **Scaffolding** — `uv init gabechoice`, dependencies, project structure, `.env.example`, basic `README.md`.
2. **Models + config + cache** — get these solid, with unit tests for the cache.
3. **Steam client** — implement and test against the real API with the user's key. Verify wishlist endpoint shape (it's the most likely thing to have changed).
4. **LLM abstraction** — implement Anthropic provider first, add OpenAI second.
5. **Graph: fetch + enrich only** — no LLM yet. Run the graph, confirm the cache fills up, confirm we get clean `Game` objects.
6. **Graph: taste profile** — add the LLM node, eyeball the output for the user's actual library.
7. **Graph: rank + explain** — full path. Tune the prompt.
8. **FastAPI + frontend** — wrap it up, render in a browser.
9. **Polish** — caching TTL, error handling, retry logic, run summary in the UI.

Defer until v2: IGDB discovery, multi-user, price/discount signals, OpenCritic fallback for missing Metacritic scores, Steam review percentage as a secondary signal.

---

## 14. Setup commands (for the README)

```bash
# clone, then:
uv sync
cp .env.example .env
# edit .env with Steam API key, Steam ID, and one LLM provider key

# run
uv run uvicorn gabechoice.api.main:app --reload --port 8000
# open http://localhost:8000
```

To get a Steam API key: https://steamcommunity.com/dev/apikey
To find your SteamID64: https://steamid.io/

The user's Steam profile and wishlist must be set to public for the API to return data.

**Security note:** when generating your Steam API key, watch for any browser extension messages claiming to have auto-saved the key. Several "trader" extensions silently capture API keys and use them to hijack trades. If you see such a message, revoke the key immediately, uninstall the extension, and generate a new one. Never paste your Steam API key into any browser extension.

---

## 15. Things known to be fragile

- **Wishlist endpoint** — Steam has changed this twice in recent years. If `IWishlistService/GetWishlist` returns nothing, the fallback is the public profile scrape at `store.steampowered.com/wishlist/profiles/{steamid}/wishlistdata/`. Keep the client interface clean so swapping is one method.
- **Metacritic coverage** — expect 60–75% coverage on a typical wishlist. Games without scores should still be ranked, just without that signal. The LLM node should handle `metacritic_score=None` gracefully.
- **Steam appdetails rate limit** — undocumented but real. If 429s start, slow down. The token bucket should be tuned conservatively.
- **LLM JSON output** — both providers occasionally return malformed JSON or trailing text. Use the structured-output features (tools / json_schema mode), not freeform prompting.

---

## 16. First message to send to Claude Code

> Read SPEC.md in the repo root. Build the project as specified, with package name `gabechoice`. Start with section 13 step 1 (scaffolding) and stop after step 5 (graph: fetch + enrich only) so I can verify the foundation before we add LLM nodes. Use uv for everything. Ask me before installing any dependency not listed in section 3.
