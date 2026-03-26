"""Video assembler — combines clips, audio, captions, and music into final MP4."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import whisper
from dotenv import load_dotenv

from utils.logger import StageTimer

load_dotenv()

_OUTPUT_DIR = Path(__file__).parent.parent / "output" / "final"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_ASSETS_DIR = Path(__file__).parent.parent / "assets"
_MUSIC_PATH = _ASSETS_DIR / "background_music.mp3"

_LINE_GAP = 0.2  # seconds of silence between dialogue lines
_W, _H = 1080, 1920


def _run(cmd: list[str], label: str = "") -> None:
    """Run an ffmpeg command, raising with full stderr on failure.

    Args:
        cmd: Command + arguments list.
        label: Human-readable label for error messages.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed{' (' + label + ')' if label else ''}:\n"
            f"CMD: {' '.join(cmd)}\n"
            f"STDERR: {result.stderr[-2000:]}"
        )


def _probe_has_audio(path: Path) -> bool:
    """Return True if the file has at least one audio stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return "audio" in result.stdout


def _probe_duration(path: Path) -> float:
    """Return duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 5.0


def _image_to_clip(image_path: Path, duration: float, output_path: Path) -> None:
    """Convert a PNG to a video clip with silent audio track.

    Args:
        image_path: Source PNG.
        duration: Clip duration in seconds.
        output_path: Destination MP4.
    """
    _run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-vf", f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2",
        "-vcodec", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
        "-acodec", "aac", "-ar", "44100", "-ac", "2",
        "-shortest", str(output_path),
    ], label=f"image_to_clip {image_path.name}")


def _concat_audio_files(audio_paths: list[Path], output_path: Path, gap: float = _LINE_GAP) -> None:
    """Concatenate multiple audio files with a small gap between each.

    Args:
        audio_paths: Ordered list of MP3 files.
        output_path: Destination MP3.
        gap: Silence gap in seconds between lines.
    """
    if not audio_paths:
        # Generate silent audio of 1 second
        _run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "1", "-acodec", "libmp3lame", str(output_path),
        ], label="silent audio")
        return

    # Build filter_complex for concat with silence gaps
    inputs: list[str] = []
    filter_parts: list[str] = []
    labels: list[str] = []

    for i, p in enumerate(audio_paths):
        inputs += ["-i", str(p)]
        filter_parts.append(f"[{i}]apad=pad_dur={gap}[a{i}]")
        labels.append(f"[a{i}]")

    filter_complex = ";".join(filter_parts) + ";" + "".join(labels) + f"concat=n={len(audio_paths)}:v=0:a=1[out]"

    _run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-acodec", "libmp3lame", "-ar", "44100", str(output_path),
    ], label="concat_audio")


def _assemble_scene(
    scene: dict[str, Any],
    video_clip: Path,
    audio_files: dict[tuple[int, int], Path],
    tmp_dir: Path,
) -> Path:
    """Layer dialogue audio onto a video clip and burn any overlay text.

    Args:
        scene: Scene dict from the script.
        video_clip: Path to raw scene video clip.
        audio_files: Mapping of (scene_num, line_idx) → MP3 path.
        tmp_dir: Temporary directory.

    Returns:
        Path to assembled scene MP4.
    """
    scene_num = scene["scene_number"]
    dialogue = scene.get("dialogue", [])
    scene_out = tmp_dir / f"scene_{scene_num}_assembled.mp4"

    # Collect audio lines for this scene
    line_paths = [
        audio_files[(scene_num, i)]
        for i in range(len(dialogue))
        if (scene_num, i) in audio_files
    ]

    if not line_paths:
        # No audio — ensure clip has an audio track and just copy
        if _probe_has_audio(video_clip):
            shutil.copy(video_clip, scene_out)
        else:
            dur = _probe_duration(video_clip)
            _run([
                "ffmpeg", "-y", "-i", str(video_clip),
                "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                "-t", str(dur),
                "-c:v", "copy", "-acodec", "aac", "-ar", "44100", "-ac", "2",
                "-shortest", str(scene_out),
            ], label=f"add_silent_audio scene {scene_num}")
        return scene_out

    # Concatenate dialogue audio
    combined_audio = tmp_dir / f"scene_{scene_num}_audio.mp3"
    _concat_audio_files(line_paths, combined_audio)
    audio_dur = _probe_duration(combined_audio)

    # Build video filter: scale + loop to fill audio duration
    vf = (
        f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
        f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2,"
        f"loop=loop=-1:size=999:start=0,trim=duration={audio_dur},setpts=PTS-STARTPTS"
    )

    # Add overlay text if present
    overlay_text = scene.get("visual_overlay")
    if overlay_text:
        escaped = overlay_text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
        vf += (
            f",drawtext=text='{escaped}':fontcolor=white:fontsize=48"
            f":box=1:boxcolor=black@0.5:boxborderw=10"
            f":x=(w-text_w)/2:y=h*0.15"
        )

    _run([
        "ffmpeg", "-y",
        "-i", str(video_clip),
        "-i", str(combined_audio),
        "-vf", vf,
        "-t", str(audio_dur),
        "-vcodec", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
        "-acodec", "aac", "-ar", "44100", "-ac", "2",
        str(scene_out),
    ], label=f"assemble scene {scene_num}")

    return scene_out


