import httpx
from pydantic import BaseModel, Field
from typing import Optional

from gabechoice.config import settings
from gabechoice.rate_limit import TokenBucket


class OwnedGameRaw(BaseModel):
    appid: int
    name: str = ""
    playtime_forever: int = 0
    playtime_2weeks: int = 0


class WishlistEntryRaw(BaseModel):
    appid: int
    priority: int = 0


class AppDetailsRaw(BaseModel):
    appid: int
    name: str = ""
    short_description: str = ""
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)   # categories from Steam
    metacritic_score: Optional[int] = None
    metacritic_url: Optional[str] = None
    header_image: Optional[str] = None
    price_cents: Optional[int] = None
    currency: Optional[str] = None

    def to_enrichment_dict(self) -> dict:
        return {
            "name": self.name,
            "genres": self.genres,
            "tags": self.tags,
            "short_description": self.short_description,
            "metacritic_score": self.metacritic_score,
            "metacritic_url": self.metacritic_url,
            "header_image": self.header_image,
            "price_cents": self.price_cents,
            "currency": self.currency,
        }


def _store_url(appid: int) -> str:
    return f"https://store.steampowered.com/app/{appid}/"


class SteamClient:
    BASE = "https://api.steampowered.com"
    STORE = "https://store.steampowered.com/api"

    def __init__(self, api_key: str, *, _client: httpx.AsyncClient | None = None):
        self._api_key = api_key
        self._owns_client = _client is None
        self._client = _client or httpx.AsyncClient(timeout=30.0)
        self._rate_limiter = TokenBucket(settings.steam_appdetails_rps)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "SteamClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()

    async def get_owned_games(self, steam_id: str) -> list[OwnedGameRaw]:
        from gabechoice.models import Game  # avoid circular at module level
        resp = await self._client.get(
            f"{self.BASE}/IPlayerService/GetOwnedGames/v1/",
            params={
                "key": self._api_key,
                "steamid": steam_id,
                "include_appinfo": 1,
                "include_played_free_games": 1,
            },
        )
        resp.raise_for_status()
        games = resp.json().get("response", {}).get("games", [])
        return [OwnedGameRaw.model_validate(g) for g in games]

    async def get_wishlist(self, steam_id: str) -> list[WishlistEntryRaw]:
        resp = await self._client.get(
            f"{self.BASE}/IWishlistService/GetWishlist/v1/",
            params={"key": self._api_key, "steamid": steam_id},
        )
        resp.raise_for_status()
        items = resp.json().get("response", {}).get("items", [])

        # Fallback: IWishlistService returned empty — try public wishlistdata scrape.
        # If needed, swap this method body to hit:
        #   https://store.steampowered.com/wishlist/profiles/{steam_id}/wishlistdata/
        # That endpoint returns a dict keyed by appid; parse accordingly.
        if not items:
            return []

        return [WishlistEntryRaw(appid=int(i["appid"]), priority=i.get("priority", 0)) for i in items]

    async def get_appdetails(self, appid: int) -> AppDetailsRaw | None:
        import asyncio as _asyncio
        for attempt in range(4):
            resp = await self._client.get(
                f"{self.STORE}/appdetails",
                params={"appids": appid, "cc": "us", "l": "en"},
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 0))
                wait = retry_after if retry_after else 30 * (2 ** attempt)
                print(f"\n  ⚠  429 on {appid} — waiting {wait}s (attempt {attempt + 1}/3)…", flush=True)
                await _asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json().get(str(appid), {})
            if not data.get("success"):
                return None
            d = data["data"]
            metacritic = d.get("metacritic") or {}
            price = d.get("price_overview") or {}
            return AppDetailsRaw(
                appid=appid,
                name=d.get("name", ""),
                short_description=d.get("short_description", ""),
                genres=[g["description"] for g in d.get("genres") or []],
                tags=[c["description"] for c in d.get("categories") or []],
                metacritic_score=metacritic.get("score"),
                metacritic_url=metacritic.get("url"),
                header_image=d.get("header_image"),
                price_cents=price.get("final"),
                currency=price.get("currency"),
            )
        return None  # all retries exhausted

    async def get_trending_appids(self) -> list[int]:
        """Appids from Steam's current top sellers and new releases (no auth required)."""
        try:
            resp = await self._client.get(
                "https://store.steampowered.com/api/featuredcategories/",
                params={"cc": "us", "l": "en"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        appids: list[int] = []
        for category in ("top_sellers", "new_releases"):
            for item in (data.get(category) or {}).get("items", []):
                appid = item.get("id")
                if appid:
                    appids.append(int(appid))
        return appids

    async def enrich_many(self, appids: list[int]) -> dict[int, AppDetailsRaw]:
        import sys
        from rich.progress import Progress, BarColumn, MofNCompleteColumn, TimeRemainingColumn, TextColumn
        results: dict[int, AppDetailsRaw] = {}
        disabled = not sys.stderr.isatty()
        with Progress(
            TextColumn("  [dim]Enriching[/]"),
            BarColumn(bar_width=36, style="cyan", complete_style="bright_cyan"),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            disable=disabled,
            transient=False,
        ) as progress:
            task = progress.add_task("", total=len(appids))
            for appid in appids:
                await self._rate_limiter.acquire()
                details = await self.get_appdetails(appid)
                if details is not None:
                    results[appid] = details
                progress.advance(task)
        return results
