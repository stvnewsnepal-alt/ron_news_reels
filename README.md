# Routine of Nepal Band: auto reels

Newest page post (photo + message) -> Gemini script (Nepali) -> edge-tts voice -> Pixabay clip +
animated layers (ffmpeg) -> 1080x1350 (4:5) video posted to the page. Runs every 2 hours, 7 AM to 7 PM Nepal time.

## Setup
1. Push this folder to a GitHub repo (public repo = free Actions minutes).
2. Repo -> Settings -> Secrets and variables -> Actions, add:
   - `FB_PAGE_ID`, `FB_PAGE_TOKEN` (long-lived Page token; needs pages_read_engagement, pages_manage_posts)
   - `GEMINI_API_KEY` (Google AI Studio, free tier)
   - `PIXABAY_API_KEY` (free)
3. Actions -> reel -> Run workflow with `dry_run = 1` first. Download the `last-reel` artifact and check it.
4. Run with `dry_run = 0` once, then let the schedule take over.

## Customize
- `assets/overlay.png`: optional 1080x1920 transparent PNG (logo/frame exported from Canva) drawn on top.
- `TTS_VOICE`: ne-NP-SagarNeural (male) or ne-NP-HemkalaNeural (female).
- `GEMINI_MODEL`, `FB_API_VERSION`: update if Google/Meta retire the defaults.
- Stock clips: check Pixabay's license before use. Never put other media's photos/videos in the reel.

## Size and upload mode (1080x1350)
Facebook Reels need 9:16, so a 4:5 video is posted as a normal page video (`UPLOAD_MODE=video`, default).
For 9:16 Reels set `UPLOAD_MODE=reel` and change `H` to 1920 in render.py.

## Headline font
Workflow auto-downloads Yatra One (Google Fonts, OFL license) as the headline font. To use another Unicode font (Kalimati, Mukta ExtraBold...), commit it as `assets/headline_font.ttf`; the download is then skipped.

Put a Unicode Nepali .ttf at `assets/headline_font.ttf` (or set `HEADLINE_FONT`). Used for headline + key lines.
Legacy fonts (Preeti-style, e.g. Ganess) will NOT work with Unicode text: they need a text converter first.
