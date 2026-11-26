"""
Nepal short-video bot (9:16, ~30-40 sec)
Gemini (script) -> Pixabay (images) -> edge-tts (Nepali voice) -> MoviePy (video) -> Facebook Page

Install:
    pip install moviepy==1.0.3 edge-tts requests pillow numpy
    + ffmpeg installed
    + font.ttf  (NotoSansDevanagari-Bold.ttf, rename to font.ttf, same folder)

.env / GitHub Secrets:
    GEMINI_API_KEY, PIXABAY_API_KEY, FB_PAGE_ID, FB_PAGE_TOKEN

Run:
    python nepal_video_bot.py            # next topic (rotates daily)
    python nepal_video_bot.py 2          # topic index 0-5
"""
import os, sys, json, re, asyncio, datetime, textwrap, requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import edge_tts
from moviepy.editor import (ImageClip, AudioFileClip, CompositeVideoClip,
                            concatenate_videoclips)

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PIXABAY_KEY = os.environ["PIXABAY_API_KEY"]
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_TOKEN = os.environ.get("FB_PAGE_TOKEN")

W, H = 1080, 1920
VOICE = "ne-NP-SagarNeural"          # or ne-NP-HemkalaNeural
LOGO = "file_000000008d8c8208b201454c7871862d.png"
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
FONT = next((f for f in ["font.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"] if os.path.exists(f)), "font.ttf")
OUT = "out"
os.makedirs(OUT, exist_ok=True)

TOPICS = [
    "नेपालको एकीकरण: पृथ्वीनारायण शाहले कसरी नेपाल बनाए",
    "नेपालको अर्थतन्त्र: वृद्धि, रेमिट्यान्स र पर्यटनको भूमिका",
    "जलवायु परिवर्तन: नेपालको हिमाल र किसानमा असर",
    "दैनिक जीवनका उपयोगी टिप्स: पैसा बचत गर्ने ५ तरिका",
    "नेपालका रोचक तथ्यहरू जुन धेरैलाई थाहा छैन",
    "विद्यार्थीका लागि अध्ययन र समय व्यवस्थापन टिप्स",
]


# ---------- 1. Gemini: script ----------
def make_script(topic):
    prompt = f"""तिमी एक सोसियल मिडिया भिडियो स्क्रिप्ट राइटर हौ।
विषय: {topic}
३०-४० सेकेन्डको ठाडो (9:16) भिडियोका लागि ६ वटा दृश्य बनाऊ।
पहिलो दृश्य आकर्षक हुक हुनुपर्छ। तथ्य सही र सरल हुनुपर्छ, नदेखिने कुरा नबनाऊ।
केवल JSON फर्काऊ (अरू केही होइन):
{{"title": "...", "hashtags": "#nepal #...",
 "scenes": [{{"narration": "नेपाली वाक्य (१-२ वटा, छोटो)",
              "caption": "४-६ शब्दको स्क्रिन टेक्स्ट",
              "keywords": "english pixabay search words (2-3 words)"}}]}}"""
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash:generateContent?key={GEMINI_KEY}")
    r = requests.post(url, json={
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}}, timeout=90)
    r.raise_for_status()
    txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(re.sub(r"^```json|```$", "", txt.strip()))


# ---------- 2. Pixabay: image ----------
def get_image(keywords, path):
    r = requests.get("https://pixabay.com/api/", params={
        "key": PIXABAY_KEY, "q": keywords, "image_type": "photo",
        "orientation": "vertical", "min_height": 1200,
        "safesearch": "true", "per_page": 5}, timeout=30).json()
    if not r.get("hits"):
        r = requests.get("https://pixabay.com/api/", params={
            "key": PIXABAY_KEY, "q": "nepal", "image_type": "photo",
            "per_page": 5}, timeout=30).json()
    url = r["hits"][0]["largeImageURL"]
    open(path, "wb").write(requests.get(url, timeout=60).content)
    return path


