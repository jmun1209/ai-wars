# How AI Wars Works — Full Pipeline Guide

This doc explains every part of the system in plain English: what each piece does, why it exists, and how they connect.

---

## The Big Picture

You run one command. A team of AI agents writes, voices, animates, and edits a TikTok video about AI — then uploads it as a draft to your account. The only thing you do is pick which episode to produce and hit Publish inside TikTok.

```
python app.py
     │
     ▼
[Season Planning] ──── runs once, saved forever
     │   Showrunner Agent  →  designs the season arc, character development, educational goals
     │   Season Agent      →  breaks that into 20 episode plans with concepts + drama
     │
     ▼
[You pick an episode number]
     │
     ▼
[Episode Production] ──── runs per episode
     │
     ├── Script Agent      →  writes the full episode script (scenes, dialogue, educational notes)
     ├── Voice Gen         →  turns every line of dialogue into an MP3 via ElevenLabs
     ├── Video Gen         →  generates an animated clip per scene via Kling AI
     ├── Visual Gen        →  generates title card + concept diagram + takeaway card via Flux
     ├── Assembler         →  stitches everything together with FFmpeg + adds captions via Whisper
     ├── Caption Agent     →  writes the TikTok hook, body, CTA, and hashtags
     └── TikTok Upload     →  uploads the final MP4 as a private draft to your TikTok account
```

---

## Step 1 — Season Planning (runs once)

### Showrunner Agent (`agents/showrunner_agent.py`)
**Model:** Claude Opus
**What it does:** Acts like an executive TV producer. Given the season number, it generates:
- A tagline and premise for the whole season
- The educational mission (what viewers will understand by episode 20)
- A narrative arc (how the story escalates)
- Individual character arcs for Claudia, Geppetto, Gemma, and Lama
- The educational progression across 4 blocks of 5 episodes
- Season finale stakes

**Saved to:** `output/seasons/season_1_vision.json`

### Season Agent (`agents/season_agent.py`)
**Model:** Claude Opus
**What it does:** Takes the showrunner's vision and acts like a writers room. Plans all 20 episodes ensuring:
- Concepts build on each other (you learn what embeddings are before RAG is introduced)
- All 4 characters appear regularly
- Drama in each episode teaches something real about how AI works
- The finale is the highest-stakes, most educational episode

**Saved to:** `output/seasons/season_1_plan.json`

> Both files are saved permanently. Re-running `app.py` loads them instead of regenerating — the season is planned once.

---

## Step 2 — Script Generation

### Script Agent (`agents/script_agent.py`)
**Model:** Claude Opus
**What it does:** Takes the episode config (title, concept, characters, drama, cliffhanger) and writes a complete episode script as structured JSON including:
- A hook for the first 3 seconds
- Scene-by-scene shot descriptions
- Dialogue for each character (max 15 words per line)
- Educational notes per scene explaining what the viewer is learning
- Visual overlay text (captions that appear on screen)
- An outro with the winner, takeaway line, and cliffhanger

**Saved to:** `output/scripts/episode_1.json`
**Resumable:** If this file already exists, the script is loaded from disk — no API call.

---

## Step 3 — Voiceovers

### Voice Gen (`generators/voice_gen.py`)
**API:** ElevenLabs
**What it does:** Loops through every dialogue line in the script and calls ElevenLabs to synthesize speech. Each character has their own voice ID (set in `config/characters.json`). The narrator also gets a separate neutral voice for the outro.

- Scenes run **in parallel** (faster)
- Lines within a scene run **sequentially** (preserves natural pacing)

**Saved to:** `output/audio/ep1_scene1_line0.mp3`, etc.
**Resumable:** Skips any file that already exists.

---

## Step 4 — Video Clips

### Video Gen (`generators/video_gen.py`)
**API:** Kling AI
**What it does:** For each scene, sends a text-to-video request to Kling AI with the shot description and character visual styles. Polls every 5 seconds until the clip is ready, then downloads it.

