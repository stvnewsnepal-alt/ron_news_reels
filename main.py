import os, json, logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from news_fetcher import fetch_latest
from news_filter import choose_story, mark_published
from image_fetcher import collect_visuals
from voice_generator import make_voice
from video_builder import build_video
from facebook_publish import publish_reel

TZ=ZoneInfo("Asia/Kathmandu")
OUT=Path("output")
OUT.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def main():
    now=datetime.now(TZ)
    logging.info("HAMRO CHITWAN NEWS VIDEO | %s", now.isoformat())
    stories=fetch_latest()
    story=choose_story(stories)
    if not story:
        raise SystemExit("No eligible fresh story found; nothing posted.")
    logging.info("Selected: %s | %s", story["title"], story["url"])
    visuals=collect_visuals(story)
    if not visuals:
        raise RuntimeError("No licensed/usable visual found; refusing to create unrelated video.")
    audio=make_voice(story)
    video=build_video(story, visuals, audio, OUT/"hamro_chitwan_news.mp4")
    if os.getenv("DRY_RUN","0")=="1":
        logging.info("DRY_RUN=1; video built but not published: %s",video)
        return
    publish_reel(video, story)
    mark_published(story)
    logging.info("Published successfully.")

if __name__=="__main__":
    main()
