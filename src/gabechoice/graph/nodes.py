from typing import Callable
from pydantic import BaseModel
from gabechoice.clients.steam import SteamClient
from gabechoice.cache import GameCache
from gabechoice.config import settings
from gabechoice.models import Game, TasteProfile, Recommendation
from gabechoice.llm.base import LLMProvider
from gabechoice.graph.state import PipelineState


def _store_url(appid: int) -> str:
    return f"https://store.steampowered.com/app/{appid}/"


def make_fetch_library(steam: SteamClient, steam_id: str) -> Callable:
    async def fetch_library(state: PipelineState) -> dict:
        raw_games = await steam.get_owned_games(steam_id)
        games = [
            Game(
                appid=g.appid,
                name=g.name,
                playtime_minutes=g.playtime_forever,
                playtime_2weeks=g.playtime_2weeks,
                source="library",
                store_url=_store_url(g.appid),
            )
            for g in raw_games
        ]
        return {"library_games": games}

    return fetch_library


def make_fetch_wishlist(steam: SteamClient, steam_id: str) -> Callable:
    async def fetch_wishlist(state: PipelineState) -> dict:
        raw_entries = await steam.get_wishlist(steam_id)
        games = [
            Game(
                appid=e.appid,
                name="",  # appdetails fills the canonical name
                playtime_minutes=0,
                source="wishlist",
                store_url=_store_url(e.appid),
            )
            for e in raw_entries
        ]
        return {"wishlist_games": games}

    return fetch_wishlist


def make_enrich_games(steam: SteamClient, cache: GameCache) -> Callable:
    async def enrich_games(state: PipelineState) -> dict:
        library = state.get("library_games", [])
        wishlist = state.get("wishlist_games", [])

        # Merge unique appids; library takes precedence (has playtime).
        appid_to_game: dict[int, Game] = {}
        for game in wishlist:
            appid_to_game[game.appid] = game
        for game in library:
            appid_to_game[game.appid] = game

        appids = list(appid_to_game.keys())

        cached = await cache.get_many(appids)
        miss_appids = [a for a in appids if a not in cached]

        newly_fetched: dict[int, dict] = {}
        if miss_appids:
            raw_details = await steam.enrich_many(miss_appids)
            for appid, raw in raw_details.items():
                enrichment = raw.to_enrichment_dict()
                await cache.set(appid, enrichment)
                # Re-read to include the cached_at stamp cache.set wrote.
                stored = await cache.get(appid)
                newly_fetched[appid] = stored or enrichment

        enriched_games: list[Game] = []
        for appid, game in appid_to_game.items():
            enrich_data = cached.get(appid) or newly_fetched.get(appid, {})
            base = game.model_dump(mode="json")
            # Only overwrite non-None enrichment values; keep source/playtime from base.
            updates = {k: v for k, v in enrich_data.items() if v is not None and k not in ("source", "playtime_minutes", "playtime_2weeks")}
            enriched_games.append(Game.model_validate({**base, **updates}))

        return {
            "enriched_games": enriched_games,
            "cache_hits": len(cached),
            "cache_misses": len(miss_appids),
        }

    return enrich_games


_TASTE_SYSTEM = (
    "You are building a comprehensive taste profile for a Steam user. "
    "You will receive five labelled data sources with different signal strengths:\n"
    "  1. RECENTLY PLAYED (last 2 weeks) — strongest current signal; what they are actively into RIGHT NOW\n"
    "  2. TOP PLAYED — strong all-time signal; what they have historically invested time in\n"
    "  3. OWNED UNPLAYED — purchase intent; they bought it so they wanted it\n"
    "  4. WISHLISTED — aspiration; games they are drawn to but haven't bought yet\n"
    "  5. TRENDING / NEW (not owned) — gap context; what popular games are they missing\n\n"
    "Use all five sources. Weigh them in that order but do not ignore any. "
    "If RECENTLY PLAYED contains a game, treat it as the most important current preference signal "
    "even if its all-time hours are low — recent play reveals current mood and direction. "
    "Be specific — 'tactical FPS' beats 'shooter', 'precision platformer' beats 'platformer'. "
    "For wildcard_picks: suggest 2-3 genres or mechanics OUTSIDE their usual pattern that could "
    "genuinely click based on hidden overlaps in their underlying motivations, not just absent "
    "genres. A mastery-curve player might love a precision puzzle game; a co-op action player "
    "might click with a roguelike that rewards practice. "
    "For the summary: synthesise all five signals including any notable gaps vs trending games. "
    "Return JSON matching the TasteProfile schema exactly."
)


