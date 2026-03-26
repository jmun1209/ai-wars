"""AI Wars — Episode Generator entry point."""

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
from rich.prompt import IntPrompt, Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn

load_dotenv()

console = Console()

_CHARACTERS_PATH = Path(__file__).parent / "config" / "characters.json"


def _load_characters() -> dict[str, Any]:
    """Load character definitions from config/characters.json."""
    return json.loads(_CHARACTERS_PATH.read_text())["characters"]


def _intake_form() -> dict[str, Any]:
    """Display the terminal intake form and collect episode configuration.

    Returns:
        episode_config dict with all user-provided values.
    """
    console.print(
        Panel.fit(
            "[bold purple]AI Wars — Episode Generator[/bold purple]\n"
            "[dim]Answer the prompts below to generate your episode.[/dim]",
            border_style="purple",
        )
    )

    characters = _load_characters()
    char_keys = list(characters.keys())

    # Show character list
    console.print("\n[bold]Available characters:[/bold]")
    for i, key in enumerate(char_keys, start=1):
        char = characters[key]
        console.print(f"  [cyan]{i}[/cyan]. {char['full_name']} ({char['model_inspiration']})")

    episode_number = IntPrompt.ask("\n[bold]Episode number[/bold]")
    title = Prompt.ask("[bold]Episode title[/bold]")
    concept = Prompt.ask("[bold]AI concept being taught[/bold] (e.g. RAG, fine-tuning, attention)")

    console.print("\n[bold]Which characters appear?[/bold] Enter numbers separated by commas (e.g. 1,3)")
    char_input = Prompt.ask("Characters")
    selected_chars: list[str] = []
    for part in char_input.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(char_keys):
                selected_chars.append(char_keys[idx])
        elif part.lower() in char_keys:
            selected_chars.append(part.lower())
    if not selected_chars:
        selected_chars = char_keys[:2]  # default to first two
        console.print(f"[dim]Defaulting to: {selected_chars}[/dim]")

    winner = Prompt.ask("[bold]Who wins and why?[/bold] (name — one-sentence reason)")
    drama = Prompt.ask("[bold]Drama/conflict[/bold] (1-2 sentences)")
    cliffhanger = Prompt.ask("[bold]Cliffhanger ending[/bold] (1 sentence)")
    length_seconds = IntPrompt.ask("[bold]Desired video length in seconds[/bold]", default=75)

    return {
        "episode_number": episode_number,
        "title": title,
        "concept": concept,
        "characters": selected_chars,
        "winner": winner,
        "drama": drama,
        "cliffhanger": cliffhanger,
        "length_seconds": length_seconds,
    }