def _build_srt(segments: list[dict[str, Any]], srt_path: Path) -> None:
    """Write Whisper segments to an SRT file."""
    def _fmt(s: float) -> str:
        h, rem = divmod(int(s), 3600)
        m, sec = divmod(rem, 60)
        ms = int((s % 1) * 1000)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"

    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        lines += [str(i), f"{_fmt(seg['start'])} --> {_fmt(seg['end'])}", seg["text"].strip(), ""]
    srt_path.write_text("\n".join(lines))


async def build(
    script: dict[str, Any],
    audio_files: dict[tuple[int, int], Path],
    video_clips: list[Path],
    visual_files: dict[str, Path],
) -> Path:
    """Assemble the final episode MP4.

    Args:
        script: Parsed script dict.
        audio_files: Mapping of (scene_num, line_idx) → MP3 path.
        video_clips: List of scene clip paths in order.
        visual_files: Dict with title_card, concept, takeaway_card PNG paths.

    Returns:
        Path to the final exported MP4.
    """
    ep_num = script["episode_number"]

    with StageTimer("Assembly"):
        with tempfile.TemporaryDirectory(prefix=f"aiwars_ep{ep_num}_") as _tmp:
            tmp_dir = Path(_tmp)

            # --- Step 1: Assemble each scene ---
            assembled: list[Path] = []
            for scene, clip in zip(script["scenes"], video_clips):
                s = await asyncio.to_thread(_assemble_scene, scene, clip, audio_files, tmp_dir)
                assembled.append(s)

            # --- Step 2: Image cards with silent audio ---
            title_clip = tmp_dir / "title_card.mp4"
            takeaway_clip = tmp_dir / "takeaway_card.mp4"
            await asyncio.gather(
                asyncio.to_thread(_image_to_clip, visual_files["title_card"], 2.0, title_clip),
                asyncio.to_thread(_image_to_clip, visual_files["takeaway_card"], 3.0, takeaway_clip),
            )

            # --- Step 3: Concat everything ---
            all_segments = [title_clip] + assembled + [takeaway_clip]
            concat_list = tmp_dir / "concat.txt"
            concat_list.write_text("\n".join(f"file '{p}'" for p in all_segments))
            concat_path = tmp_dir / "concat.mp4"
            _run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-vcodec", "libx264", "-pix_fmt", "yuv420p",
                "-acodec", "aac", "-ar", "44100", "-ac", "2",
                str(concat_path),
            ], label="concat segments")

            # --- Step 4: Background music ---
            draft_path = _OUTPUT_DIR / f"ep{ep_num}_draft.mp4"
            if _MUSIC_PATH.exists():
                _run([
                    "ffmpeg", "-y",
                    "-i", str(concat_path),
                    "-stream_loop", "-1", "-i", str(_MUSIC_PATH),
                    "-filter_complex", "[1:a]volume=0.15[music];[0:a][music]amix=inputs=2:duration=first[aout]",
                    "-map", "0:v", "-map", "[aout]",
                    "-vcodec", "copy", "-acodec", "aac",
                    "-shortest", str(draft_path),
                ], label="add background music")
            else:
                shutil.copy(concat_path, draft_path)

            # --- Step 5: Whisper captions ---
            srt_path = _OUTPUT_DIR / f"ep{ep_num}.srt"
            model = whisper.load_model("base")
            result = await asyncio.to_thread(model.transcribe, str(draft_path))
            _build_srt(result["segments"], srt_path)

            # --- Step 6: Burn subtitles + final export ---
            final_path = _OUTPUT_DIR / f"ep{ep_num}_final.mp4"
            srt_escaped = str(srt_path).replace("'", "\\'").replace(":", "\\:")
            _run([
                "ffmpeg", "-y", "-i", str(draft_path),
                "-vf", (
                    f"subtitles='{srt_escaped}':"
                    "force_style='FontName=Arial,FontSize=18,"
                    "PrimaryColour=&Hffffff,OutlineColour=&H000000,"
                    "Outline=2,Alignment=2,MarginV=30'"
                ),
                "-vcodec", "libx264", "-pix_fmt", "yuv420p",
                "-s", f"{_W}x{_H}",
                "-crf", "23", "-preset", "fast",
                "-acodec", "aac", "-b:a", "128k",
                str(final_path),
            ], label="burn subtitles + final export")

    return final_path
