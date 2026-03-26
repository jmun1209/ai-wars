"""Visual generator — creates educational images via Replicate / Flux Schnell."""

from __future__ import annotations

import asyncio
import io
import os
import time
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve

import replicate
import requests
from dotenv import load_dotenv

from utils.logger import StageTimer, log_api_call

load_dotenv()

_OUTPUT_DIR = Path(__file__).parent.parent / "output" / "images"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_MODEL = "black-forest-labs/flux-schnell"
_IMAGE_SIZE = {"width": 1080, "height": 1920}


def _title_card_prompt(script: dict[str, Any]) -> str:
    hook = script.get("hook_text", "")
    number = script.get("episode_number", 1)
    title = script.get("title", "")
    return (
        f"Minimalist tech design, bold typography, dark background, "
        f"text: '{hook}', episode {number}: {title}, "
        f"purple and white color scheme, TikTok vertical format"
    )


def _concept_prompt(script: dict[str, Any]) -> str:
    concept = script.get("concept", "AI")
    return (
        f"Clean educational diagram explaining {concept}, simple flat design, "
        f"dark background, purple blue color scheme, no text, iconic visual metaphor"
    )


def _takeaway_prompt(script: dict[str, Any]) -> str:
    takeaway = script.get("educational_takeaway", "")
    return (
        f"Bold motivational poster style, dark background, large clear text: "
        f"'{takeaway}', purple glow, TikTok vertical format"
    )


async def _generate_image(
    prompt: str,
    output_path: Path,
    max_retries: int = 3,
) -> Path:
    """Run a Replicate Flux Schnell inference and save the result as PNG.

    Args:
        prompt: Image generation prompt.
        output_path: Destination file path.
        max_retries: Retry attempts with exponential backoff.

    Returns:
        Path to the saved PNG file.
    """
    if output_path.exists():
        return output_path

    for attempt in range(max_retries):
        try:
            start = time.time()
            output = await asyncio.to_thread(
                replicate.run,
                _MODEL,
                input={
                    "prompt": prompt,
                    **_IMAGE_SIZE,
                    "num_outputs": 1,
                    "output_format": "png",
                },
            )
            duration = time.time() - start
            log_api_call(f"replicate/{_MODEL}", 200, duration)

            # output is a list of URLs or file-like objects depending on SDK version
            result = output[0] if isinstance(output, list) else output
            if hasattr(result, "read"):
                output_path.write_bytes(result.read())
            else:
                # It's a URL string
                response = requests.get(str(result), timeout=60)
                response.raise_for_status()
                output_path.write_bytes(response.content)

            return output_path

        except Exception as exc:
            if attempt == max_retries - 1:
                raise RuntimeError(f"visual_gen: failed for {output_path.name}: {exc}") from exc
            wait = 2 ** attempt
            print(f"[visual_gen] Attempt {attempt + 1} failed: {exc}. Retrying in {wait}s…")
            await asyncio.sleep(wait)

    raise RuntimeError(f"visual_gen: max retries exceeded for {output_path.name}")


async def generate_all(script: dict[str, Any]) -> dict[str, Path]:
    """Generate title card, concept diagram, and takeaway card images.

    Args:
        script: Parsed script dict from script_agent.

    Returns:
        Dict with keys 'title_card', 'concept', 'takeaway_card' mapping to PNG paths.
    """
    ep_num = script["episode_number"]

    title_path = _OUTPUT_DIR / f"ep{ep_num}_title.png"
    concept_path = _OUTPUT_DIR / f"ep{ep_num}_concept.png"
    takeaway_path = _OUTPUT_DIR / f"ep{ep_num}_takeaway.png"

    with StageTimer("Visual gen"):
        title_path, concept_path, takeaway_path = await asyncio.gather(
            _generate_image(_title_card_prompt(script), title_path),
            _generate_image(_concept_prompt(script), concept_path),
            _generate_image(_takeaway_prompt(script), takeaway_path),
        )

    return {
        "title_card": title_path,
        "concept": concept_path,
        "takeaway_card": takeaway_path,
    }
