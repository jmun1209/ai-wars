"""Voice generator — creates MP3 files for each dialogue line via ElevenLabs."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

from utils.logger import StageTimer, log_api_call

load_dotenv()

_OUTPUT_DIR = Path(__file__).parent.parent / "output" / "audio"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_CHARACTERS_PATH = Path(__file__).parent.parent / "config" / "characters.json"

# Voice settings applied to every character
_VOICE_SETTINGS = VoiceSettings(stability=0.5, similarity_boost=0.75, style=0.3)


def _get_voice_id(character_name: str, characters: dict[str, Any]) -> str:
    """Look up the ElevenLabs voice ID for a character."""
    key = character_name.lower()
    if key in characters:
        return characters[key]["voice_id"]
    # Fall back to narrator voice for unknown characters
    return os.environ.get("ELEVENLABS_NARRATOR_VOICE_ID", "")


async def _generate_line(
    client: ElevenLabs,
    voice_id: str,
    text: str,
    output_path: Path,
    max_retries: int = 3,
) -> Path:
    """Generate a single audio line and save it to output_path.

    Args:
        client: ElevenLabs client instance.
        voice_id: ElevenLabs voice ID string.
        text: Text to synthesize.
        output_path: Where to write the MP3 file.
        max_retries: Number of retry attempts with exponential backoff.

    Returns:
        Path to the saved MP3 file.
    """
    if output_path.exists():
        return output_path

    for attempt in range(max_retries):
        try:
            start = time.time()
            audio_iter = client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id="eleven_multilingual_v2",
                voice_settings=_VOICE_SETTINGS,
            )
            # Collect bytes from the generator
            audio_bytes = b"".join(audio_iter)
            duration = time.time() - start
            log_api_call(f"elevenlabs/tts voice={voice_id}", 200, duration)
            output_path.write_bytes(audio_bytes)
            return output_path
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"[voice_gen] Error on attempt {attempt + 1}: {exc}. Retrying in {wait}s…")
            await asyncio.sleep(wait)

    raise RuntimeError(f"voice_gen: max retries exceeded for {output_path.name}")


async def _generate_scene(
    client: ElevenLabs,
    scene: dict[str, Any],
    ep_num: int,
    characters: dict[str, Any],
) -> dict[tuple[int, int], Path]:
    """Generate audio for all dialogue lines in one scene sequentially.

    Returns:
        Mapping of (scene_number, line_index) → file path.
    """
    results: dict[tuple[int, int], Path] = {}
    scene_num = scene["scene_number"]

    for line_idx, dialogue in enumerate(scene.get("dialogue", [])):
        character = dialogue["character"].lower()
        voice_id = _get_voice_id(character, characters)
        filename = f"ep{ep_num}_scene{scene_num}_line{line_idx}.mp3"
        output_path = _OUTPUT_DIR / filename
        path = await _generate_line(client, voice_id, dialogue["line"], output_path)
        results[(scene_num, line_idx)] = path

    return results


async def generate_all(script: dict[str, Any]) -> dict[tuple[int, int], Path]:
    """Generate all voiceover audio files for an episode.

    Processes scenes in parallel; lines within each scene run sequentially
    to preserve natural pacing.

    Args:
        script: Parsed script dict from script_agent.

    Returns:
        Dict mapping (scene_number, line_index) to MP3 file paths.
        Also includes ("outro", 0) → narrator takeaway line.
    """
    import json

    characters_data = json.loads(_CHARACTERS_PATH.read_text())["characters"]
    ep_num = script["episode_number"]
    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

    with StageTimer("Voice gen"):
        # Run all scenes in parallel
        scene_tasks = [
            _generate_scene(client, scene, ep_num, characters_data)
            for scene in script.get("scenes", [])
        ]
        scene_results = await asyncio.gather(*scene_tasks)

        audio_files: dict[tuple[int, int], Path] = {}
        for result in scene_results:
            audio_files.update(result)

        # Narrator outro line
        narrator_voice_id = os.environ.get("ELEVENLABS_NARRATOR_VOICE_ID", "")
        if narrator_voice_id and script.get("outro", {}).get("takeaway_line"):
            outro_path = _OUTPUT_DIR / f"ep{ep_num}_outro_narrator.mp3"
            await _generate_line(
                client,
                narrator_voice_id,
                script["outro"]["takeaway_line"],
                outro_path,
            )
            audio_files[("outro", 0)] = outro_path

    return audio_files
