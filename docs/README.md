# AI Wars — Episode Generator

Automated pipeline that takes a terminal intake form and produces a fully-assembled TikTok-ready episode video, then uploads it as a draft.

---

## What it builds

```
Intake form → Script (Claude) → Voiceovers (ElevenLabs)
                              → Video clips (Kling AI)
                              → Educational images (Flux/Replicate)
                              → Assembly + Captions (FFmpeg + Whisper)
                              → TikTok Caption (Claude Haiku)
                              → Upload to TikTok Drafts
```

The only human steps are filling out the intake form and hitting **Publish** inside TikTok.

---

## Prerequisites

- Python 3.10+
- FFmpeg installed on your system:
  - Mac: `brew install ffmpeg`
  - Linux: `apt install ffmpeg`
  - Windows: download from [ffmpeg.org](https://ffmpeg.org) and add to PATH
- Accounts and API keys (see below)

---

## Setup

```bash
# 1. Clone / download the project
cd ai-wars-pipeline

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Copy the environment template and fill in your keys
cp .env.example .env
# Open .env in your editor and add all API keys

# 4. (One-time) Complete TikTok OAuth flow
python setup_tiktok_auth.py
```

---

## How to get each API key

### Anthropic (Claude)
1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an account and add billing
3. Generate an API key under **API Keys**
4. Set `ANTHROPIC_API_KEY` in `.env`

### ElevenLabs (voice)
1. Go to [elevenlabs.io](https://elevenlabs.io) and create an account
2. In **Voice Lab**, clone or create 4 character voices (Claudia, Geppetto, Gemma, Lama)
3. Copy each voice's **Voice ID** from its settings page
4. Update `voice_id` fields in `config/characters.json`
5. Create a 5th neutral voice for the narrator
6. Set `ELEVENLABS_API_KEY` and `ELEVENLABS_NARRATOR_VOICE_ID` in `.env`

### Kling AI (video generation)
1. Go to [klingai.com](https://klingai.com) and sign up for API access
2. Generate an API key from your developer dashboard
3. Set `KLING_API_KEY` in `.env`

### Replicate (image generation)
1. Go to [replicate.com](https://replicate.com) and create an account
2. Navigate to **Account → API Tokens**
3. Set `REPLICATE_API_TOKEN` in `.env`

### TikTok Content Posting API
1. Go to [developers.tiktok.com](https://developers.tiktok.com)
2. Create a developer account and a new App
3. Under **Products**, add **Content Posting API**
4. Enable the `video.upload` scope
5. Set `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` in `.env`
6. Run `python setup_tiktok_auth.py` to complete the OAuth flow — this writes
   `TIKTOK_ACCESS_TOKEN` and `TIKTOK_OPEN_ID` to `.env` automatically
7. Apply for TikTok app audit at [developers.tiktok.com/audit](https://developers.tiktok.com/audit) — until approved, all uploads will be private drafts

---

## Running the pipeline

```bash
python app.py
```

Follow the prompts:
- Episode number and title
- AI concept to teach (e.g. "RAG", "hallucination", "attention mechanisms")
- Which characters appear (multi-select from the numbered list)
- Who wins and why
- The drama/conflict
- The cliffhanger ending
- Desired video length in seconds (default: 75)

The pipeline runs and shows progress for each stage. At the end it prints the final video path and the TikTok caption ready to paste.

---

## CLI flags

```bash
# Skip all API calls — test assembly logic cheaply with placeholder files
python app.py --dry-run

# Run everything except the TikTok upload
python app.py --skip-upload
```

---

## Resumable pipeline

If a stage's output already exists (e.g. `output/scripts/episode_1.json`), that stage is skipped and the file is loaded from disk. This means you can re-run after a failure without regenerating completed stages.

---

## Background music

Place a royalty-free MP3 at `assets/background_music.mp3`. The assembler ducks it to 15% volume under dialogue. If the file is missing, the video is assembled without background music.

---

## Output files

```
output/
├── scripts/    episode_N.json
├── audio/      ep{N}_scene{S}_line{L}.mp3
├── clips/      ep{N}_scene{S}.mp4
├── images/     ep{N}_title.png, ep{N}_concept.png, ep{N}_takeaway.png
├── final/      ep{N}_draft.mp4, ep{N}.srt, ep{N}_final.mp4
└── logs/       pipeline_{timestamp}.log
```

---

## Characters

| Name | Inspiration | Personality |
|------|-------------|-------------|
| Claudia | Claude (Anthropic) | Thoughtful, careful, wins by reasoning |
| Geppetto | GPT-4 (OpenAI) | Overconfident, fast, occasionally hallucinates |
| Gemma | Gemini (Google) | Multimodal show-off, competitive |
| Lama | Llama (Meta) | Scrappy open-source underdog |

Edit `config/characters.json` to customize personalities, voice IDs, and visual styles.
