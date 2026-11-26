"""
NEWS REEL GENERATOR  -  "24 Ghanta" style  -  1080 x 1350 (4:5)

Gemini (script)  ->  Wikimedia (REAL photos of the named person / place)
                 ->  Pixabay   (generic b-roll photos)
                 ->  edge-tts  (Nepali voice-over)
                 ->  custom renderer (red-gradient news look, Ken-Burns, name plates,
                      paper text slides, stat slides, headline cards, outro, transitions)
                 ->  ffmpeg  ->  Facebook (optional)

Secrets / env
  GEMINI_API_KEY   (required)       PIXABAY_API_KEY (optional, generic photos)
  FB_PAGE_ID, FB_PAGE_TOKEN (optional, upload)        DRY_RUN=1 -> do not upload
  TOPIC       = news / topic to make the video about   (else picked from SLOT list)
  NEWS_TEXT   = (optional) facts/article text Gemini must stick to
  SCENES      = number of scenes (default 11)
  LOGO_URL    = raw GitHub url of your logo png, e.g.
                https://raw.githubusercontent.com/USER/REPO/main/logo.png
  BRAND, SITE, TAGLINE, OUTRO_IMAGE, MUSIC_FILE  = optional branding / assets
  WIKI_UA     = contact string for Wikimedia User-Agent (their API policy asks for it)

Needs: ffmpeg, Noto Sans Devanagari Bold font, pip: pillow numpy requests edge-tts
"""
import os, sys, json, re, asyncio, datetime, time, wave, subprocess, math, random, io
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, features

# ------------------------------------------------------------------ config
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_TOKEN = os.environ.get("FB_PAGE_TOKEN")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
GEMINI_MODELS = [m for m in [os.environ.get("GEMINI_TEXT_MODEL"), "gemini-flash-latest",
                 "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"] if m]

W, H, FPS, SR = 1080, 1350, 30, 44100
TR = 0.5                                           # transition length (sec)
VOICE = "ne-NP-SagarNeural"                        # or ne-NP-HemkalaNeural
BRAND = os.environ.get("BRAND", "24 GHANTA")
SITE = os.environ.get("SITE", "24ghantanepal.com")
TAGLINE = os.environ.get("TAGLINE", "Fastest-growing news page of Nepal")
LOGO_URL = os.environ.get("LOGO_URL", "")
LOGO_FILE = os.environ.get("LOGO_FILE", "logo.png")
OUTRO_IMAGE = os.environ.get("OUTRO_IMAGE", "outro.png")   # optional ready-made end card
MUSIC_FILE = os.environ.get("MUSIC_FILE", "music.mp3")     # optional bg music (loops, ducks under voice)
WIKI_UA = os.environ.get("WIKI_UA", "NewsReelBot/1.0 (https://github.com/your-user/your-repo)")
UA = {"User-Agent": WIKI_UA}

RED = (200, 16, 38)
RED_DARK = (120, 6, 22)
GOLD = (255, 196, 0)
INK = (20, 14, 12)

FONT = next((f for f in ["font.ttf",
             "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
             "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"] if os.path.exists(f)), "font.ttf")
OUT, HISTORY = "out", "posted_topics.json"
os.makedirs(OUT, exist_ok=True)

