"""
NEPAL ANIMATED TIPS REELS - 4 reels/day, auto upload
Gemini (fresh subject + 30s script) -> Pixabay (images) -> edge-tts (Nepali voice)
-> MoviePy (animated 9:16 video) -> Facebook Reel

Secrets: GEMINI_API_KEY, PIXABAY_API_KEY, FB_PAGE_ID, FB_PAGE_TOKEN
Env: SLOT = morning7 | morning9 | evening5 | night9   (auto-detected if empty)
     DRY_RUN = 1  -> video only, no upload
"""
import os, sys, json, re, asyncio, datetime, textwrap, time, requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import edge_tts
from moviepy.editor import (ImageClip, AudioFileClip, CompositeVideoClip,
                            ColorClip, concatenate_videoclips)

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PIXABAY_KEY = os.environ["PIXABAY_API_KEY"]
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_TOKEN = os.environ.get("FB_PAGE_TOKEN")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
GEMINI_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")

W, H = 1080, 1920
VOICE = "ne-NP-SagarNeural"                       # or ne-NP-HemkalaNeural
LOGO = "file_000000008d8c8208b201454c7871862d.png"
FONT = next((f for f in ["font.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"]
            if os.path.exists(f)), "font.ttf")
OUT, HISTORY = "out", "posted_topics.json"
os.makedirs(OUT, exist_ok=True)

# Each daily slot has its own category, so the 4 reels are always different.
SLOTS = {
    "morning7": "नेपालको इतिहास, एकीकरण, महान व्यक्तित्व, संस्कृति र रोचक तथ्य",
    "morning9": "नेपालको अर्थतन्त्र, रेमिट्यान्स, पर्यटन, कृषि, पैसा बचत र आम्दानीका उपयोगी टिप्स",
    "evening5": "मौसम, जलवायु परिवर्तन, हिमाल, वातावरण, विपद् सुरक्षा र स्वास्थ्य टिप्स",
    "night9":   "दैनिक जीवनका उपयोगी टिप्स: अध्ययन, समय व्यवस्थापन, करियर, मोबाइल/इन्टरनेट सुरक्षा, बानी",
}
SLOT_HOURS = {7: "morning7", 9: "morning9", 17: "evening5", 21: "night9"}


def nepal_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=45)


def detect_slot():
    s = os.environ.get("SLOT", "").strip()
    if s in SLOTS:
        return s
    h = nepal_now().hour
    return SLOT_HOURS[min(SLOT_HOURS, key=lambda x: abs(x - h))]


def load_history():
    try:
        return json.load(open(HISTORY))
    except Exception:
        return []


# ---------------- 1. Gemini: fresh subject + script ----------------
def make_script(slot, history):
    used = [h["title"] for h in history[-60:]]
    prompt = f"""तिमी नेपाली सोसियल मिडिया भिडियो स्क्रिप्ट राइटर हौ।
श्रेणी: {SLOTS[slot]}
आजको मिति: {nepal_now():%Y-%m-%d}
यो श्रेणीभित्र एउटा नयाँ, उपयोगी र रोचक विषय छान। यी विषय फेरि नदोहोर्याऊ: {used}
३० सेकेन्डको ठाडो भिडियोका लागि ठ्याक्कै ६ वटा दृश्य बनाऊ।
नियम: पहिलो दृश्य आकर्षक हुक। तथ्य सही हुनुपर्छ; पक्का नभएको मिति/संख्या नलेख।
प्रत्येक narration १०-१४ शब्दको सरल नेपाली (देवनागरी)।
केवल JSON फर्काऊ:
{{"title":"भिडियोको शीर्षक","hashtags":"#nepal #nepali ...",
 "scenes":[{{"narration":"...","caption":"४-६ शब्द स्क्रिनमा",
   "big":"१-३ शब्द वा संख्या (जस्तै: १७६८, टिप १, ५०%)",
   "badge_label":"२-३ शब्दको लेबल",
   "bg_keywords":"english pixabay search 2-3 words",
   "badge_keywords":"english pixabay search 1-2 words"}}]}}"""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}")
    for attempt in range(3):
        try:
            r = requests.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}},
                timeout=120)
            r.raise_for_status()
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(re.sub(r"^```json|```$", "", txt.strip()))
            assert len(data["scenes"]) >= 4
            return data
        except Exception as e:
            print("Gemini retry:", e)
            time.sleep(5)
    raise RuntimeError("Gemini failed")


