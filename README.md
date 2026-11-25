# Hamro Chitwan News Reels — replacement files

Copy these files into the repository:
- `main.py` → repository root (replace old file)
- `requirements.txt` → repository root (replace old file)
- `.github/workflows/reels.yml` → same path (replace old workflow)

Keep your existing `render.py` unchanged. This `main.py` calls the render interface previously shared:
`render(photo, audio, headline, points, out, bg_video=None, brand=..., work=...)`.

Required GitHub repository secrets:
- `FB_PAGE_ID`
- `FB_PAGE_TOKEN`
- `GEMINI_API_KEY`
- `PIXABAY_API_KEY` (optional; if absent, only article photo is used)

The workflow runs at Nepal time 07:00–19:00 every two hours.
Manual run supports `dry_run=1` to build without Facebook publishing.

Date rule:
- Exact publication timestamps must be within `MAX_AGE_HOURS` (default 12).
- If there is no exact timestamp, a visible Nepali Bikram Sambat date matching today's Nepali date is accepted as date-only evidence. It does not claim an exact publication time.
- Older, future, or unverified date-only stories are rejected.

Important: a date-only match is based on the article page's visible date text. Check the log for
`Accepted: today's Nepali publication date verified` to confirm the fallback was used.