SLOTS = {
    "morning7": "नेपालको ताजा राजनीतिक, आर्थिक र सामाजिक समाचारको सरल विश्लेषण",
    "morning9": "नेपालको अर्थतन्त्र, रेमिट्यान्स, डिजिटल भुक्तानी, पर्यटन र व्यापारसम्बन्धी समाचार",
    "evening5": "नेपाल र छिमेकी देशका सम्बन्ध, कूटनीति र अन्तर्राष्ट्रिय समाचार",
    "night9":   "नेपालका चर्चित व्यक्तित्व, ठाउँ, इतिहास र रोचक तथ्य",
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


# ================================================================== 1. Gemini
def make_script(topic, history):
    used = [h["title"] for h in history[-60:]]
    n = int(os.environ.get("SCENES", "11"))
    news = os.environ.get("NEWS_TEXT", "").strip()
    src = f"\nयही सामग्रीमा भएका तथ्यमा मात्र टिक:\n{news}\n" if news else ""
    prompt = f"""तिमी नेपाली न्युज पेजका लागि ठाडो (भर्टिकल) भिडियो स्क्रिप्ट लेख्ने सम्पादक हौ।
विषय: {topic}
आजको मिति: {nepal_now():%Y-%m-%d}{src}
यी विषय नदोहोर्याऊ: {used}
ठ्याक्कै {n} वटा दृश्य बनाऊ (करिब ६०-८० सेकेन्ड)। पहिलो दृश्य आकर्षक हुक, अन्तिम दृश्य type "outro"।
दृश्यका प्रकार (type):
- "person": कुनै वास्तविक व्यक्तिको दृश्य। wiki_title = उनको ठ्याक्कै अंग्रेजी Wikipedia पेज शीर्षक (जस्तै "Narendra Modi"), name_plate = नाम, role = पद।
- "place": वास्तविक ठाउँ/भवन/ल्यान्डमार्क। wiki_title = Wikipedia पेज शीर्षक (जस्तै "Patan Durbar Square")।
- "photo": सामान्य दृश्य (वस्तु/परिस्थिति)। bg_keywords = ३ शब्दसम्मको अंग्रेजी खोज।
- "text": कागजी पृष्ठभूमिमा बुलेट वाक्य। text मा जोड दिनुपर्ने शब्दलाई **यसरी** लेख।
- "stat": ठूलो संख्या। stat = छोटो संख्या/प्रतिशत/रकम, text = त्यसको व्याख्या।
- "cards": समाचार हेडलाइन कार्ड। cards = [{{"source":"स्रोतको नाम","text":"हेडलाइन"}}] (२-३ वटा; सामग्रीमा भएका वास्तविक कुरा मात्र)
- "outro": फलो/सेयर गर्न भन्ने अन्त्य।
कडा नियम:
* केवल प्रमाणित तथ्य। पक्का नभएको मिति, संख्या, उद्धरण वा आरोप नलेख। कसैको नक्कली भनाइ नबनाऊ।
* person/place मा रहेको wiki_title साँच्चै अस्तित्वमा रहेको Wikipedia पेज हुनुपर्छ।
* narration १२-१८ शब्दको सरल नेपाली (देवनागरी), तटस्थ समाचार शैली।
* हरेक दृश्यमा "text" (६-१४ शब्दको स्क्रिन वाक्य) अनिवार्य (फोटो नभेटिए यही देखिन्छ)।
केवल JSON फर्काऊ:
{{"title":"...","hashtags":"#nepal #news ...","scenes":[{{"type":"person","narration":"...","text":"...",
"wiki_title":"","name_plate":"","role":"","bg_keywords":"","stat":"","cards":[]}}]}}"""
    for model in GEMINI_MODELS:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={GEMINI_KEY}")
        for attempt in range(2):
            try:
                r = requests.post(url, json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"}}, timeout=180)
                if r.status_code == 404:
                    print("Model not found:", model)
                    break
                r.raise_for_status()
                parts = r.json()["candidates"][0]["content"]["parts"]
                txt = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                data = json.loads(re.sub(r"^```json|```$", "", txt.strip()))
                assert len(data["scenes"]) >= 4
                print("Gemini model used:", model)
                return data
            except Exception as e:
                print(f"Gemini retry ({model}):", str(e)[:200])
                time.sleep(5)
    raise RuntimeError("Gemini failed")


# ================================================================== 2. Images
def _download(url, path):
    r = requests.get(url, headers=UA, timeout=90)
    r.raise_for_status()
    open(path, "wb").write(r.content)
    Image.open(path).verify()
    return path


def _strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def commons_file(file_title, path):
    """download a Commons file (1600px wide thumb) and return a credit line"""
    r = requests.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "titles": file_title, "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata", "iiurlwidth": 1600}, headers=UA, timeout=30).json()
    for p in r["query"]["pages"].values():
        ii = (p.get("imageinfo") or [None])[0]
        if not ii or ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        _download(ii.get("thumburl") or ii["url"], path)
        md = ii.get("extmetadata", {})
        who = _strip_html(md.get("Artist", {}).get("value")) or "Unknown"
        lic = md.get("LicenseShortName", {}).get("value", "")
        return f"{who[:60]} / {lic} / Wikimedia Commons"
    return None


def wiki_photo(title, path):
    """REAL photo of the named person/place from Wikipedia/Wikimedia. Never substitutes a random photo."""
    for lang in ("en", "ne"):
        try:
            r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", params={
                "action": "query", "format": "json", "redirects": 1, "titles": title,
                "prop": "pageimages", "piprop": "name", "pilicense": "any"},
                headers=UA, timeout=30).json()
            for p in r["query"]["pages"].values():
                fn = p.get("pageimage")
                if fn:
                    c = commons_file("File:" + fn, path)
                    if c:
                        return c
        except Exception as e:
            print("wiki pageimage fail:", title, lang, str(e)[:120])
    try:                                                # fallback: Commons search, name must be in file title
        r = requests.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": title, "gsrlimit": 10, "prop": "imageinfo", "iiprop": "mime|size"},
            headers=UA, timeout=30).json()
        pages = sorted(r.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
        key = title.split()[-1].lower()
        for p in pages:
            ii = (p.get("imageinfo") or [{}])[0]
            if key in p["title"].lower() and ii.get("mime") in ("image/jpeg", "image/png") \
                    and ii.get("width", 0) >= 600:
                c = commons_file(p["title"], path)
                if c:
                    return c
    except Exception as e:
        print("commons search fail:", title, str(e)[:120])
    return None


def pixabay(keywords, path, orientation="vertical"):
    if not PIXABAY_KEY:
        return None

    def q(k, o):
        return requests.get("https://pixabay.com/api/", params={
            "key": PIXABAY_KEY, "q": k, "image_type": "photo", "orientation": o,
            "safesearch": "true", "per_page": 8}, timeout=30).json().get("hits", [])
    try:
        hits = q(keywords, orientation) or q(keywords.split()[0], "all") or q("nepal", "all")
        hit = random.choice(hits)
        return _download(hit["largeImageURL"], path)
    except Exception as e:
        print("pixabay fail:", str(e)[:120])
        return None


# ================================================================== 3. Voice
async def _tts(text, path):
    import edge_tts
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


def load_audio(src, norm=True):
    wav = os.path.splitext(src)[0] + "_pcm.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ar", str(SR), "-ac", "2", wav], check=True)
    with wave.open(wav) as w:
        raw = w.readframes(w.getnframes())
    arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2).astype(np.float32) / 32768.0
    if norm:
        arr = arr * (0.9 / (np.abs(arr).max() or 1.0))
    return arr


