"""Video generator — creates MP4 clips per scene via Kling AI API."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from utils.logger import StageTimer, log_api_call

load_dotenv()

_OUTPUT_DIR = Path(__file__).parent.parent / "output" / "clips"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_CHARACTERS_PATH = Path(__file__).parent.parent / "config" / "characters.json"

_KLING_BASE_URL = "https://api.klingai.com/v1"
_POLL_INTERVAL = 5       # seconds between status checks
_TIMEOUT_SECONDS = 300   # 5-minute per-clip timeout


def _build_prompt(scene: dict[str, Any], characters: dict[str, Any]) -> str:
    """Combine shot description with character visual style details."""
    styles: list[str] = []
    for dialogue in scene.get("dialogue", []):
        char_key = dialogue["character"].lower()
        if char_key in characters:
            styles.append(characters[char_key]["visual_style"])
    style_str = ". ".join(styles)
    return f"{scene['shot_description']}. Characters: {style_str}. Vertical 9:16 TikTok format."


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['KLING_API_KEY']}",
        "Content-Type": "application/json",
    }


def _make_fallback_video(scene: dict[str, Any], output_path: Path, characters: dict[str, Any]) -> None:
    """Create a fallback 5-second static-image video using Pillow + FFmpeg."""
    import ffmpeg

    # Pick colour from first character in scene
    bg_color = "#1a1a2e"
    char_name = ""
    for dialogue in scene.get("dialogue", []):
        key = dialogue["character"].lower()
        if key in characters:
            bg_color = characters[key]["color"]
            char_name = characters[key]["full_name"]
            break

    img_path = output_path.with_suffix(".png")
    img = Image.new("RGB", (1080, 1920), bg_color)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size=80)
    except OSError:
        font = ImageFont.load_default()
    draw.text((540, 960), char_name or "AI Wars", fill="white", anchor="mm", font=font)
    img.save(img_path)

    (
        ffmpeg
        .input(str(img_path), loop=1, t=5, framerate=24)
        .output(str(output_path), vcodec="libx264", pix_fmt="yuv420p", shortest=None)
        .overwrite_output()
        .run(quiet=True)
    )
    img_path.unlink(missing_ok=True)


async def _generate_clip(
    scene: dict[str, Any],
    ep_num: int,
    characters: dict[str, Any],
    max_retries: int = 3,
) -> Path:
    """Submit a text-to-video job to Kling AI and poll until complete.

    Falls back to a static image video if generation fails or times out.

    Args:
        scene: Scene dict from the script.
        ep_num: Episode number used for file naming.
        characters: Characters config dict.
        max_retries: Number of submission retry attempts.

    Returns:
        Path to the downloaded MP4 clip.
    """
    scene_num = scene["scene_number"]
    output_path = _OUTPUT_DIR / f"ep{ep_num}_scene{scene_num}.mp4"

    if output_path.exists():
        return output_path

    prompt = _build_prompt(scene, characters)
    duration = min(max(scene.get("duration_seconds", 5), 5), 10)  # Kling supports 5–10s

    task_id: str | None = None

    for attempt in range(max_retries):
        try:
            start = time.time()
            resp = requests.post(
                f"{_KLING_BASE_URL}/videos/text2video",
                headers=_headers(),
                json={
                    "prompt": prompt,
                    "duration": duration,
                    "aspect_ratio": "9:16",
                    "model": "kling-v1",
                },
                timeout=30,
            )
            log_api_call("kling/text2video", resp.status_code, time.time() - start)
            resp.raise_for_status()
            task_id = resp.json()["task_id"]
            break
        except Exception as exc:
            if attempt == max_retries - 1:
                print(f"[video_gen] Scene {scene_num}: submission failed after {max_retries} tries: {exc}. Using fallback.")
                _make_fallback_video(scene, output_path, characters)
                return output_path
            await asyncio.sleep(2 ** attempt)

    if not task_id:
        _make_fallback_video(scene, output_path, characters)
        return output_path

    # Poll for completion
    deadline = time.time() + _TIMEOUT_SECONDS
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            start = time.time()
            poll = requests.get(
                f"{_KLING_BASE_URL}/videos/text2video/{task_id}",
                headers=_headers(),
                timeout=15,
            )
            log_api_call(f"kling/status/{task_id}", poll.status_code, time.time() - start)
            poll.raise_for_status()
            data = poll.json()
            if data.get("status") == "completed":
                video_url = data["video_url"]
                dl = requests.get(video_url, timeout=120)
                output_path.write_bytes(dl.content)
                return output_path
            if data.get("status") == "failed":
                print(f"[video_gen] Scene {scene_num}: Kling job failed. Using fallback.")
                _make_fallback_video(scene, output_path, characters)
                return output_path
        except Exception as exc:
            print(f"[video_gen] Scene {scene_num}: poll error: {exc}")

    print(f"[video_gen] Scene {scene_num}: timed out after {_TIMEOUT_SECONDS}s. Using fallback.")
    _make_fallback_video(scene, output_path, characters)
    return output_path


async def generate_all(script: dict[str, Any]) -> list[Path]:
    """Generate video clips for all scenes in parallel.

    Args:
        script: Parsed script dict from script_agent.

    Returns:
        List of MP4 clip paths in scene order.
    """
    characters_data = json.loads(_CHARACTERS_PATH.read_text())["characters"]
    ep_num = script["episode_number"]

    with StageTimer("Video gen"):
        tasks = [
            _generate_clip(scene, ep_num, characters_data)
            for scene in script.get("scenes", [])
        ]
        clips: list[Path] = list(await asyncio.gather(*tasks))

    return clips