# ---------------- 2. Pixabay ----------------
def get_image(keywords, path, orientation="vertical"):
    def q(k, o):
        return requests.get("https://pixabay.com/api/", params={
            "key": PIXABAY_KEY, "q": k, "image_type": "photo",
            "orientation": o, "safesearch": "true", "per_page": 8},
            timeout=30).json().get("hits", [])
    hits = q(keywords, orientation) or q(keywords.split()[0], "all") or q("nepal", "all")
    hit = hits[int(time.time()) % len(hits)]
    data = requests.get(hit["largeImageURL"], timeout=60,
                        headers={"User-Agent": "Mozilla/5.0"}).content
    open(path, "wb").write(data)
    return path


def cover(path, w, h):
    im = Image.open(path).convert("RGB")
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s) + 2, int(im.height * s) + 2))
    l, t = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((l, t, l + w, t + h))


# ---------------- 3. Voice ----------------
async def _tts(text, path):
    await edge_tts.Communicate(text, VOICE, rate="+3%").save(path)


def make_voice(text, path):
    for _ in range(3):
        try:
            asyncio.run(_tts(text, path))
            return path
        except Exception as e:
            print("TTS retry:", e)
            time.sleep(3)
    raise RuntimeError("TTS failed")


# ---------------- 4. Graphic layers (PIL -> RGBA) ----------------
def font(sz):
    return ImageFont.truetype(FONT, sz)


def to_clip(rgba, dur):
    a = np.array(rgba)
    c = ImageClip(a[:, :, :3]).set_duration(dur)
    m = ImageClip(a[:, :, 3] / 255.0, ismask=True).set_duration(dur)
    return c.set_mask(m)


def shade_layer():
    """dark gradient top+bottom so text is readable"""
    g = np.zeros((H, W, 4), dtype=np.uint8)
    y = np.arange(H)[:, None]
    top = np.clip(1 - y / 700, 0, 1) * 170
    bot = np.clip((y - 900) / 1000, 0, 1) * 200
    g[:, :, 3] = np.broadcast_to((top + bot).astype(np.uint8), (H, W))
    return Image.fromarray(g, "RGBA")


def big_box(text):
    sz = 130
    while sz > 50 and ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(text, font=font(sz)) > W - 260:
        sz -= 6
    f = font(sz)
    tw = int(ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(text, font=f))
    bw, bh = tw + 100, sz + 80
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, bw - 1, bh - 1), 28, fill=(255, 255, 255, 245))
    d.text((50, 28), text, font=f, fill=(15, 15, 15, 255))
    return img


