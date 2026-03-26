"""Season agent — the writers room that breaks the vision into 20 episode plans."""

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

_SYSTEM_PROMPT = """You are the head of the writers room for "AI Wars," an educational TikTok series.

You receive a showrunner's season vision and break it down into exactly 20 episode plans.

Your job:
1. Map each episode to a specific AI/ML concept that teaches something real and useful
2. Ensure concepts build on each other — don't teach fine-tuning before explaining what
   training is, don't teach RAG before explaining embeddings
3. Give each episode a compelling dramatic hook that makes the concept memorable
4. Spread all 4 characters across the season — no character disappears for more than 3 episodes
5. Make the finale epic — the highest stakes, the most important concept, the biggest payoff
6. Each episode should work as a standalone video AND reward viewers who've watched the season

Educational concept progression must feel like a curriculum, not random topics.

Return ONLY valid JSON. No markdown, no explanation."""


def _build_user_message(vision: dict[str, Any]) -> str:
    return f"""Season vision:
{json.dumps(vision, indent=2)}

Break this into exactly 20 episode plans. Each episode is a TikTok video, max 180 seconds.

Return a JSON object:
{{
  "season_number": int,
  "episodes": [
    {{
      "episode_number": int,
      "title": string,
      "concept": string (the specific AI/ML concept taught — be precise, e.g. "transformer attention mechanisms" not just "AI"),
      "concept_prerequisite": string or null (which earlier episode concept this builds on, if any),
      "characters": [string] (2-4 character keys from: claudia, geppetto, gemma, lama),
      "winner": string (character name + one sentence why — this IS the educational takeaway),
      "drama": string (1-2 sentences of conflict that makes the concept dramatic),
      "cliffhanger": string (1 sentence that makes viewers come back),
      "length_seconds": int (60-180),
      "narrative_role": string (e.g. "season opener", "rivalry escalation", "midseason twist", "finale"),
      "status": "planned"
    }}
  ]
}}"""


async def _call_with_retry(
    client: anthropic.Anthropic,
    vision: dict[str, Any],
    max_retries: int = 3,
) -> anthropic.types.Message:
    """Call Claude Opus with exponential backoff retry."""
    user_msg = _build_user_message(vision)
    for attempt in range(max_retries):
        try:
            start = time.time()
            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=8192,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            duration = time.time() - start
            log_api_call("anthropic/messages (season_agent)", 200, duration)
            log_token_usage("claude-opus-4-5", response.usage.input_tokens, response.usage.output_tokens)
            return response
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
        except anthropic.APIError as exc:
            if attempt == max_retries - 1:
                raise
            print(f"[season_agent] API error attempt {attempt + 1}: {exc}")
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("season_agent: max retries exceeded")


async def generate(vision: dict[str, Any]) -> dict[str, Any]:
    """Break a season vision into 20 episode plans.

    Args:
        vision: Season vision dict from showrunner_agent.

    Returns:
        Season plan dict with 20 episode configs.
        Saved to output/seasons/season_{N}_plan.json.
    """
    season_number = vision["season_number"]
    plan_path = _SEASONS_DIR / f"season_{season_number}_plan.json"

    if plan_path.exists():
        print(f"[season_agent] Loading existing plan from {plan_path}")
        return json.loads(plan_path.read_text())

    client = anthropic.Anthropic()
    response = await _call_with_retry(client, vision)
    plan = json.loads(response.content[0].text.strip())
    plan_path.write_text(json.dumps(plan, indent=2))
    print(f"[season_agent] Season plan saved to {plan_path}")
    return plan


def get_episode_config(plan: dict[str, Any], episode_number: int) -> dict[str, Any]:
    """Extract a single episode config from the season plan in pipeline format.

    Args:
        plan: Season plan dict.
        episode_number: 1-based episode number.

    Returns:
        episode_config dict compatible with the pipeline.
    """
    episodes = plan["episodes"]
    ep = next((e for e in episodes if e["episode_number"] == episode_number), None)
    if not ep:
        raise ValueError(f"Episode {episode_number} not found in season plan")
    return {
        "episode_number": ep["episode_number"],
        "title": ep["title"],
        "concept": ep["concept"],
        "characters": ep["characters"],
        "winner": ep["winner"],
        "drama": ep["drama"],
        "cliffhanger": ep["cliffhanger"],
        "length_seconds": ep.get("length_seconds", 120),
    }


def mark_episode_complete(plan: dict[str, Any], episode_number: int) -> None:
    """Mark an episode as generated in the season plan file.

    Args:
        plan: Season plan dict (mutated in place and saved).
        episode_number: Episode to mark complete.
    """
    season_number = plan["season_number"]
    plan_path = _SEASONS_DIR / f"season_{season_number}_plan.json"

    for ep in plan["episodes"]:
        if ep["episode_number"] == episode_number:
            ep["status"] = "generated"
            break

    plan_path.write_text(json.dumps(plan, indent=2))
