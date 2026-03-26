"""Caption agent — generates TikTok hook copy and hashtags via Claude Haiku."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import anthropic
from dotenv import load_dotenv

from utils.logger import StageTimer, log_api_call, log_token_usage

load_dotenv()

_SYSTEM_PROMPT = """You are a TikTok content strategist specializing in AI and tech education content.
You write captions that hook viewers in the first line, use strategic hashtags,
and drive engagement through curiosity and educational value.

Your captions follow this formula:
Line 1: Hook — a bold claim, surprising fact, or question (max 10 words)
Line 2: What they'll learn (max 15 words)
Line 3: Call to action (max 8 words)
Hashtags: 8-12 hashtags mixing broad (#AI #Tech) and niche (#AIEngineering #LLMs)

Return ONLY valid JSON."""


def _build_user_message(script: dict[str, Any], episode_config: dict[str, Any]) -> str:
    """Build the user message for caption generation."""
    return f"""
Episode concept: {script['concept']}
Educational takeaway: {script['educational_takeaway']}
Characters featured: {episode_config['characters']}
Episode title: {script['title']}

Return JSON:
{{
  "hook": string,
  "body": string,
  "cta": string,
  "hashtags": [string],
  "full_caption": string
}}

full_caption should be: hook + newline + body + newline + cta + newline + hashtags joined by spaces
"""


async def _call_with_retry(
    client: anthropic.Anthropic,
    user_message: str,
    max_retries: int = 3,
) -> anthropic.types.Message:
    """Call the Anthropic API with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            start = time.time()
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            duration = time.time() - start
            log_api_call("anthropic/messages (caption)", 200, duration)
            log_token_usage(
                "claude-haiku-4-5-20251001",
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            return response
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
        except anthropic.APIError as exc:
            if attempt == max_retries - 1:
                raise
            print(f"[caption_agent] API error (attempt {attempt + 1}): {exc}. Retrying…")
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("Caption agent: max retries exceeded")


async def generate(script: dict[str, Any], episode_config: dict[str, Any]) -> dict[str, Any]:
    """Generate TikTok caption copy for an episode.

    Args:
        script: Parsed script dict from script_agent.
        episode_config: Original episode configuration from the intake form.

    Returns:
        Dict with keys hook, body, cta, hashtags, full_caption.
    """
    with StageTimer("Caption agent"):
        client = anthropic.Anthropic()
        user_message = _build_user_message(script, episode_config)
        response = await _call_with_retry(client, user_message)

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        try:
            caption_data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"[caption_agent] Raw response:\n{raw[:500]}")
            raise RuntimeError(f"caption_agent: failed to parse JSON: {exc}") from exc

    return caption_data
