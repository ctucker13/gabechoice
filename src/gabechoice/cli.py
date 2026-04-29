"""GabeChoice CLI — run the fetch+enrich+taste pipeline from the terminal."""

import asyncio
import time

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from gabechoice.config import settings
from gabechoice.cache import GameCache
from gabechoice.clients.steam import SteamClient
from gabechoice.graph.builder import build_graph
from gabechoice.llm import get_llm

console = Console()

_LOGO = """\
[cyan]  ██████╗  ██████╗[/]
[cyan]  ██╔════╝ ██╔════╝[/]
[cyan]  ██║  ███╗██║[/]
[cyan]  ██║   ██║██║[/]
[cyan]  ╚██████╔╝╚██████╗[/]
[cyan]   ╚═════╝  ╚═════╝[/]

[bold]        GabeChoice[/]
[dim]  Gaben picks your next game.[/]
"""
cli = typer.Typer(add_completion=False, help="GabeChoice — Steam recommendations powered by LLMs.")


@cli.command()
def main(
    limit: int = typer.Option(15, "--limit", "-n", help="Rows to show in results table."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip LLM nodes (fetch+enrich only)."),
) -> None:
    asyncio.run(_run(limit=limit, use_llm=not no_llm))


async def _run(limit: int, use_llm: bool) -> None:
    if not settings.steam_id_64:
        console.print("[red]Error:[/] STEAM_ID_64 is not set. Add it to your .env file.")
        raise SystemExit(1)

    console.print(_LOGO)
    console.print(Panel(
        f"[bold cyan]◈  GabeChoice[/]  [dim]·  Steam recommendations[/]\n"
        f"[dim]Steam ID :[/]  {settings.steam_id_64}\n"
        f"[dim]Provider :[/]  {settings.llm_provider}  [dim]·[/]  "
        f"[dim]Rate :[/]  {settings.steam_appdetails_rps} req/s  [dim]·[/]  "
        f"[dim]Cache :[/]  {settings.cache_db_path}",
        border_style="dim blue",
        padding=(0, 1),
    ))

    cache = GameCache(settings.cache_db_path)
    await cache.init_db()

    llm = get_llm() if use_llm else None

    console.print("[dim]▸ Fetching library and wishlist…[/]")

    async with SteamClient(settings.steam_api_key) as steam:
        graph = build_graph(steam, cache, llm=llm)
        start = time.monotonic()
        result = await graph.ainvoke({"run_start": start})

    elapsed = time.monotonic() - start
    games  = result.get("enriched_games", [])
    hits   = result.get("cache_hits", 0)
    misses = result.get("cache_misses", 0)

    console.print(Panel(
        f"[bold]{len(games)}[/] games  [dim]·[/]  "
        f"[green]{hits} cache hits[/]  [dim]·[/]  "
        f"[yellow]{misses} misses[/]  [dim]·[/]  "
        f"[dim]{elapsed:.1f}s[/]",
        title="[bold]Pipeline complete[/]",
        border_style="green" if misses == 0 else "yellow",
        padding=(0, 1),
    ))

    profile = result.get("taste_profile")
    if profile:
        _render_taste_profile(profile)

    recs = result.get("recommendations", [])
    if recs:
        _render_recommendations(recs)

    _render_games_table(games, limit)


def _render_taste_profile(profile) -> None:
    def chips(items: list[str], style: str) -> str:
        return "  ".join(f"[{style}]{i}[/]" for i in items) if items else "[dim]—[/]"

    console.print(Panel(
        f"{profile.summary}\n\n"
        f"[dim]Genres      [/]  {chips(profile.preferred_genres, 'cyan')}\n"
        f"[dim]Mechanics   [/]  {chips(profile.preferred_mechanics, 'cyan')}\n"
        f"[dim]Vibes       [/]  {chips(profile.vibes, 'green')}\n"
        f"[dim]Try different[/]  {chips(profile.wildcard_picks, 'yellow')}",
        title="[bold cyan]◈  Your Taste Fingerprint[/]",
        border_style="cyan",
        padding=(0, 1),
    ))


def _render_recommendations(recs: list) -> None:
    table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold dim", padding=(0, 1),
    )
    table.add_column("#",    width=3,  justify="right", style="bold cyan")
    table.add_column("Game", min_width=28)
    table.add_column("MC",   width=4,  justify="right")
    table.add_column("Src",  width=7)
    table.add_column("Fit",  width=4,  justify="right")
    table.add_column("Why",  min_width=50)

    for rec in recs:
        g = rec.game
        src = Text("library", style="green") if g.source == "library" else Text("wishlist", style="cyan")
        fit = rec.score
        fit_style = "bold green" if fit >= 85 else ("bold yellow" if fit >= 70 else "bold red")
        table.add_row(
            str(rec.rank),
            g.name,
            _mc(g.metacritic_score),
            src,
            Text(f"{fit:.0f}", style=fit_style),
            Text(rec.rationale, style="dim"),
        )

    console.print(Panel(
        table,
        title="[bold cyan]◈  GabeChoice Recommends[/]",
        border_style="cyan",
        padding=(0, 1),
    ))


def _render_games_table(games: list, limit: int) -> None:
    table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold dim", padding=(0, 1),
    )
    table.add_column("Source",  style="dim", width=9)
    table.add_column("Name",    min_width=36)
    table.add_column("Hours",   justify="right", width=7)
    table.add_column("MC",      justify="right", width=4)

    for g in sorted(games, key=lambda x: x.playtime_minutes, reverse=True)[:limit]:
        source = Text("library", style="green") if g.source == "library" else Text("wishlist", style="cyan")
        table.add_row(source, g.name, str(g.playtime_minutes // 60), _mc(g.metacritic_score))

    console.print(table)


def _mc(score: int | None) -> Text:
    if score is None:
        return Text("—", style="dim")
    if score >= 85:
        return Text(str(score), style="bold green")
    if score >= 70:
        return Text(str(score), style="yellow")
    return Text(str(score), style="red")


if __name__ == "__main__":
    cli()