async def run_pipeline(episode_config: dict[str, Any], dry_run: bool = False, skip_upload: bool = False) -> None:
    """Execute the full AI Wars generation pipeline.

    Args:
        episode_config: Dict from the intake form.
        dry_run: If True, skip real API calls and use placeholder assets.
        skip_upload: If True, stop before the TikTok upload step.
    """
    from agents import script_agent, caption_agent
    from generators import voice_gen, video_gen, visual_gen
    from editor import assemble
    from publisher import tiktok_upload
    from utils.logger import print_summary

    ep_num = episode_config["episode_number"]

    def _stage(label: str):
        return Progress(SpinnerColumn(), TextColumn(f"[bold cyan]{label}…"), transient=True)

    # ------------------------------------------------------------------
    # Stage 1 — Script
    # ------------------------------------------------------------------
    try:
        with _stage("Generating script"):
            if dry_run:
                script = _dry_run_script(episode_config)
            else:
                script = await script_agent.generate(episode_config)
        console.print("[green]✓[/green] Script generated")
    except Exception as exc:
        console.print(f"[red]✗ Script generation failed:[/red] {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Stage 2 — Voiceovers
    # ------------------------------------------------------------------
    try:
        with _stage("Generating voiceovers"):
            if dry_run:
                audio_files: dict = {}
            else:
                audio_files = await voice_gen.generate_all(script)
        console.print("[green]✓[/green] Voiceovers generated")
    except Exception as exc:
        console.print(f"[red]✗ Voice generation failed:[/red] {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Stage 3 — Video clips
    # ------------------------------------------------------------------
    try:
        with _stage("Generating video clips"):
            if dry_run:
                video_clips = _dry_run_clips(script, ep_num)
            else:
                video_clips = await video_gen.generate_all(script)
        console.print("[green]✓[/green] Video clips generated")
    except Exception as exc:
        console.print(f"[red]✗ Video generation failed:[/red] {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Stage 4 — Educational visuals
    # ------------------------------------------------------------------
    try:
        with _stage("Generating visuals"):
            if dry_run:
                visual_files = _dry_run_visuals(script, ep_num)
            else:
                visual_files = await visual_gen.generate_all(script)
        console.print("[green]✓[/green] Visuals generated")
    except Exception as exc:
        console.print(f"[red]✗ Visual generation failed:[/red] {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Stage 5 — Assembly
    # ------------------------------------------------------------------
    try:
        with _stage("Assembling final video"):
            if dry_run:
                final_video_path = _dry_run_final(ep_num)
            else:
                final_video_path = await assemble.build(script, audio_files, video_clips, visual_files)
        console.print(f"[green]✓[/green] Video assembled: [bold]{final_video_path}[/bold]")
    except Exception as exc:
        console.print(f"[red]✗ Assembly failed:[/red] {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Stage 6 — TikTok caption copy
    # ------------------------------------------------------------------
    try:
        with _stage("Generating TikTok caption"):
            if dry_run:
                tiktok_copy = {"full_caption": "[dry-run caption]"}
            else:
                tiktok_copy = await caption_agent.generate(script, episode_config)
        console.print("[green]✓[/green] Caption generated")
    except Exception as exc:
        console.print(f"[red]✗ Caption generation failed:[/red] {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Stage 7 — TikTok upload
    # ------------------------------------------------------------------
    if not skip_upload and not dry_run:
        try:
            with _stage("Uploading to TikTok"):
                upload_result = await tiktok_upload.upload_draft(final_video_path, tiktok_copy)
            if upload_result["success"]:
                console.print("[green]✓[/green] Uploaded to TikTok drafts")
            else:
                console.print(f"[yellow]⚠ Upload result:[/yellow] {upload_result['message']}")
        except Exception as exc:
            console.print(f"[red]✗ TikTok upload failed:[/red] {exc}")
    else:
        console.print("[dim]Skipping TikTok upload[/dim]")
        upload_result = {"caption_to_paste": tiktok_copy.get("full_caption", "")}

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    console.print("\n[bold green]Pipeline complete![/bold green]")
    console.print(f"Final video: [bold]{final_video_path}[/bold]")
    console.print("\n[bold]TikTok caption:[/bold]")
    console.print(Panel(upload_result.get("caption_to_paste", tiktok_copy.get("full_caption", "")), border_style="cyan"))

    print_summary()


# ---------------------------------------------------------------------------
# Dry-run helpers — return placeholder paths without hitting any API
# ---------------------------------------------------------------------------

def _dry_run_script(episode_config: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal placeholder script for dry-run mode."""
    return {
        "episode_number": episode_config["episode_number"],
        "title": episode_config["title"],
        "concept": episode_config["concept"],
        "educational_takeaway": "Dry-run educational takeaway",
        "hook_text": "Dry-run hook",
        "scenes": [
            {
                "scene_number": 1,
                "duration_seconds": 5,
                "shot_description": "Dry-run scene",
                "dialogue": [
                    {"character": "claudia", "line": "Hello world.", "emotion": "calm"}
                ],
                "educational_note": "test",
                "visual_overlay": None,
            }
        ],
        "outro": {
            "winner": "claudia",
            "takeaway_line": "Dry-run takeaway.",
            "cliffhanger_text": "To be continued…",
        },
    }


def _dry_run_clips(script: dict[str, Any], ep_num: int) -> list[Path]:
    """Return placeholder clip paths (files do not need to exist for dry-run)."""
    clips_dir = Path(__file__).parent / "output" / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for scene in script.get("scenes", []):
        p = clips_dir / f"ep{ep_num}_scene{scene['scene_number']}_dryrun.mp4"
        p.touch()
        paths.append(p)
    return paths


def _dry_run_visuals(script: dict[str, Any], ep_num: int) -> dict[str, Path]:
    """Return placeholder visual paths."""
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
    """Return a placeholder final video path."""
    final_dir = Path(__file__).parent / "output" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    p = final_dir / f"ep{ep_num}_final_dryrun.mp4"
    p.touch()
    return p


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Wars Episode Generator")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip all API calls; use placeholder files to test assembly logic",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Stop before the TikTok upload step",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = _parse_args()

    if args.dry_run:
        console.print("[yellow]DRY-RUN mode — no real API calls will be made.[/yellow]")

    try:
        episode_config = _intake_form()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled.[/dim]")
        sys.exit(0)

    asyncio.run(run_pipeline(episode_config, dry_run=args.dry_run, skip_upload=args.skip_upload))


if __name__ == "__main__":
    main()
