import re
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from gabechoice.models import RunResult
from gabechoice.config import settings
from gabechoice.cache import GameCache
from gabechoice.clients.steam import SteamClient
from gabechoice.graph.builder import build_graph
from gabechoice.llm import get_llm

_cache: GameCache | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cache
    _cache = GameCache(settings.cache_db_path)
    await _cache.init_db()
    yield


app = FastAPI(title="GabeChoice", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)


# ── Steam OpenID helpers ──────────────────────────────────────────────────────

def _steam_login_url(return_to: str, realm: str) -> str:
    params = {
        "openid.mode": "checkid_setup",
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.return_to": return_to,
        "openid.realm": realm,
    }
    return "https://steamcommunity.com/openid/login?" + urlencode(params)


async def _verify_openid(params: dict) -> str | None:
    """Returns Steam ID 64 string if assertion is valid, else None."""
    verify_params = {**params, "openid.mode": "check_authentication"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://steamcommunity.com/openid/login",
            data=verify_params,
        )
    if "is_valid:true" not in resp.text:
        return None
    claimed = params.get("openid.claimed_id", "")
    m = re.search(r"/openid/id/(\d+)$", claimed)
    return m.group(1) if m else None


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/auth/steam/login")
async def steam_login(request: Request):
    base = str(request.base_url).rstrip("/")
    return_to = f"{base}/auth/steam/callback"
    return RedirectResponse(_steam_login_url(return_to=return_to, realm=base))


@app.post("/auth/manual")
async def manual_login(request: Request) -> dict:
    body = await request.json()
    steam_id = str(body.get("steam_id", "")).strip()
    if not steam_id.isdigit() or len(steam_id) < 10:
        raise HTTPException(status_code=400, detail="Invalid Steam ID")
    request.session["steam_id"] = steam_id
    return {"ok": True, "steam_id": steam_id}


@app.get("/auth/steam/callback")
async def steam_callback(request: Request):
    steam_id = await _verify_openid(dict(request.query_params))
    if not steam_id:
        raise HTTPException(status_code=401, detail="Steam OpenID verification failed")
    request.session["steam_id"] = steam_id
    return RedirectResponse("/")


@app.post("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request) -> dict:
    steam_id = request.session.get("steam_id")
    if not steam_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    return {"steam_id": steam_id}


# ── Pipeline ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/recommend")
async def recommend(request: Request) -> RunResult:
    steam_id = request.session.get("steam_id")
    if not steam_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    start = time.monotonic()
    llm = get_llm()

    async with SteamClient(settings.steam_api_key) as steam:
        graph = build_graph(steam, _cache, llm=llm, steam_id=steam_id)
        result = await graph.ainvoke({"run_start": start})

    taste_profile = result.get("taste_profile")
    if taste_profile is None:
        raise HTTPException(status_code=500, detail="Pipeline did not produce a taste profile")

    return RunResult(
        taste_profile=taste_profile,
        recommendations=result.get("recommendations", []),
        games_considered=len(result.get("enriched_games", [])),
        cache_hits=result.get("cache_hits", 0),
        cache_misses=result.get("cache_misses", 0),
        run_seconds=round(time.monotonic() - start, 1),
    )


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
