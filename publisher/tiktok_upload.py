"""TikTok Content Posting API — upload episode video as a draft."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from utils.logger import StageTimer, log_api_call

load_dotenv()

_TIKTOK_BASE = "https://open.tiktokapis.com/v2"
_POLL_INTERVAL = 3  # seconds between status checks
_POLL_TIMEOUT = 120  # max seconds to wait for inbox delivery

_ERROR_MESSAGES: dict[str, str] = {
    "spam_risk_too_many_posts": (
        "WARNING: TikTok flagged this as spam (too many posts). "
        "Wait a few hours before uploading again."
    ),
    "video_duration_check_failed": (
        "ERROR: Video exceeds TikTok's duration limit for your account tier. "
        "Trim the video and retry."
    ),
    "authorization_failed": (
        "ERROR: TikTok access token is invalid or expired. "
        "Re-run `python setup_tiktok_auth.py` to refresh your token."
    ),
}


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['TIKTOK_ACCESS_TOKEN']}",
        "Content-Type": "application/json; charset=UTF-8",
    }


def _handle_error(error_code: str) -> None:
    """Print a friendly message for known TikTok error codes."""
    msg = _ERROR_MESSAGES.get(error_code, f"TikTok API error: {error_code}")
    print(f"[tiktok_upload] {msg}")


async def _get_creator_info() -> dict[str, Any]:
    """Fetch the authenticated creator's posting capabilities.

    Returns:
        Creator info dict including privacy_level_options.
    """
    start = time.time()
    resp = requests.post(
        f"{_TIKTOK_BASE}/post/publish/creator_info/query/",
        headers=_auth_headers(),
        timeout=15,
    )
    log_api_call("tiktok/creator_info", resp.status_code, time.time() - start)
    resp.raise_for_status()
    data = resp.json()
    error_code = data.get("error", {}).get("code", "ok")
    if error_code != "ok":
        _handle_error(error_code)
        raise RuntimeError(f"TikTok creator_info error: {error_code}")
    return data.get("data", {})


async def _init_upload(video_path: Path) -> tuple[str, str]:
    """Initialise a FILE_UPLOAD session with TikTok.

    Args:
        video_path: Path to the MP4 to upload.

    Returns:
        Tuple of (publish_id, upload_url).
    """
    size = video_path.stat().st_size
    start = time.time()
    resp = requests.post(
        f"{_TIKTOK_BASE}/post/publish/inbox/video/init/",
        headers=_auth_headers(),
        json={
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            }
        },
        timeout=30,
    )
    log_api_call("tiktok/video/init", resp.status_code, time.time() - start)
    resp.raise_for_status()
    data = resp.json()
    error_code = data.get("error", {}).get("code", "ok")
    if error_code != "ok":
        _handle_error(error_code)
        raise RuntimeError(f"TikTok init error: {error_code}")
    result = data["data"]
    return result["publish_id"], result["upload_url"]


async def _upload_video(upload_url: str, video_path: Path) -> None:
    """PUT the raw video bytes to the TikTok upload URL.

    Args:
        upload_url: Pre-signed upload URL from init step.
        video_path: Path to the local MP4.
    """
    size = video_path.stat().st_size
    video_bytes = video_path.read_bytes()
    start = time.time()
    resp = requests.put(
        upload_url,
        data=video_bytes,
        headers={
            "Content-Range": f"bytes 0-{size - 1}/{size}",
            "Content-Type": "video/mp4",
            "Content-Length": str(size),
        },
        timeout=300,
    )
    log_api_call("tiktok/video/upload PUT", resp.status_code, time.time() - start)
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"TikTok upload failed with status {resp.status_code}: {resp.text}")


async def _poll_status(publish_id: str) -> str:
    """Poll TikTok until the video reaches SEND_TO_USER_INBOX or fails.

    Args:
        publish_id: The publish_id returned by the init step.

    Returns:
        Final status string.
    """
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        start = time.time()
        resp = requests.post(
            f"{_TIKTOK_BASE}/post/publish/status/fetch/",
            headers=_auth_headers(),
            json={"publish_id": publish_id},
            timeout=15,
        )
        log_api_call("tiktok/status/fetch", resp.status_code, time.time() - start)
        resp.raise_for_status()
        data = resp.json()
        error_code = data.get("error", {}).get("code", "ok")
        if error_code != "ok":
            _handle_error(error_code)
            raise RuntimeError(f"TikTok status error: {error_code}")
        status = data.get("data", {}).get("status", "")
        if status == "SEND_TO_USER_INBOX":
            return status
        if status in ("FAILED", "PUBLISH_COMPLETE"):
            return status
    return "TIMEOUT"


async def upload_draft(
    video_path: Path,
    tiktok_copy: dict[str, Any],
    max_retries: int = 3,
) -> dict[str, Any]:
    """Upload a video to TikTok as an inbox draft.

    The video lands in the TikTok app's drafts folder where the creator
    can review and publish manually.

    Args:
        video_path: Path to the final MP4.
        tiktok_copy: Caption dict from caption_agent containing full_caption.
        max_retries: Number of retry attempts for the upload flow.

    Returns:
        Result dict with success, publish_id, status, message, caption_to_paste.
    """
    with StageTimer("TikTok upload"):
        # Verify creator info first
        await _get_creator_info()

        for attempt in range(max_retries):
            try:
                publish_id, upload_url = await _init_upload(video_path)
                await _upload_video(upload_url, video_path)
                final_status = await _poll_status(publish_id)
                break
            except Exception as exc:
                if attempt == max_retries - 1:
                    return {
                        "success": False,
                        "publish_id": None,
                        "status": "error",
                        "message": str(exc),
                        "caption_to_paste": tiktok_copy.get("full_caption", ""),
                    }
                wait = 2 ** attempt
                print(f"[tiktok_upload] Attempt {attempt + 1} failed: {exc}. Retrying in {wait}s…")
                await asyncio.sleep(wait)

    return {
        "success": True,
        "publish_id": publish_id,
        "status": "draft",
        "message": "Video uploaded to TikTok drafts. Open TikTok to review and publish.",
        "caption_to_paste": tiktok_copy.get("full_caption", ""),
    }