def make_fetch_trending(steam: SteamClient, cache: GameCache) -> Callable:
    async def fetch_trending(state: PipelineState) -> dict:
        enriched = state.get("enriched_games", [])
        owned = {g.appid for g in enriched}

        all_trending = await steam.get_trending_appids()
        new_appids = [a for a in all_trending if a not in owned][:40]

        if not new_appids:
            return {"trending_games": []}

        cached = await cache.get_many(new_appids)
        miss_appids = [a for a in new_appids if a not in cached][:15]

        newly_fetched: dict[int, dict] = {}
        if miss_appids:
            raw_details = await steam.enrich_many(miss_appids)
            for appid, raw in raw_details.items():
                enrichment = raw.to_enrichment_dict()
                await cache.set(appid, enrichment)
                stored = await cache.get(appid)
                newly_fetched[appid] = stored or enrichment

        trending_games = []
        for appid in new_appids:
            data = cached.get(appid) or newly_fetched.get(appid)
            if data and data.get("name"):
                trending_games.append({"appid": appid, **data})

        return {"trending_games": trending_games}

    return fetch_trending


_RANK_SYSTEM = (
    "You are a personalised game recommendation engine with deep knowledge of Steam's catalogue. "
    "You receive a player's taste profile and two sets of candidate games: "
    "games they already OWN but have never played (zero cost — prioritise these), "
    "and games on their WISHLIST (require purchase). "
    "\n\nRules:\n"
    "  1. rationale: 1-2 sentences citing SPECIFIC signals from their profile — "
    "     hours played, named genres, mechanics, or vibes. Generic praise is not acceptable.\n"
    "  2. score: 0-100 TASTE FIT score — how well this game matches their preferences. "
    "     Do NOT factor Metacritic into this number; score purely on taste alignment. "
    "     (90+ near-perfect, 75-89 strong, 60-74 solid)\n"
    "  3. Metacritic context: use MC scores to inform the quality of a pick, but keep it "
    "     separate from taste fit. A high-MC game that's a weak taste fit should score low; "
    "     a zero-MC indie that's a perfect fit should score high.\n"
    "  4. Favour owned-unplayed for immediate value; a great wishlist pick beats a weak owned one.\n"
    "  5. Include at least one pick reflecting their wildcard / open-to-trying signals.\n"
    "  6. Skip appids with insufficient metadata (no name, genres, or tags).\n"
    "Order picks by score descending. Return JSON matching the schema exactly."
)


class _Pick(BaseModel):
    appid: int
    rationale: str
    score: float


class _PickList(BaseModel):
    picks: list[_Pick]


def make_rank_and_explain(llm: LLMProvider) -> Callable:
    async def rank_and_explain(state: PipelineState) -> dict:
        profile = state.get("taste_profile")
        enriched = state.get("enriched_games", [])
        n = settings.top_n_recommendations

        if not profile:
            return {"recommendations": []}

        by_appid = {g.appid: g for g in enriched}
        unplayed = sorted(
            [g for g in enriched if g.source == "library" and g.playtime_minutes == 0],
            key=lambda g: g.metacritic_score or 0,
            reverse=True,
        )
        wishlist = sorted(
            [g for g in enriched if g.source == "wishlist"],
            key=lambda g: g.metacritic_score or 0,
            reverse=True,
        )

        def fmt(g: Game) -> str:
            mc = f" MC:{g.metacritic_score}" if g.metacritic_score else ""
            price = f" ${g.price_cents / 100:.0f}" if g.price_cents is not None else ""
            genres = ", ".join(g.genres) or "—"
            tags = ", ".join(g.tags[:5]) or "—"
            return f"• [{g.appid}] {g.name}{mc}{price} — genres: {genres} — tags: {tags}"

        recently = sorted(
            [g for g in enriched if g.source == "library" and g.playtime_2weeks > 0],
            key=lambda g: g.playtime_2weeks, reverse=True,
        )

        lines: list[str] = [
            "TASTE PROFILE:",
            f"Summary: {profile.summary}",
            f"Preferred genres: {', '.join(profile.preferred_genres)}",
            f"Preferred mechanics: {', '.join(profile.preferred_mechanics)}",
            f"Vibes: {', '.join(profile.vibes)}",
            f"Open to trying: {', '.join(profile.wildcard_picks)}",
        ]

        if recently:
            lines.append("")
            lines.append(f"CURRENTLY ACTIVE (last 2 weeks — strongest momentum signal):")
            for g in recently:
                lines.append(f"  • {g.name} [{g.playtime_2weeks // 60}h this fortnight]")

        lines += [
            "",
            f"OWNED — NOT YET PLAYED ({len(unplayed)} total, top {min(len(unplayed), 60)} shown):",
        ]
        for g in unplayed[:60]:
            lines.append(fmt(g))

        lines.append(f"\nWISHLISTED ({len(wishlist)} total, top {min(len(wishlist), 60)} shown):")
        for g in wishlist[:60]:
            lines.append(fmt(g))

        lines.append(f"\nSelect the top {n} recommendations from the candidates above.")

        result = await llm.complete_json(_RANK_SYSTEM, "\n".join(lines), _PickList)

        # Blend LLM taste-fit score with Metacritic quality signal.
        # 70% taste fit (LLM) + 30% MC (65 = neutral when score is unavailable).
        raw: list[tuple[float, _Pick]] = []
        for pick in result.picks[:n * 2]:  # over-fetch so we still get n after filtering
            game = by_appid.get(pick.appid)
            if game is None:
                continue
            mc = game.metacritic_score if game.metacritic_score is not None else 65
            blended = round(0.70 * pick.score + 0.30 * mc, 1)
            raw.append((blended, pick))

        raw.sort(key=lambda t: t[0], reverse=True)

        recommendations: list[Recommendation] = []
        for rank, (blended_score, pick) in enumerate(raw[:n], start=1):
            game = by_appid.get(pick.appid)  # already verified above, always present
            recommendations.append(Recommendation(
                game=game,
                score=blended_score,
                rationale=pick.rationale,
                rank=rank,
            ))

        return {"recommendations": recommendations}

    return rank_and_explain


