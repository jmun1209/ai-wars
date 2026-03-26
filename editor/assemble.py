"""Video assembler — combines clips, audio, captions, and music into final MP4."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

import ffmpeg
import whisper
from dotenv import load_dotenv

from utils.logger import StageTimer

load_dotenv()

_OUTPUT_DIR = Path(__file__).parent.parent / "output" / "final"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_ASSETS_DIR = Path(__file__).parent.parent / "assets"
_MUSIC_PATH = _ASSETS_DIR / "background_music.mp3"

_LINE_GAP = 0.2  # seconds of silence between dialogue lines


def _image_to_clip(image_path: Path, duration: float, output_path: Path) -> None:
    """Convert a static PNG image to a video clip of the given duration.

    Args:
        image_path: Source PNG file.
        duration: How many seconds the clip should hold.
        output_path: Destination MP4 file.
    """
    (
        ffmpeg
        .input(str(image_path), loop=1, t=duration, framerate=24)
        .filter("scale", 1080, 1920)
        .output(
            str(output_path),
            vcodec="libx264",
            pix_fmt="yuv420p",
            r=24,
            an=None,  # no audio track
        )
        .overwrite_output()
        .run(quiet=True)
    )


def _get_audio_duration(path: Path) -> float:
    """Return the duration of an audio file in seconds using ffprobe."""
    probe = ffmpeg.probe(str(path))
    return float(probe["format"]["duration"])


def _assemble_scene(
    scene: dict[str, Any],
    video_clip: Path,
    audio_files: dict[tuple[int, int], Path],
    ep_num: int,
    tmp_dir: Path,
) -> Path:
    """Layer audio onto a video clip and burn any visual overlay text.

    Args:
        scene: Scene dict from the script.
        video_clip: Path to the raw scene video clip.
        audio_files: Mapping of (scene_num, line_idx) → MP3 path.
        ep_num: Episode number for temp file naming.
        tmp_dir: Temporary directory for intermediate files.

    Returns:
        Path to the assembled scene MP4.
    """
    scene_num = scene["scene_number"]
    dialogue = scene.get("dialogue", [])

    # Build a silence-padded audio track by concatenating line audio files
    audio_inputs: list[ffmpeg.nodes.FilterableStream] = []
    silence_path = tmp_dir / "silence.mp3"

    if not silence_path.exists():
        (
            ffmpeg
            .input("anullsrc=r=44100:cl=mono", f="lavfi", t=_LINE_GAP)
            .output(str(silence_path), acodec="libmp3lame", ar=44100)
            .overwrite_output()
            .run(quiet=True)
        )

    for line_idx in range(len(dialogue)):
        key = (scene_num, line_idx)
        if key in audio_files:
            audio_inputs.append(ffmpeg.input(str(audio_files[key])).audio)
            # Small gap after each line
            audio_inputs.append(ffmpeg.input(str(silence_path)).audio)

    scene_out = tmp_dir / f"scene_{scene_num}_assembled.mp4"

    if not audio_inputs:
        # No dialogue: just copy the clip
        shutil.copy(video_clip, scene_out)
        return scene_out

    # Concatenate all audio segments
    combined_audio = ffmpeg.concat(*audio_inputs, v=0, a=1)
    combined_audio_path = tmp_dir / f"scene_{scene_num}_audio.mp3"
    (
        combined_audio
        .output(str(combined_audio_path), acodec="libmp3lame", ar=44100)
        .overwrite_output()
        .run(quiet=True)
    )

    audio_duration = _get_audio_duration(combined_audio_path)
    video_input = ffmpeg.input(str(video_clip))

    # Loop video to match audio length if needed
    video_stream = video_input.video.filter("loop", loop=-1, size=999999).filter(
        "setpts", "N/FRAME_RATE/TB"
    ).trim(duration=audio_duration).filter("setpts", "PTS-STARTPTS")
    audio_stream = ffmpeg.input(str(combined_audio_path)).audio

    # Burn visual overlay text if present
    overlay_text = scene.get("visual_overlay")
    if overlay_text:
        escaped = overlay_text.replace("'", "\\'").replace(":", "\\:")
        video_stream = video_stream.drawtext(
            text=escaped,
            fontcolor="white",
            fontsize=48,
            box=1,
            boxcolor="black@0.5",
            boxborderw=10,
            x="(w-text_w)/2",
            y="h*0.15",
        )

    (
        ffmpeg
        .output(video_stream, audio_stream, str(scene_out), vcodec="libx264", acodec="aac", pix_fmt="yuv420p")
        .overwrite_output()
        .run(quiet=True)
    )

    return scene_out


def _build_srt(segments: list[dict[str, Any]], srt_path: Path) -> None:
    """Write a Whisper segments list to an SRT subtitle file.

    Args:
        segments: List of Whisper segment dicts with start, end, text.
        srt_path: Destination SRT file path.
    """
    lines: list[str] = []

    def _fmt(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt(seg['start'])} --> {_fmt(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")

    srt_path.write_text("\n".join(lines))


async def build(
    script: dict[str, Any],
    audio_files: dict[tuple[int, int], Path],
    video_clips: list[Path],
    visual_files: dict[str, Path],
) -> Path:
    """Assemble the final episode MP4 from all generated assets.

    Steps:
        1. Assemble each scene (audio + video + overlay)
        2. Prepend title card and append takeaway card
        3. Concatenate all segments
        4. Add background music (ducked to 15%)
        5. Run Whisper for auto-captions and burn subtitles
        6. Export final H.264/AAC video

    Args:
        script: Parsed script dict.
        audio_files: Mapping of (scene_num, line_idx) → MP3 path.
        video_clips: List of scene clip paths in order.
        visual_files: Dict with 'title_card', 'concept', 'takeaway_card' image paths.

    Returns:
        Path to the final exported MP4.
    """
    ep_num = script["episode_number"]

    with StageTimer("Assembly"):
        with tempfile.TemporaryDirectory(prefix=f"aiwars_ep{ep_num}_") as _tmp:
            tmp_dir = Path(_tmp)

            # --- Step 1: Per-scene assembly ---
            assembled_scenes: list[Path] = []
            for scene, clip in zip(script["scenes"], video_clips):
                assembled = await asyncio.to_thread(
                    _assemble_scene, scene, clip, audio_files, ep_num, tmp_dir
                )
                assembled_scenes.append(assembled)

            # --- Step 2: Title card (2s) and takeaway card (3s) ---
            title_clip = tmp_dir / "title_card.mp4"
            takeaway_clip = tmp_dir / "takeaway_card.mp4"
            await asyncio.to_thread(_image_to_clip, visual_files["title_card"], 2.0, title_clip)
            await asyncio.to_thread(_image_to_clip, visual_files["takeaway_card"], 3.0, takeaway_clip)

            # --- Step 3: Concatenate all segments ---
            all_segments = [title_clip] + assembled_scenes + [takeaway_clip]
            concat_path = tmp_dir / "concat.mp4"

            # Write a concat list file
            list_file = tmp_dir / "concat_list.txt"
            list_file.write_text(
                "\n".join(f"file '{p}'" for p in all_segments)
            )
            (
                ffmpeg
                .input(str(list_file), format="concat", safe=0)
                .output(str(concat_path), c="copy")
                .overwrite_output()
                .run(quiet=True)
            )

            # --- Step 4: Add background music ---
            draft_path = _OUTPUT_DIR / f"ep{ep_num}_draft.mp4"
            if _MUSIC_PATH.exists():
                video_in = ffmpeg.input(str(concat_path))
                music_in = ffmpeg.input(str(_MUSIC_PATH), stream_loop=-1)
                # Duck music to 15%
                ducked_music = music_in.audio.filter("volume", 0.15)
                # Mix dialogue audio + ducked music
                mixed = ffmpeg.filter(
                    [video_in.audio, ducked_music],
                    "amix",
                    inputs=2,
                    duration="first",
                )
                (
                    ffmpeg
                    .output(video_in.video, mixed, str(draft_path), vcodec="copy", acodec="aac")
                    .overwrite_output()
                    .run(quiet=True)
                )
            else:
                shutil.copy(concat_path, draft_path)

            # --- Step 5: Auto-captions with Whisper ---
            srt_path = _OUTPUT_DIR / f"ep{ep_num}.srt"
            model = whisper.load_model("base")
            result = await asyncio.to_thread(model.transcribe, str(draft_path))
            _build_srt(result["segments"], srt_path)

            # --- Step 6: Burn subtitles and export final video ---
            final_path = _OUTPUT_DIR / f"ep{ep_num}_final.mp4"
            escaped_srt = str(srt_path).replace("'", "\\'").replace(":", "\\:")
            (
                ffmpeg
                .input(str(draft_path))
                .video
                .filter(
                    "subtitles",
                    escaped_srt,
                    force_style=(
                        "FontName=Arial,FontSize=18,PrimaryColour=&Hffffff,"
                        "OutlineColour=&H000000,Outline=2,Alignment=2,MarginV=30"
                    ),
                )
                .output(
                    str(final_path),
                    vcodec="libx264",
                    acodec="aac",
                    pix_fmt="yuv420p",
                    s="1080x1920",
                    crf=23,
                    preset="fast",
                    **{"b:a": "128k"},
                )
                .overwrite_output()
                .run(quiet=True)
            )

    return final_path
