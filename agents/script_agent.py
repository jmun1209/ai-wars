"""Script agent — generates structured episode scripts via Claude API."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

from utils.logger import StageTimer, log_api_call, log_token_usage

load_dotenv()

_OUTPUT_DIR = Path(__file__).parent.parent / "output" / "scripts"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_CHARACTERS_PATH = Path(__file__).parent.parent / "config" / "characters.json"

_SYSTEM_PROMPT = """You are the head writer for "AI Wars," an educational short-form video series where
anthropomorphic AI models — Claudia (Claude), Geppetto (GPT), Gemma (Gemini), and
Lama (Llama) — compete in challenges that teach viewers real AI concepts.

Each episode is 60-90 seconds, formatted for TikTok vertical video (9:16).

Your job is to write a complete episode script that:
1. Opens with a hook in the first 3 seconds — a shocking fact, dramatic moment, or
   cliffhanger resolution
2. Introduces the challenge clearly in plain language any viewer can understand
3. Shows each character attempting the challenge in a way that reflects their
   personality AND teaches something real about how that AI model actually works
4. Has a clear winner with a one-sentence explanation of WHY they won (this is
   the educational takeaway)
5. Ends on a cliffhanger to drive return viewers

Rules:
- Every piece of drama must have an educational reason. If Geppetto hallucinates,
  explain what hallucination is. If Claudia over-explains, explain why safety
  reasoning matters.
- Keep dialogue punchy. Each line max 15 words.
- Write shot descriptions as if directing a short film. Be specific.
- The educational takeaway must be stated explicitly and memorably.

Return ONLY valid JSON. No markdown, no explanation, no code blocks."""


def _build_user_message(episode_config: dict[str, Any], characters: dict[str, Any]) -> str:
    """Build the user message for the script generation prompt."""
    return f"""
Episode number: {episode_config['episode_number']}
Title: {episode_config['title']}
AI concept to teach: {episode_config['concept']}
Characters in this episode: {episode_config['characters']}
Who wins and why: {episode_config['winner']}
Drama/conflict: {episode_config['drama']}
Cliffhanger: {episode_config['cliffhanger']}
Target length: {episode_config['length_seconds']} seconds

Character personalities:
{json.dumps(characters, indent=2)}

Return a JSON object with this exact structure:
{{
  "episode_number": int,
  "title": string,
  "concept": string,
  "educational_takeaway": string,
  "hook_text": string,
  "scenes": [
    {{
      "scene_number": int,
      "duration_seconds": int,
      "shot_description": string,
      "dialogue": [
        {{
          "character": string,
          "line": string,
          "emotion": string
        }}
      ],
      "educational_note": string,
      "visual_overlay": string or null
    }}
  ],
  "outro": {{
    "winner": string,
    "takeaway_line": string,
    "cliffhanger_text": string
  }}
}}
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
                model="claude-opus-4-5",
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            duration = time.time() - start
            log_api_call("anthropic/messages (script)", 200, duration)
            log_token_usage(
                "claude-opus-4-5",
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            return response
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            await asyncio.sleep(wait)
        except anthropic.APIError as exc:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"[script_agent] API error (attempt {attempt + 1}): {exc}. Retrying in {wait}s…")
            await asyncio.sleep(wait)
    raise RuntimeError("Script agent: max retries exceeded")


async def generate(episode_config: dict[str, Any]) -> dict[str, Any]:
    """Generate a structured episode script using Claude.

    Args:
        episode_config: Dict with keys episode_number, title, concept, characters,
                        winner, drama, cliffhanger, length_seconds.

    Returns:
        Parsed script dict. Also saves JSON to output/scripts/episode_{N}.json.
    """
    ep_num = episode_config["episode_number"]
    output_path = _OUTPUT_DIR / f"episode_{ep_num}.json"

    # Resumable — skip generation if file already exists
    if output_path.exists():
        print(f"[script_agent] Found existing script at {output_path}, loading.")
        with open(output_path) as f:
            return json.load(f)

    with StageTimer("Script agent"):
        characters_data = json.loads(_CHARACTERS_PATH.read_text())["characters"]
        client = anthropic.Anthropic()
        user_message = _build_user_message(episode_config, characters_data)
        response = await _call_with_retry(client, user_message)

        raw = response.content[0].text.strip()
        script = json.loads(raw)

        output_path.write_text(json.dumps(script, indent=2))
        print(f"[script_agent] Script saved to {output_path}")

    return script


# Standalone test entry point
if __name__ == "__main__":
    import sys

    sample_config: dict[str, Any] = {
        "episode_number": 1,
        "title": "The Hallucination Games",
        "concept": "hallucination",
        "characters": ["claudia", "geppetto"],
        "winner": "claudia — because she admits uncertainty instead of guessing",
        "drama": "Geppetto confidently gives a wrong answer. The crowd loves it anyway.",
        "cliffhanger": "A new challenger appears with a mysterious USB drive.",
        "length_seconds": 75,
    }

    async def _main() -> None:
        result = await generate(sample_config)
        print(json.dumps(result, indent=2))

    asyncio.run(_main())
    sys.exit(0)
