# AGENTS.md

## Cursor Cloud specific instructions

This repo is a single FastAPI service (`main.py`) that acts as a LINE Messaging API
webhook bot. When a user sends an Instagram Reel/Post or YouTube Shorts URL, it
downloads the video (`yt-dlp`), extracts audio (`ffmpeg`), and transcribes it with
OpenAI, then replies via the LINE API.

### Running the service (dev)

- Python deps live in `.venv` (created by the update script). Run the dev server with hot reload:
  `.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload`
- Endpoints: `GET /health` (liveness) and `POST /callback` (LINE webhook; requires a valid
  `X-Line-Signature` HMAC-SHA256 header computed with `CHANNEL_SECRET`).

### Required environment variables (gotcha)

`main.py` reads these at **import time** and raises `RuntimeError` immediately if any are
missing, so the process will not even start without them (also true for `import main` in
tests/REPL). Set them in the shell or a local `.env` (loaded via `python-dotenv`):

- `CHANNEL_SECRET` — LINE channel secret (used for real HMAC signature verification)
- `CHANNEL_ACCESS_TOKEN` — LINE channel access token (used for outbound LINE API calls)
- `OPENAI_API_KEY` — OpenAI key (used for audio transcription)

For local boot/plumbing tests you can use placeholder values; the app starts and signature
verification works with any `CHANNEL_SECRET`. However, sending a reply hits the **real** LINE
API and transcription hits the **real** OpenAI API, so full end-to-end message handling needs
genuine credentials.

### System dependencies (not installed by the update script)

`ffmpeg` (audio extraction) and `python3-venv` (to build `.venv`) are system packages expected
to already be present on the VM image. If missing, install with
`sudo apt-get install -y ffmpeg python3-venv`.

### Lint / test

There is no configured linter or test suite in this repo. Verify changes by booting the app and
exercising `/health` and `/callback` (a valid signed webhook can be crafted with `CHANNEL_SECRET`).
