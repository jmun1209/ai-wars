"""Showrunner agent — generates the high-level season vision using Claude Opus."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

from utils.logger import log_api_call, log_token_usage

load_dotenv()

_SEASONS_DIR = Path(__file__).parent.parent / "output" / "seasons"
_SEASONS_DIR.mkdir(parents=True, exist_ok=True)

_SYSTEM_PROMPT = """You are the executive showrunner and creator of "AI Wars," an educational
TikTok series where anthropomorphic AI models compete in challenges that teach viewers
how AI actually works.

Characters:
- Claudia (Claude/Anthropic): Thoughtful, ethical, wins by reasoning. Color: purple.
- Geppetto (GPT-4/OpenAI): Overconfident, fast, occasionally hallucinates. Color: green.
- Gemma (Gemini/Google): Multimodal show-off, competitive, hates being second. Color: blue.
- Lama (Llama/Meta): Scrappy open-source underdog, anti-paywall, community-driven. Color: orange.

Your job is to design a complete season vision that:
1. Has a clear educational mission — by the end of the season, viewers should understand
   how modern AI systems actually work, from fundamentals to advanced topics
2. Has a compelling narrative arc — character relationships evolve, rivalries form,
   alliances shift, stakes escalate
3. Builds knowledge progressively — early episodes cover fundamentals, later episodes
   cover advanced topics that build on earlier ones
4. Has a satisfying season finale that resolves the main conflict AND delivers the
   biggest educational payoff

Think like a Netflix showrunner who also has a PhD in machine learning.

Return ONLY valid JSON. No markdown, no explanation."""


_USER_PROMPT = """Design a complete Season {season_number} vision for AI Wars.

Season {season_number} context: {context}

Return a JSON object with this exact structure:
{{
  "season_number": int,
  "tagline": string (one punchy sentence that sells the season),
  "premise": string (2-3 sentences — what is this season about narratively),
  "educational_mission": string (what viewers will understand by the end),
  "narrative_arc": string (the overall story shape — how does it start, escalate, resolve),
  "overarching_antagonist_force": string (the main dramatic tension or challenge across the season),
  "character_arcs": {{
    "claudia": string (how she grows or changes this season),
    "geppetto": string (how he grows or changes),
    "gemma": string (how she grows or changes),
    "lama": string (how they grow or change)
  }},
  "educational_progression": [
    string (topic area for episodes 1-5),
    string (topic area for episodes 6-10),
    string (topic area for episodes 11-15),
    string (topic area for episodes 16-20)
  ],
  "season_finale_stakes": string (what is at risk in the finale),
  "recurring_elements": [string] (2-3 running jokes, motifs, or callbacks that reward loyal viewers)
}}"""


async def _call_with_retry(
    client: anthropic.Anthropic,
    season_number: int,
    context: str,
    max_retries: int = 3,
) -> anthropic.types.Message:
    """Call Claude Opus with exponential backoff retry."""
    user_msg = _USER_PROMPT.format(season_number=season_number, context=context)
    for attempt in range(max_retries):
        try:
            start = time.time()
            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            duration = time.time() - start
            log_api_call("anthropic/messages (showrunner)", 200, duration)
            log_token_usage("claude-opus-4-5", response.usage.input_tokens, response.usage.output_tokens)
            return response
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
        except anthropic.APIError as exc:
            if attempt == max_retries - 1:
                raise
            print(f"[showrunner] API error attempt {attempt + 1}: {exc}")
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("showrunner_agent: max retries exceeded")


async def generate(season_number: int, context: str = "") -> dict[str, Any]:
    """Generate the high-level season vision.

    Args:
        season_number: Which season this is.
        context: Any optional seed context (e.g. "focus on generative AI").

    Returns:
        Season vision dict. Saved to output/seasons/season_{N}_vision.json.
    """
    vision_path = _SEASONS_DIR / f"season_{season_number}_vision.json"
    if vision_path.exists():
        print(f"[showrunner] Loading existing vision from {vision_path}")
        return json.loads(vision_path.read_text())

    if not context:
        context = "This is the first season. Start from AI fundamentals and build to advanced topics."

    client = anthropic.Anthropic()
    response = await _call_with_retry(client, season_number, context)
    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        vision = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[showrunner] Raw response was:\n{raw[:500]}")
        raise RuntimeError(f"showrunner_agent: failed to parse JSON response: {exc}") from exc

    vision_path.write_text(json.dumps(vision, indent=2))
    print(f"[showrunner] Season vision saved to {vision_path}")
    return vision