# ================================================================== 4. Drawing helpers
_fonts = {}


def font(sz):
    if sz not in _fonts:
        _fonts[sz] = ImageFont.truetype(FONT, sz)
    return _fonts[sz]


def clamp01(x):
    return min(max(x, 0.0), 1.0)


def ease_out(x):
    return 1 - (1 - clamp01(x)) ** 3


def ease_io(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_back(x):
    x = clamp01(x)
    c = 1.70158
    y = x - 1
    return 1 + (c + 1) * y ** 3 + c * y ** 2


class Sp:
    def __init__(self, rgb, a, shadow=None):
        self.rgb, self.a, self.shadow = rgb, a, shadow
        self.w, self.h = rgb.size


def blit(base, sp, x, y, alpha=1.0, scale=1.0):
    if alpha <= 0.01 or scale <= 0.02:
        return
    if sp.shadow is not None:
        blit(base, sp.shadow, x, y + 5, alpha * 0.55, scale)
    rgb, a = sp.rgb, sp.a
    if scale != 1.0:
        nw, nh = max(1, int(sp.w * scale)), max(1, int(sp.h * scale))
        rgb, a = rgb.resize((nw, nh), Image.BILINEAR), a.resize((nw, nh), Image.BILINEAR)
        x, y = x - (nw - sp.w) // 2, y - (nh - sp.h) // 2
    if alpha < 1.0:
        a = a.point(lambda v: int(v * alpha))
    base.paste(rgb, (int(x), int(y)), a)


def text_sp(text, f, color=(255, 255, 255), shadow=True, pad=10):
    asc, desc = f.getmetrics()
    w, h = int(f.getlength(text)) + pad * 2, asc + desc + pad * 2
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).text((pad, pad), text, font=f, fill=255)
    sp = Sp(Image.new("RGB", (w, h), color), m)
    if shadow:
        sp.shadow = Sp(Image.new("RGB", (w, h), (0, 0, 0)), m.filter(ImageFilter.GaussianBlur(3)))
    return sp


def wrap(text, f, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if f.getlength(t) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def cover(im, w, h, anchor=(0.5, 0.5)):
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s) + 2, int(im.height * s) + 2), Image.LANCZOS)
    l = int((im.width - w) * anchor[0])
    t = int((im.height - h) * anchor[1])
    return im.crop((l, t, l + w, t + h))


def rr(w, h, r, fill, outline=None, ow=0):
    """anti-aliased rounded rect -> (rgb, mask)"""
    m = Image.new("L", (w * 2, h * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * 2 - 1, h * 2 - 1), r * 2, fill=255)
    return m.resize((w, h), Image.LANCZOS)


# ------------------------------------------------------------------ news look
BW, BH = int(W * 1.16), int(H * 1.16)        # background canvas (room for Ken-Burns)
_ov = None


def overlay_masks():
    """red gradient on top (strong) and bottom (softer) - the signature look"""
    global _ov
    if _ov is None:
        y = np.arange(H, dtype=np.float32)[:, None]
        top = np.clip(1 - y / (H * 0.30), 0, 1) ** 1.5 * 0.93
        bot = np.clip((y - H * 0.68) / (H * 0.32), 0, 1) ** 1.7 * 0.72
        f = lambda a: Image.fromarray(np.ascontiguousarray(
            np.broadcast_to(a * 255, (H, W)).astype(np.uint8)), "L")
        _ov = (f(top), f(bot))
    return _ov


def grad_bg(c1, c2, w=BW, h=BH):
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    a = np.array(c1, np.float32)[None, None, :]
    b = np.array(c2, np.float32)[None, None, :]
    arr = np.broadcast_to(a * (1 - y) + b * y, (h, w, 3))
    return Image.fromarray(np.ascontiguousarray(arr).astype(np.uint8))