def fit_cover(path):
    im = Image.open(path).convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * s) + 2, int(im.height * s) + 2))
    l, t = (im.width - W) // 2, (im.height - H) // 2
    return np.array(im.crop((l, t, l + W, t + H)))


# ---------- 3. Voice ----------
async def _tts(text, path):
    await edge_tts.Communicate(text, VOICE, rate="+5%").save(path)

def make_voice(text, path):
    asyncio.run(_tts(text, path))
    return path


# ---------- 4. Caption overlay ----------
def caption_clip(text, dur):
    font = ImageFont.truetype(FONT, 78)
    lines = textwrap.wrap(text, 18)
    img = Image.new("RGBA", (W, 460), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 20, W - 60, 440), 40, fill=(0, 0, 0, 150))
    y = 60
    for ln in lines[:4]:
        w = d.textlength(ln, font=font)
        d.text(((W - w) / 2, y), ln, font=font, fill="white",
               stroke_width=3, stroke_fill="black")
        y += 95
    a = np.array(img)
    clip = ImageClip(a[:, :, :3]).set_duration(dur)
    mask = ImageClip(a[:, :, 3] / 255.0, ismask=True).set_duration(dur)
    return clip.set_mask(mask).set_position(("center", 1250))


# ---------- 5. Build video ----------
def build_video(script, name):
    clips = []
    for i, sc in enumerate(script["scenes"]):
        img = get_image(sc["keywords"], f"{OUT}/{name}_{i}.jpg")
        aud = AudioFileClip(make_voice(sc["narration"], f"{OUT}/{name}_{i}.mp3"))
        dur = aud.duration + 0.3
        bg = (ImageClip(fit_cover(img)).set_duration(dur)
              .resize(lambda t, d=dur: 1 + 0.10 * t / d)      # slow zoom
              .set_position("center"))
        layers = [bg, caption_clip(sc["caption"], dur)]
        if os.path.exists(LOGO):
            layers.append(ImageClip(LOGO).resize(width=220).set_duration(dur)
                          .set_position((40, 80)))
        scene = CompositeVideoClip(layers, size=(W, H)).set_audio(aud).set_duration(dur)
        clips.append(scene.crossfadein(0.25) if i else scene)
    final = concatenate_videoclips(clips, method="compose", padding=-0.25)
    path = f"{OUT}/{name}.mp4"
    final.write_videofile(path, fps=30, codec="libx264", audio_codec="aac",
                          threads=4, preset="medium", logger=None)
    return path


# ---------- 6. Facebook upload ----------
def post_facebook(video_path, title, hashtags):
    r = requests.post(
        f"https://graph-video.facebook.com/v21.0/{FB_PAGE_ID}/videos",
        data={"access_token": FB_TOKEN, "title": title,
              "description": f"{title}\n\n{hashtags}"},
        files={"source": open(video_path, "rb")}, timeout=600)
    print("Facebook:", r.status_code, r.text)
    r.raise_for_status()


if __name__ == "__main__":
    posted = json.load(open("posted_topics.json")) if os.path.exists("posted_topics.json") else []
    if len(sys.argv) > 1:
        idx = int(sys.argv[1])
    else:  # next topic not used most recently (rotates through all 6)
        idx = (posted[-1] + 1) % len(TOPICS) if posted else 0
    topic = TOPICS[idx]
    print("Topic:", topic)
    script = make_script(topic)
    json.dump(script, open(f"{OUT}/script_{idx}.json", "w"), ensure_ascii=False, indent=2)
    video = build_video(script, f"video_{idx}")
    print("Video:", video)
    if DRY_RUN:
        print("DRY RUN: not posting")
    elif FB_PAGE_ID and FB_TOKEN:
        post_facebook(video, script["title"], script.get("hashtags", ""))
        json.dump((posted + [idx])[-30:], open("posted_topics.json", "w"))