**Fallback:** If Kling fails or takes more than 5 minutes, it auto-generates a colored static image (character's brand color + name) and converts it to a 5-second video using FFmpeg. The pipeline never stops for a failed clip.

**Saved to:** `output/clips/ep1_scene1.mp4`

---

## Step 5 — Educational Visuals

### Visual Gen (`generators/visual_gen.py`)
**API:** Replicate (Flux Schnell model)
**What it does:** Generates 3 images per episode, all at 1080×1920 (TikTok vertical format):

| Image | Purpose |
|-------|---------|
| Title card | Episode number + title + hook text. Shown for 2 seconds at the start. |
| Concept diagram | A clean visual metaphor for the AI concept being taught. No text. |
| Takeaway card | The educational takeaway in bold text. Shown for 3 seconds at the end. |

**Saved to:** `output/images/ep1_title.png`, `ep1_concept.png`, `ep1_takeaway.png`

---

## Step 6 — Assembly

### Assembler (`editor/assemble.py`)
**Tools:** FFmpeg + OpenAI Whisper
**What it does:** Builds the final video in 4 steps:

**Step 1 — Per-scene assembly**
Each scene gets its audio lines layered onto its video clip sequentially (with 0.2s gaps between lines). If the scene has a visual overlay, it's burned onto the video as text.

**Step 2 — Full video assembly**
- Title card image → held for 2 seconds
- All assembled scenes in order
- Takeaway card image → held for 3 seconds
- Background music from `assets/background_music.mp3` ducked to 15% volume under dialogue

**Step 3 — Auto-captions**
Whisper (`base` model) transcribes the full video and generates an SRT subtitle file. Captions are burned into the video (white text, black outline, bottom-center).

**Step 4 — Final export**
H.264 video, AAC audio, 1080×1920, optimized to stay under TikTok's 50MB limit.

**Saved to:** `output/final/ep1_final.mp4`

---

## Step 7 — TikTok Caption

### Caption Agent (`agents/caption_agent.py`)
**Model:** Claude Haiku (fast + cheap)
**What it does:** Writes TikTok-optimized copy using this formula:
- **Line 1:** Hook — bold claim or surprising fact (max 10 words)
- **Line 2:** What they'll learn (max 15 words)
- **Line 3:** Call to action (max 8 words)
- **Hashtags:** 8-12 mixing broad (#AI #Tech) and niche (#LLMs #AIEngineering)

---

## Step 8 — TikTok Upload

### TikTok Upload (`publisher/tiktok_upload.py`)
**API:** TikTok Content Posting API v2
**What it does:**
1. Fetches your creator info to verify the connection
2. Initializes an upload session and gets a pre-signed upload URL
3. PUTs the raw video bytes to that URL
4. Polls until TikTok confirms the video is in your drafts inbox
5. Returns the caption text for you to paste when you publish

The video lands in your **TikTok Drafts** — only you can see it until you hit Publish inside the app.

---

## Characters

| Name | Based On | Personality | Wins By |
|------|----------|-------------|---------|
| Claudia | Claude (Anthropic) | Thoughtful, ethical, over-explains | Reasoning and safety |
| Geppetto | GPT-4 (OpenAI) | Overconfident, fast, hallucinates | Charisma and speed |
| Gemma | Gemini (Google) | Multimodal show-off, competitive | Bringing in images/video/audio |
| Lama | Llama (Meta) | Scrappy open-source underdog | Community, resourcefulness |

Every dramatic moment has an educational reason. When Geppetto hallucinates, the episode explains what hallucination is. When Claudia over-explains, it shows why safety reasoning matters.

---

## File Structure

```
ai-wars-pipeline/
├── app.py                    ← Run this
├── config/
│   └── characters.json       ← Character definitions + ElevenLabs voice IDs
├── agents/
│   ├── showrunner_agent.py   ← Season vision (Claude Opus)
│   ├── season_agent.py       ← 20-episode plan (Claude Opus)
│   ├── script_agent.py       ← Episode script (Claude Opus)
│   └── caption_agent.py      ← TikTok copy (Claude Haiku)
├── generators/
│   ├── voice_gen.py          ← ElevenLabs MP3s
│   ├── video_gen.py          ← Kling AI clips
│   └── visual_gen.py         ← Replicate/Flux images
├── editor/
│   └── assemble.py           ← FFmpeg + Whisper final video
├── publisher/
│   └── tiktok_upload.py      ← TikTok Content Posting API
├── utils/
│   └── logger.py             ← API call logs + cost tracking + summary table
├── output/                   ← Everything generated lands here (gitignored)
│   ├── seasons/              ← season_1_vision.json, season_1_plan.json
│   ├── scripts/              ← episode_1.json, episode_2.json …
│   ├── audio/                ← MP3s per dialogue line
│   ├── clips/                ← MP4s per scene
│   ├── images/               ← PNG title/concept/takeaway cards
│   ├── final/                ← The finished episode videos
│   └── logs/                 ← Pipeline run logs with API costs
├── docs/
│   ├── terms.html            ← TikTok developer portal: Terms of Service URL
│   ├── privacy.html          ← TikTok developer portal: Privacy Policy URL
│   └── callback.html         ← TikTok OAuth redirect landing page
├── setup_tiktok_auth.py      ← One-time OAuth flow to get TikTok tokens
├── .env                      ← Your API keys (never committed)
└── .env.example              ← Template showing which keys are needed
```

---

## Resumability

Every expensive operation checks if its output already exists before running:

| Stage | Skipped if this file exists |
|-------|-----------------------------|
| Season vision | `output/seasons/season_1_vision.json` |
| Season plan | `output/seasons/season_1_plan.json` |
| Script | `output/scripts/episode_N.json` |
| Voice line | `output/audio/epN_sceneS_lineL.mp3` |
| Video clip | `output/clips/epN_sceneS.mp4` |
| Visual image | `output/images/epN_title.png` etc. |

If the pipeline crashes mid-run, re-run it and it picks up where it left off.

---

## Useful Commands

```bash
# Normal run — shows season plan, you pick an episode
python app.py

# Just generate/view the season plan without producing video
python app.py --plan-only

# Skip the menu and go straight to episode 5
python app.py --episode 5

# Start a new season
python app.py --season 2

# Test the full pipeline without spending any API credits
python app.py --dry-run --episode 1

# Produce an episode but don't upload to TikTok
python app.py --episode 3 --skip-upload
```

---

## API Keys You Need

| Service | What it's used for | Where to get it |
|---------|-------------------|-----------------|
| Anthropic | Scripts, season planning, captions | console.anthropic.com |
| ElevenLabs | Character voices (4 voices + narrator) | elevenlabs.io |
| Kling AI | Animated video clips per scene | klingai.com |
| Replicate | Educational images (Flux Schnell) | replicate.com |
| TikTok | Upload videos as drafts | developers.tiktok.com |

---

## Cost Estimates (per episode)

| Stage | Service | Rough cost |
|-------|---------|------------|
| Script (Claude Opus) | Anthropic | ~$0.10 |
| Caption (Claude Haiku) | Anthropic | ~$0.001 |
| Voiceovers (10-15 lines) | ElevenLabs | ~$0.05 |
| Video clips (4-6 scenes) | Kling AI | ~$0.50–$2.00 |
| Images (3 images) | Replicate | ~$0.01 |
| **Total per episode** | | **~$0.70–$2.20** |

Season of 20 episodes: roughly **$15–$45 in API costs**.