def paper_bg():
    """warm orange paper with grain + vignette (the text-slide background)"""
    rng = np.random.default_rng(3)
    y = np.linspace(0, 1, BH, dtype=np.float32)[:, None, None]
    top, bot = np.array([244, 160, 52], np.float32), np.array([252, 206, 108], np.float32)
    arr = top * (1 - y) + bot * y
    yy, xx = np.mgrid[0:BH, 0:BW].astype(np.float32)
    v = 1 - 0.35 * (((xx - BW / 2) / (BW / 2)) ** 2 + ((yy - BH / 2) / (BH / 2)) ** 2) ** 1.2
    arr = arr * v[:, :, None]
    arr = arr + rng.normal(0, 6, (BH, BW, 1))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def photo_bg(path, person=False):
    """full-bleed photo; wide photos get a blurred fill + sharp centred photo so faces are not cropped"""
    im = Image.open(path).convert("RGB")
    ar = im.width / im.height
    if 0.62 <= ar <= 1.08:
        return cover(im, BW, BH, (0.5, 0.28 if person else 0.5))
    bgim = cover(im, BW, BH).filter(ImageFilter.GaussianBlur(28))
    bgim = Image.blend(bgim, Image.new("RGB", (BW, BH), (0, 0, 0)), 0.28)
    fw = BW
    fh = int(fw * im.height / im.width)
    if fh > BH:
        fh, fw = BH, int(BH * im.width / im.height)
    fg = im.resize((fw, fh), Image.LANCZOS)
    m = np.ones((fh, fw), np.float32)
    e = min(60, fh // 6)
    ramp = np.linspace(0, 1, e, dtype=np.float32)
    m[:e] *= ramp[:, None]
    m[-e:] *= ramp[::-1][:, None]
    bgim.paste(fg, ((BW - fw) // 2, int((BH - fh) * 0.42)),
               Image.fromarray((m * 255).astype(np.uint8)))
    return bgim


def dim_blur_bg(path, blur=24, dark=0.55):
    im = cover(Image.open(path).convert("RGB"), BW, BH).filter(ImageFilter.GaussianBlur(blur))
    return Image.blend(im, Image.new("RGB", (BW, BH), (10, 4, 8)), dark)


def parse_marked(text):
    out, on = [], False
    for tok in text.split():
        s, e = tok.startswith("**"), tok.endswith("**")
        if s:
            on = True
        out.append((tok.strip("*"), on))
        if e:
            on = False
    return out


# ================================================================== 5. Scene
class Scene:
    def __init__(self, sc, idx, n, dur, logo):
        self.sc, self.idx, self.n, self.dur, self.logo = sc, idx, n, dur, logo
        self.kind = sc["type"]
        k = self.kind
        self.zoom = (1.0, 1.10) if idx % 2 == 0 else (1.10, 1.0)
        if k in ("person", "place", "photo"):
            self.src = photo_bg(sc["_img"], person=(k == "person"))
            self._init_plate()
        elif k == "text":
            self.src = paper_bg()
            self.zoom = (1.0, 1.035)
            self._init_text()
        elif k == "stat":
            self.src = dim_blur_bg(sc["_img"]) if sc.get("_img") else grad_bg(RED_DARK, (14, 6, 10))
            self._init_stat()
        elif k == "cards":
            self.src = dim_blur_bg(sc["_img"]) if sc.get("_img") else grad_bg((60, 8, 20), (14, 6, 10))
            self.zoom = (1.0, 1.04)
            self._init_cards()
        else:                                           # outro
            if os.path.exists(OUTRO_IMAGE):
                self.src = photo_bg(OUTRO_IMAGE)
                self.kind = "photo"
                self.plate = None
            else:
                self.src = grad_bg((8, 6, 8), (150, 10, 30))
                self._init_outro()
        self.cnt = None

    # ---------- name plate (person / place)
    def _init_plate(self):
        self.plate = None
        name = (self.sc.get("name_plate") or "").strip()
        if not name or not self.sc.get("_credit"):
            return
        role = (self.sc.get("role") or "").strip()
        f1, f2 = font(50), font(32)
        w = int(max(f1.getlength(name), f2.getlength(role) if role else 0)) + 90
        h = 74 + (50 if role else 0)
        m = rr(w, h, 12, 255)
        grad = grad_bg((250, 238, 205), (214, 184, 112), w, h)
        d = ImageDraw.Draw(grad)
        d.rectangle((0, 0, w, 6), fill=(255, 250, 225))
        d.rectangle((0, h - 8, w, h), fill=RED)
        d.text((w // 2, 14 + 25), name, font=f1, fill=INK, anchor="mm")
        if role:
            d.text((w // 2, 14 + 64), role, font=f2, fill=(70, 50, 20), anchor="mm")
        self.plate = Sp(grad, m, Sp(Image.new("RGB", (w, h), (0, 0, 0)), m.filter(ImageFilter.GaussianBlur(8))))

    # ---------- paper text slide
    def _init_text(self):
        words = parse_marked(self.sc.get("text", ""))
        maxw, fs = W - 90 - 130, 88
        while True:
            f = font(fs)
            gap = f.getlength(" ")
            lines, cur, cw = [], [], 0
            for wd, hi in words:
                wl = f.getlength(wd)
                if cur and cw + gap + wl > maxw:
                    lines.append(cur)
                    cur, cw = [], 0
                cw += (gap if cur else 0) + wl
                cur.append((wd, hi))
            if cur:
                lines.append(cur)
            lh = int(fs * 1.4)
            if len(lines) * lh <= 760 or fs <= 50:
                break
            fs -= 4
        self.fs, total = fs, len(lines) * lh
        y0 = 360 + (780 - total) // 2
        self.tw, k = [], 0
        for li, line in enumerate(lines):
            x = 130
            for wd, hi in line:
                s = text_sp(wd, f, RED if hi else INK, shadow=False, pad=6)
                self.tw.append((s, x - 6, y0 + li * lh - 6, k))
                x += int(f.getlength(wd) + gap)
                k += 1
        self.t_y0 = y0
        self.t_step = max(0.07, min(0.18, (self.dur * 0.72 - 0.5) / max(1, k)))

    # ---------- big stat
    def _init_stat(self):
        st = (self.sc.get("stat") or "").strip()
        self.st = text_sp(st, font(190 if len(st) <= 6 else 130), GOLD) if st else None
        f = font(58)
        self.sl = [(text_sp(l, f), l) for l in wrap(self.sc.get("text", ""), f, W - 200)[:4]]

    # ---------- headline cards (news-search look)
    def _init_cards(self):
        cards = (self.sc.get("cards") or [])[:3]
        self.cards = []
        fs, ft = font(30), font(44)
        for c in cards:
            lines = wrap(c.get("text", ""), ft, 800)[:3]
            h = 40 + 44 + len(lines) * 62 + 30
            w = 940
            m = rr(w, h, 34, 255)
            im = Image.new("RGB", (w, h), (255, 255, 255))
            d = ImageDraw.Draw(im)
            d.rectangle((0, 0, 14, h), fill=RED)
            d.text((50, 30), c.get("source", ""), font=fs, fill=(100, 100, 110))
            for i, l in enumerate(lines):
                d.text((50, 84 + i * 62), l, font=ft, fill=INK)
            self.cards.append(Sp(im, m, Sp(Image.new("RGB", (w, h), (0, 0, 0)), m.filter(ImageFilter.GaussianBlur(14)))))
        if not self.cards:
            self.kind = "text"
            self.src = paper_bg()
            self._init_text()

    # ---------- generated end card
    def _init_outro(self):
        self.o_brand = text_sp(BRAND, font(130), (255, 255, 255))
        self.o_tag = [text_sp(l, font(46), (235, 235, 240)) for l in wrap(TAGLINE, font(46), W - 200)[:2]]
        self.o_site = text_sp(SITE, font(44), GOLD)
        f = font(54)
        w, h = int(f.getlength("Follow us")) + 120, 96
        m = rr(w, h, 48, 255)
        im = grad_bg((230, 30, 50), (160, 10, 30), w, h)
        ImageDraw.Draw(im).text((w // 2, h // 2), "Follow us", font=f, fill=(255, 255, 255), anchor="mm")
        self.o_btn = Sp(im, m, Sp(Image.new("RGB", (w, h), (0, 0, 0)), m.filter(ImageFilter.GaussianBlur(10))))
        self.o_note = text_sp("आफ्नो साथीलाई सेयर गर्नुहोस्", font(40), (255, 255, 255))

    # ---------- per frame
    def bg(self, t):
        p = clamp01(t / self.dur)
        z = self.zoom[0] + (self.zoom[1] - self.zoom[0]) * ease_io(p)
        sw, sh = self.src.size
        vw, vh = sw / 1.16 / z, sh / 1.16 / z
        cx = sw / 2 + math.sin(self.idx * 1.7) * 26 * p
        cy = sh / 2
        return self.src.resize((W, H), Image.BILINEAR, box=(cx - vw / 2, cy - vh / 2, cx + vw / 2, cy + vh / 2))

    def frame(self, t):
        t = max(0.0, t)
        img = self.bg(t)
        top, bot = overlay_masks()
        img.paste(RED, (0, 0), top)
        img.paste(RED_DARK, (0, 0), bot)
        if self.logo is not None:
            blit(img, self.logo, W - 50 - self.logo.w + 10, 28, ease_out(t / 0.5))
        getattr(self, "fg_" + self.kind)(img, t)
        return img

    def fg_photo(self, img, t):
        self.fg_person(img, t)

    def fg_place(self, img, t):
        self.fg_person(img, t)

    def fg_person(self, img, t):
        pl = getattr(self, "plate", None)
        if pl is not None:
            k = ease_back((t - 0.7) / 0.6)
            x = W - 56 - pl.w + int((1 - ease_out((t - 0.7) / 0.5)) * 420)
            blit(img, pl, x, H - 150 - pl.h // 2, clamp01((t - 0.7) / 0.2))

    def fg_text(self, img, t):
        d = ImageDraw.Draw(img)
        kb = ease_back((t - 0.2) / 0.5)
        r = int(15 * kb)
        yb = self.t_y0 + int(self.fs * 0.66)
        d.ellipse((72 - r, yb - r, 72 + r, yb + r), fill=RED)
        for s, x, y, k in self.tw:
            a = ease_out((t - (0.5 + self.t_step * k)) / 0.3)
            blit(img, s, x, y + (1 - a) * 34, a)

    def fg_stat(self, img, t):
        if self.st is not None:
            k = ease_back((t - 0.25) / 0.6)
            blit(img, self.st, (W - self.st.w) // 2, 400, clamp01((t - 0.25) / 0.25), 0.6 + 0.4 * k)
            aw = int(220 * ease_out((t - 0.8) / 0.5))
            if aw > 2:
                ImageDraw.Draw(img).rounded_rectangle(((W - aw) // 2, 400 + self.st.h + 10, (W + aw) // 2,
                                                       400 + self.st.h + 20), 5, fill=GOLD)
        y = 400 + (self.st.h if self.st else 0) + 60
        for i, (s, _) in enumerate(self.sl):
            a = ease_out((t - (0.9 + 0.25 * i)) / 0.4)
            blit(img, s, (W - s.w) // 2, y + i * 80 + (1 - a) * 30, a)

    def fg_cards(self, img, t):
        gap = 28
        total = sum(c.h for c in self.cards) + gap * (len(self.cards) - 1)
        y = 330 + (800 - total) // 2
        step = min(0.9, max(0.4, (self.dur - 1.2) / max(1, len(self.cards))))
        for i, c in enumerate(self.cards):
            tt = (t - (0.4 + step * i)) / 0.6
            blit(img, c, 70 + int((1 - ease_out(tt)) * 0), y + int((1 - ease_back(tt)) * 260), clamp01(tt * 2))
            y += c.h + gap

    def fg_outro(self, img, t):
        blit(img, self.o_brand, (W - self.o_brand.w) // 2, 330, ease_out(t / 0.5), 0.8 + 0.2 * ease_back(t / 0.7))
        y = 520
        for s in self.o_tag:
            blit(img, s, (W - s.w) // 2, y, ease_out((t - 0.4) / 0.4))
            y += 62
        pulse = 1 + 0.05 * math.sin(2 * math.pi * t / 1.2) if t > 1.0 else 1
        blit(img, self.o_btn, (W - self.o_btn.w) // 2, 760, clamp01((t - 0.7) / 0.3), ease_back((t - 0.7) / 0.6) * pulse)
        blit(img, self.o_site, (W - self.o_site.w) // 2, 930, ease_out((t - 1.0) / 0.4))
        blit(img, self.o_note, (W - self.o_note.w) // 2, 1010, ease_out((t - 1.3) / 0.4))


# ================================================================== 6. Transitions
_u = None


def _grid():
    global _u
    if _u is None:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        _u = (xx + 0.35 * yy) / (W + 0.35 * H)
    return _u


def transition(kind, A, B, p):
    p = ease_io(p)
    if kind == "fade":
        return Image.blend(A, B, p)
    if kind == "slide":
        s = int(W * p)
        out = Image.new("RGB", (W, H))
        out.paste(A, (-s, 0))
        out.paste(B, (W - s, 0))
        return out
    if kind == "zoom":
        z = 1 + 0.5 * p
        ww, hh = W / z, H / z
        Az = A.resize((W, H), Image.BILINEAR, box=((W - ww) / 2, (H - hh) / 2, (W + ww) / 2, (H + hh) / 2))
        z2 = 0.84 + 0.16 * p
        Bs = B.resize((int(W * z2), int(H * z2)), Image.BILINEAR)
        Bc = Image.new("RGB", (W, H))
        Bc.paste(Bs, ((W - Bs.width) // 2, (H - Bs.height) // 2))
        return Image.blend(Az, Bc, p)
    if kind == "wipe":
        u = _grid()
        w = 0.14
        m = np.clip((-w + p * (1 + 2 * w) - u) / w, 0, 1)[:, :, None]
        a, b = np.asarray(A, np.float32), np.asarray(B, np.float32)
        out = a * (1 - m) + b * m + (4 * m * (1 - m)) * 90
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    if kind == "blur":
        Ab = A.filter(ImageFilter.GaussianBlur(10 * p))
        Bb = B.filter(ImageFilter.GaussianBlur(10 * (1 - p)))
        return Image.blend(Ab, Bb, p)
    return B


KINDS = ["zoom", "fade", "slide", "wipe", "blur", "zoom"]


# ================================================================== 7. Sound design
rng = np.random.default_rng(7)


def whoosh(dur=0.7):
    n = int(SR * dur)
    t = np.arange(n) / SR
    x = rng.standard_normal((n, 2)).astype(np.float32)
    fc = 250 + 6500 * np.sin(np.pi / 2 * t / dur) ** 2
    al = (1 - np.exp(-2 * np.pi * fc / SR)).astype(np.float32)
    y = np.zeros(2, np.float32)
    out = np.empty_like(x)
    for i in range(n):
        y += al[i] * (x[i] - y)
        out[i] = y
    env = (np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2)[:, None]
    chirp = (np.sin(2 * np.pi * np.cumsum(180 + 1900 * (t / dur) ** 2) / SR) * 0.15)[:, None]
    s = (out * 2.2 + chirp) * env
    return s / (np.abs(s).max() + 1e-6) * 0.5


def impact(dur=1.0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    s = np.sin(2 * np.pi * (48 + 40 * np.exp(-t * 9)) * t) * np.exp(-t * 4.5)
    s += rng.standard_normal(n) * np.exp(-t * 30) * 0.25
    return np.stack([s, s], 1).astype(np.float32) * 0.55


def pop(f0=880, dur=0.16, g=0.2):
    n = int(SR * dur)
    t = np.arange(n) / SR
    s = np.sin(2 * np.pi * f0 * t * (1 + 0.5 * np.exp(-t * 40))) * np.exp(-t * 28) * g
    return np.stack([s, s], 1).astype(np.float32)


def tick(g=0.06):
    n = int(SR * 0.035)
    t = np.arange(n) / SR
    s = np.sin(2 * np.pi * 2600 * t) * np.exp(-t * 130) * g
    return np.stack([s, s], 1).astype(np.float32)


def add(mix, snd, at, gain=1.0):
    i = int(at * SR)
    if i < 0 or i >= len(mix):
        return
    j = min(len(mix), i + len(snd))
    mix[i:j] += snd[: j - i] * gain


def pad_music(total):
    """fallback synthetic news bed (used when no music.mp3 is in the repo)"""
    n = int(total * SR)
    t = np.arange(n) / SR
    s = np.zeros(n, np.float32)
    for f, g in [(110, 1), (164.81, .8), (220, .7), (277.18, .5), (329.63, .4)]:
        s += g * np.sin(2 * np.pi * f * t + f) * (0.75 + 0.25 * np.sin(2 * np.pi * 0.11 * t + f))
    pulse = 0.65 + 0.35 * (np.sin(2 * np.pi * 1.0 * t) > 0.6)
    s = s * pulse * np.minimum(1, np.minimum(t / 2.0, (total - t) / 2.0)) * 0.03
    return np.stack([s, s], 1).astype(np.float32)


def bg_music(total, voice_track):
    n = int((total + 1) * SR)
    if os.path.exists(MUSIC_FILE):
        m = load_audio(MUSIC_FILE, norm=True)
        m = np.tile(m, (n // len(m) + 1, 1))[:n] * 0.20
    else:
        m = pad_music(total + 1)[:n]
    env = np.abs(voice_track[:n]).max(axis=1)
    k = int(0.25 * SR)
    env = np.convolve(env, np.ones(k, np.float32) / k, mode="same")
    duck = 1 - 0.65 * np.clip(env * 8, 0, 1)                     # lower music while voice speaks
    fade = np.minimum(1, np.minimum(np.arange(n) / (1.5 * SR), (n - np.arange(n)) / (2.0 * SR)))
    return m * (duck * fade)[:, None].astype(np.float32)


def write_wav(path, mix):
    pk = np.abs(mix).max()
    if pk > 0.97:
        mix = mix * (0.97 / pk)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())


# ================================================================== 8. Build video
def load_logo():
    if LOGO_URL:
        try:
            _download(LOGO_URL, LOGO_FILE)
            print("Logo downloaded from", LOGO_URL)
        except Exception as e:
            print("Logo download failed:", str(e)[:120])
    if os.path.exists(LOGO_FILE):
        lg = Image.open(LOGO_FILE).convert("RGBA")
        lg.thumbnail((300, 110))
        a = lg.getchannel("A")
        return Sp(lg.convert("RGB"), a, Sp(Image.new("RGB", lg.size, (0, 0, 0)), a.filter(ImageFilter.GaussianBlur(4))))
    return text_sp(BRAND, font(46), (255, 255, 255))            # fallback text logo


def prepare_scene(sc, i, p):
    """download assets; degrade gracefully, never put a wrong face on a named person"""
    k = sc.get("type", "text")
    sc["type"] = k
    sc["_img"], sc["_credit"] = None, None
    if k in ("person", "place") and sc.get("wiki_title"):
        c = wiki_photo(sc["wiki_title"], p + "_wiki.jpg")
        if c:
            sc["_img"], sc["_credit"] = p + "_wiki.jpg", c
        else:
            print(f"!! no Wikimedia photo for '{sc['wiki_title']}' -> scene becomes text slide")
    elif k == "photo":
        sc["_img"] = pixabay(sc.get("bg_keywords") or "nepal", p + "_bg.jpg")
        sc["_credit"] = "Pixabay" if sc["_img"] else None
    elif k in ("stat", "cards"):
        sc["_img"] = pixabay(sc.get("bg_keywords") or "newspaper", p + "_bg.jpg")
    if k in ("person", "place", "photo") and not sc["_img"]:
        sc["type"] = "text"
    if sc["type"] == "text" and not sc.get("text"):
        sc["text"] = sc.get("narration", "")[:90]
    return sc


def build_video(script, name):
    scs = script["scenes"]
    if scs[-1].get("type") != "outro":
        scs.append({"type": "outro", "narration": "ताजा अपडेटका लागि हामीलाई फलो गर्नुहोस्।", "text": ""})
    n = len(scs)
    logo = load_logo()
    items, start, credits = [], 0.0, []
    for i, sc in enumerate(scs):
        p = f"{OUT}/{name}_{i}"
        prepare_scene(sc, i, p)
        if sc.get("_credit") and sc["type"] in ("person", "place"):
            credits.append(f"{sc.get('name_plate') or sc.get('wiki_title')}: {sc['_credit']}")
        voice = load_audio(make_voice(sc["narration"], p + ".mp3"))
        lead = 0.3 if i == 0 else TR * 0.7
        dur = lead + len(voice) / SR + 0.4
        items.append(dict(sc=sc, voice=voice, start=start, lead=lead, dur=dur))
        start += dur - TR
    total = items[-1]["start"] + items[-1]["dur"] + 0.3
    scenes = [Scene(it["sc"], i, n, it["dur"], logo) for i, it in enumerate(items)]

    # ---- audio: voice + ducked music + sfx
    L = int((total + 1) * SR)
    vtrack = np.zeros((L, 2), np.float32)
    for it in items:
        add(vtrack, it["voice"], it["start"] + it["lead"], 1.0)
    mix = vtrack + bg_music(total, vtrack)
    wsh = whoosh()
    add(mix, impact(), 0.0, 0.9)
    for i, it in enumerate(items):
        if i > 0:
            add(mix, wsh, it["start"] - 0.05, 0.75)
        k = it["sc"]["type"]
        if k in ("person", "place"):
            add(mix, pop(880), it["start"] + 0.75, 1.0)
        elif k == "stat":
            add(mix, impact(0.6), it["start"] + 0.3, 0.5)
        elif k == "cards":
            for c in range(len(it["sc"].get("cards") or [])):
                add(mix, pop(660, g=0.18), it["start"] + 0.5 + 0.9 * c, 1.0)
        elif k == "text":
            add(mix, pop(1100, g=0.14), it["start"] + 0.35, 1.0)
    wav = f"{OUT}/{name}_mix.wav"
    write_wav(wav, mix[: int(total * SR)])

    # ---- frames -> ffmpeg
    path = f"{OUT}/{name}.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", wav,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    nf = int(total * FPS)
    for fi in range(nf):
        g = fi / FPS
        i = max(k for k, it in enumerate(items) if it["start"] <= g)
        t_in = g - items[i]["start"]
        if i > 0 and t_in < TR:
            A = scenes[i - 1].frame(g - items[i - 1]["start"])
            B = scenes[i].frame(t_in)
            img = transition(KINDS[(i - 1) % len(KINDS)], A, B, t_in / TR)
        else:
            img = scenes[i].frame(t_in)
        proc.stdin.write(img.tobytes())
        if fi % 150 == 0:
            print(f"frame {fi}/{nf}", flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    print("Duration: %.1fs" % total)
    return path, credits


# ================================================================== 9. Facebook
def post_facebook(video_path, title, hashtags, credits):
    desc = f"{title}\n\n{hashtags}"
    if credits:
        desc += "\n\nPhotos: " + " | ".join(credits)             # CC-BY(-SA) attribution
    mode = os.environ.get("FB_MODE", "auto")
    if mode == "auto":
        mode = "reel" if H / W >= 1.7 else "video"               # Reels need 9:16; 4:5 goes as feed video
    base = "https://graph.facebook.com/v21.0"
    if mode == "reel":
        try:
            s = requests.post(f"{base}/{FB_PAGE_ID}/video_reels",
                              data={"upload_phase": "start", "access_token": FB_TOKEN}, timeout=60)
            s.raise_for_status()
            vid, up = s.json()["video_id"], s.json()["upload_url"]
            requests.post(up, headers={"Authorization": f"OAuth {FB_TOKEN}", "offset": "0",
                          "file_size": str(os.path.getsize(video_path))},
                          data=open(video_path, "rb"), timeout=900).raise_for_status()
            f = requests.post(f"{base}/{FB_PAGE_ID}/video_reels", data={
                "access_token": FB_TOKEN, "video_id": vid, "upload_phase": "finish",
                "video_state": "PUBLISHED", "description": desc}, timeout=120)
            print("Reel:", f.status_code, f.text)
            f.raise_for_status()
            return
        except Exception as e:
            print("Reel failed, fallback to /videos:", e)
    r = requests.post(f"https://graph-video.facebook.com/v21.0/{FB_PAGE_ID}/videos",
                      data={"access_token": FB_TOKEN, "title": title, "description": desc},
                      files={"source": open(video_path, "rb")}, timeout=900)
    print("Video:", r.status_code, r.text)
    r.raise_for_status()


if __name__ == "__main__":
    print("Text shaping (raqm):", features.check("raqm"), "| font:", FONT)
    slot = detect_slot()
    topic = os.environ.get("TOPIC", "").strip() or SLOTS[slot]
    history = load_history()
    print("Slot:", slot, "| Topic:", topic)
    script = make_script(topic, history)
    print("Title:", script["title"])
    name = f"{nepal_now():%Y%m%d_%H%M}_{slot}"
    json.dump(script, open(f"{OUT}/{name}.json", "w"), ensure_ascii=False, indent=2)
    video, credits = build_video(script, name)
    print("Photo credits:", credits)
    if DRY_RUN or not (FB_PAGE_ID and FB_TOKEN):
        print("DRY RUN: not posting")
    else:
        post_facebook(video, script["title"], script.get("hashtags", ""), credits)
        history.append({"title": script["title"], "slot": slot, "date": f"{nepal_now():%Y-%m-%d %H:%M}"})
        json.dump(history[-200:], open(HISTORY, "w"), ensure_ascii=False, indent=1)
