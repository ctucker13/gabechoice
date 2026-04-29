# GabeChoice

> *Gaben picks your next game.*

GabeChoice is a personal agentic Steam recommendation tool. It reads your library and wishlist, builds a taste profile from your play history, and produces ranked recommendations with LLM-generated reasoning — all running locally against your own API keys.

---

## Features

- **Parallel Steam fetching** — library and wishlist fetched concurrently
- **SQLite enrichment cache** — genres, tags, Metacritic scores cached per-appid; reruns are fast
- **5-signal taste profile** — recently played, top played, owned unplayed, wishlisted, and trending games all inform the LLM
- **Blended scoring** — 70% LLM taste-fit + 30% Metacritic quality signal
- **Recent-play awareness** — games played in the last 2 weeks are surfaced as the strongest current-intent signal
- **Multi-user** — Steam OpenID login or manual Steam ID entry; each user's session is independent
- **CLI + web** — rich terminal output or a Command Deck web frontend
- **Provider-agnostic LLM** — Anthropic (default) or OpenAI, swappable via config

---

## Setup

```bash
git clone https://github.com/ctucker13/gabechoice.git
cd gabechoice
uv sync
cp .env.example .env
```

Edit `.env` with your credentials:

```
STEAM_API_KEY=your_steam_api_key
STEAM_ID_64=your_steam_id_64        # CLI only; web users log in via Steam
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_anthropic_key
SECRET_KEY=your_random_secret       # sessions — generate with: python -c "import secrets; print(secrets.token_hex(32))"
```

- **Steam API key:** https://steamcommunity.com/dev/apikey
- **Steam ID 64:** https://steamid.io/
- Your Steam profile and wishlist must be set to **Public**

> **Security:** when generating your Steam API key, watch for browser extensions that silently capture it. If you see such a message, revoke the key immediately and generate a new one.

---

## Running

### Web (recommended)

```bash
uv run uvicorn gabechoice.api.main:app --reload --port 8000
```

Open `http://localhost:8000`, sign in with Steam (or enter your Steam ID 64 manually), then click **Ask Gaben**.

### CLI

```bash
uv run python src/gabechoice/cli.py
```

Options:
- `--limit N` / `-n N` — rows to show in the top-played table (default 15)
- `--no-llm` — fetch and enrich only, skip taste profile and recommendations

---

## Architecture

```
Frontend (vanilla JS)
       │
       ▼
FastAPI  /api/recommend
       │
       ▼
LangGraph pipeline
       │
   ┌───┴────────────────┐
   ▼                    ▼
fetch_library      fetch_wishlist     ← parallel
   └───┬────────────────┘
       ▼
  enrich_games          ← Steam appdetails + SQLite cache
       ▼
  fetch_trending        ← Steam featured categories (not in library)
       ▼
build_taste_profile     ← LLM: 5-signal taste fingerprint
       ▼
rank_and_explain        ← LLM: blended score + rationale per game
```

See [AGENTS.md](AGENTS.md) for a detailed breakdown of each node and the prompting strategy.

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `STEAM_API_KEY` | — | Required. Steam Web API key |
| `STEAM_ID_64` | — | Required for CLI. Your 64-bit Steam ID |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` | — | Required if using Anthropic |
| `OPENAI_API_KEY` | — | Required if using OpenAI |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model ID |
| `OPENAI_MODEL` | `gpt-4o` | Model ID |
| `SECRET_KEY` | dev default | Session signing key — change for any networked use |
| `CACHE_DB_PATH` | `./cache.sqlite` | SQLite cache location |
| `CACHE_TTL_DAYS` | `30` | Days before cached enrichment is re-fetched |
| `TOP_N_RECOMMENDATIONS` | `10` | How many recommendations to generate |
| `STEAM_APPDETAILS_RPS` | `0.5` | Steam API rate limit (req/s) |

---

## Tests

```bash
uv run pytest tests/ -v
```

24 tests covering cache, Steam client, and the full LangGraph pipeline with mocked clients.

---

## Known limitations

- **Metacritic coverage** is ~60–75% on a typical wishlist; unscored games are still ranked on taste fit alone
- **Wishlist endpoint** — Steam has changed `IWishlistService/GetWishlist` before; if it returns nothing the client returns an empty list gracefully
- **Cold cache** — first run fetches appdetails for every game at 0.5 req/s; 400+ games takes ~15 minutes. Subsequent runs are fast.
- **LLM latency** — the two LLM calls add 20–60 seconds depending on library size

---

## Roadmap

- [ ] "Not Interested" / "Ask Why" button wiring
- [ ] IGDB discovery — games outside library and wishlist
- [ ] Persistent preferences across sessions
- [ ] Shareable taste profile URL
- [ ] OpenCritic as Metacritic fallback
