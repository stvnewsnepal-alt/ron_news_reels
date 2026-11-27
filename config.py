import os
PAGE_ID=os.environ.get("FB_PAGE_ID","")
PAGE_TOKEN=os.environ.get("FB_PAGE_ACCESS_TOKEN","")
PIXABAY_API_KEY=os.environ.get("PIXABAY_API_KEY","")
# Set an exact, legally reusable presenter cutout in assets/presenter.png.
PRESENTER_PATH="assets/presenter.png"
LOGO_PATH="assets/logo.png"
MUSIC_PATH="assets/music.mp3"
FONT_PATH="assets/fonts/NotoSansDevanagari-Bold.ttf"
FONT_REGULAR_PATH="assets/fonts/NotoSansDevanagari-Regular.ttf"
VIDEO_W, VIDEO_H = 1080, 1350
FPS=30
DURATION=81
MAX_STORY_AGE_HOURS=18
GRAPH_API_VERSION=os.getenv("GRAPH_API_VERSION","v27.0")
