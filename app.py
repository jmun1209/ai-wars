"""AI Wars — Season-driven episode generator entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import IntPrompt
from rich.table import Table

load_dotenv()

console = Console()

_SEASONS_DIR = Path(__file__).parent / "output" / "seasons"
_SEASONS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Season UI helpers
# ---------------------------------------------------------------------------

def _show_banner() -> None:
    console.print(
        Panel.fit(
            "[bold purple]AI Wars — Season Generator[/bold purple]\n"
            "[dim]Automated TikTok series about AI models competing to teach you ML.[/dim]",
            border_style="purple",
        )
    )


def _show_episode_table(plan: dict[str, Any]) -> None:
    """Print a rich table of all episodes and their status."""
    table = Table(title=f"Season {plan['season_number']} — Episode Plan", show_header=True, header_style="bold magenta")
    table.add_column("#", justify="right", width=3)
    table.add_column("Title", width=32)
    table.add_column("Concept", width=28)
    table.add_column("Characters", width=20)
    table.add_column("Status", justify="center", width=10)

    for ep in plan["episodes"]:
        status = ep.get("status", "planned")
        status_style = "green" if status == "generated" else "yellow" if status == "in_progress" else "dim"
        table.add_row(
            str(ep["episode_number"]),
            ep["title"],
            ep["concept"],
            ", ".join(ep.get("characters", [])),
            f"[{status_style}]{status}[/{status_style}]",
        )

    console.print(table)


def _spinner(label: str) -> Progress:
    return Progress(SpinnerColumn(), TextColumn(f"[bold cyan]{label}…"), transient=True)


# ---------------------------------------------------------------------------
# Season planning
# ---------------------------------------------------------------------------

async def _get_or_create_season(season_number: int) -> dict[str, Any]:
    """Load existing season plan or generate a new one via the agent pipeline."""
    from agents import showrunner_agent, season_agent

    plan_path = _SEASONS_DIR / f"season_{season_number}_plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        console.print(f"[green]✓[/green] Loaded existing Season {season_number} plan ({len(plan['episodes'])} episodes)")
        return plan

    console.print(f"\n[bold]No season plan found for Season {season_number}. Generating now…[/bold]")
    console.print("[dim]This uses two AI agents and takes about 30-60 seconds.[/dim]\n")

    with _spinner("Showrunner crafting season vision"):
        vision = await showrunner_agent.generate(season_number)

    console.print(f"[green]✓[/green] Season vision: [italic]{vision.get('tagline', '')}[/italic]")
    console.print(f"[dim]{vision.get('premise', '')}[/dim]\n")

    with _spinner("Writers room planning 20 episodes"):
        plan = await season_agent.generate(vision)

    console.print(f"[green]✓[/green] {len(plan['episodes'])} episodes planned\n")
    return plan


# ---------------------------------------------------------------------------
# Episode pipeline
# ---------------------------------------------------------------------------

async def run_episode(
    episode_config: dict[str, Any],
    plan: dict[str, Any],
    dry_run: bool = False,
    skip_upload: bool = False,
) -> None:
    """Run the full production pipeline for a single episode.

    Args:
        episode_config: Episode dict from season_agent.get_episode_config().
        plan: Full season plan (used to mark episode complete after).
        dry_run: Skip real API calls; use placeholder files.
        skip_upload: Stop before TikTok upload.
    """
    from agents import script_agent, caption_agent
    from agents.season_agent import mark_episode_complete
    from generators import voice_gen, video_gen, visual_gen
    from editor import assemble
    from publisher import tiktok_upload
    from utils.logger import print_summary

    ep_num = episode_config["episode_number"]

    console.print(f"\n[bold purple]Producing Episode {ep_num}: {episode_config['title']}[/bold purple]")
    console.print(f"[dim]Concept: {episode_config['concept']}[/dim]\n")

    # --- Script ---
    try:
        with _spinner(f"Ep {ep_num} — Writing script"):
            script = _dry_run_script(episode_config) if dry_run else await script_agent.generate(episode_config)
        console.print("[green]✓[/green] Script written")
    except Exception as exc:
        console.print(f"[red]✗ Script failed:[/red] {exc}")
        sys.exit(1)

    # --- Voiceovers ---
    try:
        with _spinner(f"Ep {ep_num} — Generating voiceovers"):
            audio_files = {} if dry_run else await voice_gen.generate_all(script)
        console.print("[green]✓[/green] Voiceovers generated")
    except Exception as exc:
        console.print(f"[red]✗ Voice gen failed:[/red] {exc}")
        sys.exit(1)

    # --- Video clips ---
    try:
        with _spinner(f"Ep {ep_num} — Generating video clips"):
            video_clips = _dry_run_clips(script, ep_num) if dry_run else await video_gen.generate_all(script)
        console.print("[green]✓[/green] Video clips generated")
    except Exception as exc:
        console.print(f"[red]✗ Video gen failed:[/red] {exc}")
        sys.exit(1)

    # --- Visuals ---
    try:
        with _spinner(f"Ep {ep_num} — Generating visuals"):
            visual_files = _dry_run_visuals(script, ep_num) if dry_run else await visual_gen.generate_all(script)
        console.print("[green]✓[/green] Visuals generated")
    except Exception as exc:
        console.print(f"[red]✗ Visual gen failed:[/red] {exc}")
        sys.exit(1)

    # --- Assembly ---
    try:
        with _spinner(f"Ep {ep_num} — Assembling video"):
            final_video_path = _dry_run_final(ep_num) if dry_run else await assemble.build(
                script, audio_files, video_clips, visual_files
            )
        console.print(f"[green]✓[/green] Video: [bold]{final_video_path}[/bold]")
    except Exception as exc:
        console.print(f"[red]✗ Assembly failed:[/red] {exc}")
        sys.exit(1)

    # --- Caption ---
    try:
        with _spinner(f"Ep {ep_num} — Writing TikTok caption"):
            tiktok_copy = {"full_caption": "[dry-run]"} if dry_run else await caption_agent.generate(
                script, episode_config
            )
        console.print("[green]✓[/green] Caption written")
    except Exception as exc:
        console.print(f"[red]✗ Caption failed:[/red] {exc}")
        sys.exit(1)

    # --- Upload ---
    upload_result: dict = {"caption_to_paste": tiktok_copy.get("full_caption", "")}
    if not skip_upload and not dry_run:
        try:
            with _spinner(f"Ep {ep_num} — Uploading to TikTok"):
                upload_result = await tiktok_upload.upload_draft(final_video_path, tiktok_copy)
            if upload_result["success"]:
                console.print("[green]✓[/green] Uploaded to TikTok drafts")
            else:
                console.print(f"[yellow]⚠[/yellow] {upload_result['message']}")
        except Exception as exc:
            console.print(f"[red]✗ Upload failed:[/red] {exc}")
    else:
        console.print("[dim]Skipping TikTok upload[/dim]")

    # Mark complete in season plan
    mark_episode_complete(plan, ep_num)

    # --- Summary ---
    console.print(f"\n[bold green]Episode {ep_num} complete![/bold green]")
    console.print(Panel(
        upload_result.get("caption_to_paste", tiktok_copy.get("full_caption", "")),
        title="TikTok Caption",
        border_style="cyan",
    ))
    print_summary()


# ---------------------------------------------------------------------------
# Dry-run placeholders
# ---------------------------------------------------------------------------

def _dry_run_script(episode_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode_number": episode_config["episode_number"],
        "title": episode_config["title"],
        "concept": episode_config["concept"],
        "educational_takeaway": "Dry-run takeaway",
        "hook_text": "Dry-run hook",
        "scenes": [{
            "scene_number": 1, "duration_seconds": 5,
            "shot_description": "Dry-run scene",
            "dialogue": [{"character": "claudia", "line": "Hello.", "emotion": "calm"}],
            "educational_note": "test", "visual_overlay": None,
        }],
        "outro": {"winner": "claudia", "takeaway_line": "Dry-run.", "cliffhanger_text": "…"},
    }


def _dry_run_clips(script: dict[str, Any], ep_num: int) -> list[Path]:
    clips_dir = Path(__file__).parent / "output" / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for scene in script.get("scenes", []):
        p = clips_dir / f"ep{ep_num}_scene{scene['scene_number']}_dryrun.mp4"
        p.touch()
        paths.append(p)
    return paths


def _dry_run_visuals(script: dict[str, Any], ep_num: int) -> dict[str, Path]:
    images_dir = Path(__file__).parent / "output" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for name in ("title", "concept", "takeaway"):
        (images_dir / f"ep{ep_num}_{name}_dryrun.png").touch()
    return {
        "title_card": images_dir / f"ep{ep_num}_title_dryrun.png",
        "concept": images_dir / f"ep{ep_num}_concept_dryrun.png",
        "takeaway_card": images_dir / f"ep{ep_num}_takeaway_dryrun.png",
    }


def _dry_run_final(ep_num: int) -> Path:
    final_dir = Path(__file__).parent / "output" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    p = final_dir / f"ep{ep_num}_final_dryrun.mp4"
    p.touch()
    return p


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Wars Season Generator")
    parser.add_argument("--season", type=int, default=1, help="Season number (default: 1)")
    parser.add_argument("--episode", type=int, default=None, help="Skip menu, produce this episode directly")
    parser.add_argument("--dry-run", action="store_true", help="No real API calls; test pipeline with placeholders")
    parser.add_argument("--skip-upload", action="store_true", help="Stop before TikTok upload")
    parser.add_argument("--plan-only", action="store_true", help="Generate season plan and exit (no video production)")
    return parser.parse_args()


async def main_async() -> None:
    args = _parse_args()
    _show_banner()

    if args.dry_run:
        console.print("[yellow]DRY-RUN mode — no real API calls.[/yellow]\n")

    # Load or generate the season plan
    try:
        plan = await _get_or_create_season(args.season)
    except Exception as exc:
        console.print(f"[red]✗ Season planning failed:[/red] {exc}")
        sys.exit(1)

    if args.plan_only:
        _show_episode_table(plan)
        console.print("\n[dim]--plan-only: exiting without producing any video.[/dim]")
        return

    # Show the episode table
    _show_episode_table(plan)

    # Pick an episode
    if args.episode:
        episode_number = args.episode
    else:
        console.print()
        episode_number = IntPrompt.ask(
            "[bold]Which episode do you want to produce?[/bold] (enter number)",
            default=1,
        )

    # Get episode config from plan
    from agents.season_agent import get_episode_config
    try:
        episode_config = get_episode_config(plan, episode_number)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    await run_episode(episode_config, plan, dry_run=args.dry_run, skip_upload=args.skip_upload)


def main() -> None:
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled.[/dim]")
        sys.exit(0)


if __name__ == "__main__":
    main()