def badge(path, label):
    R = 215
    face = cover(path, R * 2, R * 2)
    img = Image.new("RGBA", (560, 560), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = 280, 235
    d.ellipse((cx - R - 12, cy - R - 12, cx + R + 12, cy + R + 12), fill=(255, 255, 255, 255))
    mask = Image.new("L", (R * 2, R * 2), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, R * 2 - 1, R * 2 - 1), fill=255)
    img.paste(face, (cx - R, cy - R), mask)
    f = font(46)
    tw = int(d.textlength(label, font=f))
    pw = min(tw + 60, 540)
    d.rounded_rectangle((280 - pw // 2, 470, 280 + pw // 2, 545), 22, fill=(20, 40, 30, 235),
                        outline=(255, 255, 255, 200), width=3)
    d.text((280 - tw // 2 if tw < 540 else 30, 478), label, font=f, fill="white")
    return img


def caption_box(text):
    f = font(76)
    lines = textwrap.wrap(text, 17)[:3]
    h = 60 + 100 * len(lines)
    img = Image.new("RGBA", (W - 80, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, W - 81, h - 1), 36, fill=(0, 0, 0, 165))
    y = 28
    for ln in lines:
        w = d.textlength(ln, font=f)
        d.text(((W - 80 - w) / 2, y), ln, font=f, fill="white",
               stroke_width=3, stroke_fill="black")
        y += 100
    return img


def ease_back(t, d):
    if t >= d:
        return 1.0
    x = t / d - 1
    s = 1.70158
    return max(0.02, 1 + (s + 1) * x ** 3 + s * x ** 2)


def ease_out(t, d):
    if t >= d:
        return 1.0
    x = 1 - t / d
    return 1 - x ** 3


def pop_clip(rgba, dur, cx, cy, delay=0.0, d=0.55):
    """pop-in (scale with overshoot) around centre (cx,cy)"""
    w, h = rgba.size
    clip = to_clip(rgba, dur)
    sc = lambda t: ease_back(max(0, t - delay), d)
    clip = clip.resize(sc)
    clip = clip.set_position(lambda t: (cx - w * sc(t) / 2, cy - h * sc(t) / 2))
    return clip.set_start(0)


def slide_clip(rgba, dur, x, y, delay=0.0, d=0.5, dy=120):
    """slide up + fade-in"""
    clip = to_clip(rgba, dur)
    pos = lambda t: (x, y + dy * (1 - ease_out(max(0, t - delay), d)))
    return clip.set_position(pos).crossfadein(d).set_start(delay) if delay else \
        clip.set_position(pos).crossfadein(d)


# ---------------- 5. Build video ----------------
def build_video(script, name):
    scenes = script["scenes"][:6]
    n = len(scenes)
    shade = shade_layer()
    logo = Image.open(LOGO).convert("RGBA") if os.path.exists(LOGO) else None
    if logo:
        logo.thumbnail((200, 200))
    clips = []
    for i, sc in enumerate(scenes):
        p = f"{OUT}/{name}_{i}"
        bg_path = get_image(sc["bg_keywords"], p + "_bg.jpg")
        bd_path = get_image(sc["badge_keywords"], p + "_badge.jpg", "all")
        aud = AudioFileClip(make_voice(sc["narration"], p + ".mp3"))
        dur = aud.duration + 0.35

        bg = (to_clip(cover(bg_path, W, H).convert("RGBA"), dur)
              .resize(lambda t, d=dur: 1 + 0.12 * t / d)
              .set_position("center"))
        layers = [bg, to_clip(shade, dur)]
        layers.append(pop_clip(big_box(sc["big"]), dur, W / 2, 330, delay=0.15))
        layers.append(pop_clip(badge(bd_path, sc["badge_label"]), dur, W / 2, 800, delay=0.45))
        cap = caption_box(sc["caption"])
        layers.append(slide_clip(cap, dur, 40, 1090, delay=0.8))
        # progress bar
        layers.append(ColorClip((W, 14), color=(255, 255, 255)).set_opacity(0.25)
                      .set_duration(dur).set_position((0, 0)))
        layers.append(ColorClip((int(W * (i + 1) / n), 14), color=(255, 200, 0))
                      .set_duration(dur).set_position((0, 0)))
        if logo:
            layers.append(to_clip(logo, dur).set_position((40, 60)))

        scene = (CompositeVideoClip(layers, size=(W, H))
                 .set_audio(aud).set_duration(dur))
        clips.append(scene.crossfadein(0.25) if i else scene)

    final = concatenate_videoclips(clips, method="compose", padding=-0.25)
    path = f"{OUT}/{name}.mp4"
    final.write_videofile(path, fps=30, codec="libx264", audio_codec="aac",
                          threads=4, preset="medium", logger=None)
    print("Duration: %.1fs" % final.duration)
    return path


# ---------------- 6. Facebook Reel upload ----------------
def post_facebook_reel(video_path, title, hashtags):
    base = "https://graph.facebook.com/v21.0"
    desc = f"{title}\n\n{hashtags}"
    try:
        s = requests.post(f"{base}/{FB_PAGE_ID}/video_reels",
                          data={"upload_phase": "start", "access_token": FB_TOKEN},
                          timeout=60)
        s.raise_for_status()
        vid, up_url = s.json()["video_id"], s.json()["upload_url"]
        size = os.path.getsize(video_path)
        u = requests.post(up_url, headers={"Authorization": f"OAuth {FB_TOKEN}",
                                           "offset": "0", "file_size": str(size)},
                          data=open(video_path, "rb"), timeout=900)
        u.raise_for_status()
        f = requests.post(f"{base}/{FB_PAGE_ID}/video_reels", data={
            "access_token": FB_TOKEN, "video_id": vid, "upload_phase": "finish",
            "video_state": "PUBLISHED", "description": desc}, timeout=120)
        print("Reel:", f.status_code, f.text)
        f.raise_for_status()
    except Exception as e:                       # fallback: normal video post
        print("Reel upload failed, fallback to /videos:", e)
        r = requests.post(f"https://graph-video.facebook.com/v21.0/{FB_PAGE_ID}/videos",
                          data={"access_token": FB_TOKEN, "title": title, "description": desc},
                          files={"source": open(video_path, "rb")}, timeout=900)
        print("Video:", r.status_code, r.text)
        r.raise_for_status()


if __name__ == "__main__":
    slot = detect_slot()
    history = load_history()
    print("Slot:", slot, "| Nepal time:", nepal_now())
    script = make_script(slot, history)
    print("Title:", script["title"])
    name = f"{nepal_now():%Y%m%d_%H%M}_{slot}"
    json.dump(script, open(f"{OUT}/{name}.json", "w"), ensure_ascii=False, indent=2)
    video = build_video(script, name)
    if DRY_RUN or not (FB_PAGE_ID and FB_TOKEN):
        print("DRY RUN: not posting")
    else:
        post_facebook_reel(video, script["title"], script.get("hashtags", ""))
        history.append({"title": script["title"], "slot": slot,
                        "date": f"{nepal_now():%Y-%m-%d %H:%M}"})
        json.dump(history[-200:], open(HISTORY, "w"), ensure_ascii=False, indent=1)
