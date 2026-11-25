HAMRO CHITWAN NEWS REELS

Files:
- main.py
- render.py
- .github/workflows/reel.yml
- requirements.txt

IMPORTANT SETUP:
1. Upload the four files preserving the folder structure.
2. Upload your Hamro Chitwan logo image to the repository ROOT with this exact name:
   file_000000008d8c8208b201454c7871862d.png
3. Add GitHub repository secrets:
   FB_PAGE_ID
   FB_PAGE_TOKEN
   GEMINI_API_KEY
   PIXABAY_API_KEY (optional; without it the article photo is reused)
4. Manual run defaults to DRY RUN=1. Check the generated artifact first. Select 0 to publish manually.
5. Scheduled runs publish automatically at 7 AM, 9 AM, 11 AM, 1 PM, 3 PM, 5 PM and 7 PM Nepal time.

Gemini TTS:
The code defaults to model gemini-2.5-flash-preview-tts and voice Cruz. If your Google AI Studio account exposes a different TTS model name, set GEMINI_TTS_MODEL as a repository variable. The narration script model defaults to gemini-2.5-flash; override with GEMINI_TEXT_MODEL if needed.

Freshness:
Stories without a machine-readable publication timestamp are rejected. Only a verified timestamp within the last 12 hours is accepted. A run stops if the latest eligible story has already been posted.

NOTE:
The renderer adds a subtle locally synthesized ambient backing pad (not a downloaded copyrighted song). No headline, subtitles, source label, or other text is drawn over the video. The Facebook post caption still contains the story headline and hashtags.