def make_build_taste_profile(llm: LLMProvider) -> Callable:
    async def build_taste_profile(state: PipelineState) -> dict:
        enriched = state.get("enriched_games", [])
        trending = state.get("trending_games", [])

        library  = [g for g in enriched if g.source == "library"]
        wishlist = [g for g in enriched if g.source == "wishlist"]
        played   = sorted([g for g in library if g.playtime_minutes > 0],
                          key=lambda g: g.playtime_minutes, reverse=True
                          )[:settings.library_sample_for_rediscovery]
        unplayed = [g for g in library if g.playtime_minutes == 0][:20]

        if not played and not wishlist:
            return {
                "taste_profile": TasteProfile(
                    preferred_genres=[],
                    preferred_mechanics=[],
                    vibes=[],
                    wildcard_picks=[],
                    summary="No library or wishlist data available.",
                )
            }

        def fmt(g) -> str:
            mc    = f" MC:{g.metacritic_score}" if g.metacritic_score else ""
            tags  = ", ".join(g.tags[:5]) if g.tags else "—"
            genres = ", ".join(g.genres) if g.genres else "—"
            return f"• {g.name}{mc} — genres: {genres} — tags: {tags}"

        recently = sorted(
            [g for g in library if g.playtime_2weeks > 0],
            key=lambda g: g.playtime_2weeks, reverse=True,
        )

        sections: list[str] = []

        if recently:
            sections.append(f"RECENTLY PLAYED — LAST 2 WEEKS ({len(recently)}, strongest current signal):")
            for g in recently:
                genres = ", ".join(g.genres) or "—"
                tags = ", ".join(g.tags[:5]) or "—"
                sections.append(
                    f"• {g.name} [{g.playtime_2weeks // 60}h this fortnight"
                    f"{', ' + str(g.playtime_minutes // 60) + 'h all-time' if g.playtime_minutes > 0 else ''}]"
                    f" — genres: {genres} — tags: {tags}"
                )

        if played:
            sections.append(f"\nTOP PLAYED LIBRARY GAMES ({len(played)}, strong all-time signal):")
            for g in played:
                sections.append(f"• {g.name} [{g.playtime_minutes // 60}h] — genres: {', '.join(g.genres) or '—'} — tags: {', '.join(g.tags[:5]) or '—'}")

        if unplayed:
            sections.append(f"\nOWNED BUT NOT YET PLAYED ({len(unplayed)}, purchase-intent signal):")
            for g in unplayed:
                sections.append(fmt(g))

        if wishlist:
            sections.append(f"\nWISHLISTED ({len(wishlist)}, aspiration signal):")
            for g in wishlist[:30]:
                sections.append(fmt(g))

        if trending:
            sections.append(f"\nTRENDING / NEW ON STEAM — NOT IN THEIR LIBRARY ({len(trending)}):")
            for t in trending[:20]:
                mc     = f" MC:{t['metacritic_score']}" if t.get("metacritic_score") else ""
                genres = ", ".join(t.get("genres", [])) or "—"
                sections.append(f"• {t['name']}{mc} — genres: {genres}")

        profile = await llm.complete_json(_TASTE_SYSTEM, "\n".join(sections), TasteProfile)
        return {"taste_profile": profile}

    return build_taste_profile
