"""
HAMRO CHITWAN - ANIMATED TIPS REELS (4 per day, auto upload)
Gemini (fresh subject + script) -> Pixabay (photos) -> edge-tts (Nepali voice)
-> custom renderer (glass UI, text animation, transitions, sound FX) -> ffmpeg -> Facebook Reel

Secrets : GEMINI_API_KEY, PIXABAY_API_KEY, FB_PAGE_ID, FB_PAGE_TOKEN
Env     : SLOT = morning7 | morning9 | evening5 | night9   (auto if empty)
          DRY_RUN = 1  -> build video only, do not upload
Needs   : ffmpeg, Noto Devanagari font, pip: pillow numpy requests edge-tts  (NO moviepy)
"""
import os, sys, json, re, asyncio, datetime, time, wave, subprocess, math, requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, features
import edge_tts

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PIXABAY_KEY = os.environ["PIXABAY_API_KEY"]
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_TOKEN = os.environ.get("FB_PAGE_TOKEN")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
GEMINI_MODELS = [m for m in [os.environ.get("GEMINI_TEXT_MODEL"), "gemini-flash-latest",
                 "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"] if m]

W, H, FPS, SR = 1080, 1920, 30, 44100
TR = 0.6                                          # transition length (sec)
GOLD = (255, 196, 0)
VOICE = "ne-NP-SagarNeural"                       # or ne-NP-HemkalaNeural
LOGO = "file_000000008d8c8208b201454c7871862d.png"
FONT = next((f for f in ["font.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"]
            if os.path.exists(f)), "font.ttf")
OUT, HISTORY = "out", "posted_topics.json"
os.makedirs(OUT, exist_ok=True)

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


# =============== 1. Gemini ===============
def make_script(slot, history):
    used = [h["title"] for h in history[-60:]]
    prompt = f"""तिमी नेपाली सोसियल मिडिया भिडियो स्क्रिप्ट राइटर हौ।
श्रेणी: {SLOTS[slot]}
आजको मिति: {nepal_now():%Y-%m-%d}
यो श्रेणीभित्र एउटा नयाँ, उपयोगी र रोचक विषय छान। यी विषय नदोहोर्याऊ: {used}
३० सेकेन्डको ठाडो भिडियोका लागि ठ्याक्कै ६ वटा दृश्य बनाऊ।
नियम: दृश्य १ आकर्षक हुक; अन्तिम दृश्य निष्कर्ष + "फलो गर्नुहोस्" सहित। जानकारीमूलक र सही हुनुपर्छ;
पक्का नभएको मिति/संख्या नलेख। narration १०-१४ शब्दको सरल नेपाली (देवनागरी)।
केवल JSON फर्काऊ:
{{"title":"भिडियोको शीर्षक","hashtags":"#nepal #nepali ...",
 "scenes":[{{"narration":"...",
   "headline":"२-४ शब्दको शक्तिशाली शीर्षक",
   "detail":"८-१४ शब्दको एक वाक्य तथ्य/टिप स्क्रिनमा",
   "stat":"छोटो संख्या/वर्ष/प्रतिशत (जस्तै १७६८, ५०%) नभए खाली स्ट्रिङ",
   "tag":"१-२ शब्दको वर्ग (जस्तै इतिहास, बचत, मौसम)",
   "bg_keywords":"english pixabay search 2-3 words (concrete, visual)",
   "badge_keywords":"english pixabay search 1-2 words (concrete object)"}}]}}"""
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


# =============== 2. Pixabay ===============
def get_image(keywords, path, orientation="vertical"):
    def q(k, o):
        return requests.get("https://pixabay.com/api/", params={
            "key": PIXABAY_KEY, "q": k, "image_type": "photo", "orientation": o,
            "safesearch": "true", "per_page": 8}, timeout=30).json().get("hits", [])
    hits = q(keywords, orientation) or q(keywords.split()[0], "all") or q("nepal", "all")
    hit = hits[int(time.time()) % len(hits)]
    open(path, "wb").write(requests.get(hit["largeImageURL"], timeout=60,
                           headers={"User-Agent": "Mozilla/5.0"}).content)
    return path


def cover(path, w, h):
    im = Image.open(path).convert("RGB")
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s) + 2, int(im.height * s) + 2), Image.LANCZOS)
    l, t = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((l, t, l + w, t + h))


# =============== 3. Voice ===============
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


def load_audio(mp3):
    wav = mp3.replace(".mp3", ".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ar", str(SR),
                    "-ac", "2", wav], check=True)
    with wave.open(wav) as w:
        raw = w.readframes(w.getnframes())
    arr = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2).astype(np.float32) / 32768.0
    pk = np.abs(arr).max() or 1.0
    return arr * (0.9 / pk)


# =============== 4. Drawing helpers ===============
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
    """sprite = rgb + alpha mask (+ optional soft shadow)"""
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
        sm = m.filter(ImageFilter.GaussianBlur(3))
        sp.shadow = Sp(Image.new("RGB", (w, h), (0, 0, 0)), sm)
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


_masks = {}


def rr_mask(w, h, r):
    k = ("m", w, h, r)
    if k not in _masks:
        m = Image.new("L", (w * 2, h * 2), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, w * 2 - 1, h * 2 - 1), r * 2, fill=255)
        _masks[k] = m.resize((w, h), Image.LANCZOS)
    return _masks[k]


def rr_border(w, h, r):
    k = ("b", w, h, r)
    if k not in _masks:
        m = Image.new("L", (w * 2, h * 2), 0)
        d = ImageDraw.Draw(m)
        d.rounded_rectangle((0, 0, w * 2 - 1, h * 2 - 1), r * 2, outline=120, width=4)
        d.rounded_rectangle((9, 9, w * 2 - 10, h * 2 - 10), r * 2 - 9, outline=28, width=4)
        d.line((r * 2, 3, w * 2 - r * 2, 3), fill=190, width=5)      # top light edge
        _masks[k] = m.resize((w, h), Image.LANCZOS)
    return _masks[k]


def rr_shadow(w, h, r):
    k = ("s", w, h, r)
    if k not in _masks:
        m = Image.new("L", (w + 160, h + 160), 0)
        ImageDraw.Draw(m).rounded_rectangle((80, 80, 80 + w, 80 + h), r, fill=255)
        _masks[k] = m.filter(ImageFilter.GaussianBlur(26)).point(lambda v: int(v * 0.5))
    return _masks[k]


def glass(base, x, y, w, h, r=48, alpha=1.0, dark=0.5, tint=(12, 22, 36)):
    """frosted-glass panel: blurred backdrop + tint + soft border + shadow"""
    if alpha <= 0.01:
        return
    x, y = int(x), int(y)
    sh = rr_shadow(w, h, r)
    sha = sh.point(lambda v: int(v * alpha)) if alpha < 1 else sh
    base.paste((0, 0, 0), (x - 80, y - 80 + 26), sha)
    reg = base.crop((x, y, x + w, y + h))
    sm = reg.resize((max(1, w // 5), max(1, h // 5)), Image.BILINEAR)
    sm = sm.filter(ImageFilter.GaussianBlur(5)).resize((w, h), Image.BILINEAR)
    sm = Image.blend(sm, Image.new("RGB", (w, h), tint), dark)
    sm = Image.blend(sm, Image.new("RGB", (w, h), (255, 255, 255)), 0.07)
    m = rr_mask(w, h, r)
    if alpha < 1:
        m = m.point(lambda v: int(v * alpha))
    base.paste(sm, (x, y), m)
    b = rr_border(w, h, r)
    if alpha < 1:
        b = b.point(lambda v: int(v * alpha))
    base.paste((255, 255, 255), (x, y), b)


def shine(base, x, y, w, h, r, p):
    """moving light streak across the glass"""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    pos = -160 + (w + 320) * p
    d = (xx + yy * 0.55) - pos
    m = (np.exp(-(d / 38.0) ** 2) * 105).astype(np.uint8)
    mask = ImageChops.multiply(Image.fromarray(m, "L"), rr_mask(w, h, r))
    base.paste((255, 255, 255), (int(x), int(y)), mask)


def circle_sp(path, D=290, ring=10):
    ss = 2
    face = cover(path, (D - 2 * ring), (D - 2 * ring))
    inner = Image.new("L", ((D - 2 * ring) * ss, (D - 2 * ring) * ss), 0)
    ImageDraw.Draw(inner).ellipse((0, 0, inner.width - 1, inner.height - 1), fill=255)
    inner = inner.resize((D - 2 * ring, D - 2 * ring), Image.LANCZOS)
    rgb = Image.new("RGB", (D, D), (255, 255, 255))
    rgb.paste(face, (ring, ring), inner)
    outer = Image.new("L", (D * ss, D * ss), 0)
    ImageDraw.Draw(outer).ellipse((0, 0, D * ss - 1, D * ss - 1), fill=255)
    a = outer.resize((D, D), Image.LANCZOS)
    sh = Sp(Image.new("RGB", (D, D), (0, 0, 0)), a.filter(ImageFilter.GaussianBlur(10)))
    return Sp(rgb, a, sh)


_shade = None


def shade_mask():
    global _shade
    if _shade is None:
        y = np.arange(H, dtype=np.float32)[:, None]
        top = np.clip(1 - y / 620, 0, 1) * 0.62
        bot = np.clip((y - 650) / 1100, 0, 1) * 0.78
        a = np.broadcast_to((top + bot) * 255, (H, W)).astype(np.uint8)
        _shade = Image.fromarray(np.ascontiguousarray(a), "L")
    return _shade


# =============== 5. Scene ===============
class Scene:
    def __init__(self, sc, bg_path, badge_path, idx, n, dur, logo):
        self.idx, self.n, self.dur, self.logo = idx, n, dur, logo
        self.src = cover(bg_path, int(W * 1.2), int(H * 1.2))
        self.badge = circle_sp(badge_path)
        inner, cw = 860, 980
        # --- headline / detail layout (shrinks until the card fits) ---
        hs, ds = 92, 46
        while True:
            hf, df = font(hs), font(ds)
            hl = wrap(sc["headline"], hf, inner)[:3]
            dl = wrap(sc["detail"], df, inner)[:3]
            hlh = sum(hf.getmetrics()) + 6
            dlh = sum(df.getmetrics()) + 6
            self.ch = 40 + 64 + 26 + len(hl) * hlh + 30 + len(dl) * dlh + 46
            if self.ch <= 800 or hs <= 60:
                break
            hs -= 8
            ds = max(36, ds - 3)
        self.cw, self.cx, self.cy = cw, 50, 1560 - self.ch
        self.hlh, self.dlh = hlh, dlh
        self.words = []                                        # (sprite, x, y, order)
        k = 0
        for li, line in enumerate(hl):
            ws = line.split()
            sps = [text_sp(w, hf) for w in ws]
            gap = int(hf.getlength(" "))
            tot = sum(s.w - 20 for s in sps) + gap * (len(sps) - 1)
            x = (cw - tot) // 2
            for s in sps:
                self.words.append((s, x - 10, 40 + 64 + 26 + li * hlh, k))
                x += s.w - 20 + gap
                k += 1
        self.nwords = k
        self.dlines = []
        for li, line in enumerate(dl):
            s = text_sp(line, df, (235, 240, 248))
            self.dlines.append((s, (cw - s.w) // 2, 40 + 64 + 26 + len(hl) * hlh + 30 + li * dlh))
        # chip
        tf = font(34)
        tag = sc.get("tag") or ""
        tw = int(tf.getlength(tag))
        cwid = tw + 90
        chip = Image.new("RGBA", (cwid, 64), GOLD + (0,))
        d = ImageDraw.Draw(chip)
        d.rounded_rectangle((0, 0, cwid - 1, 63), 32, fill=GOLD + (55,), outline=GOLD + (235,), width=3)
        d.ellipse((24, 24, 40, 40), fill=GOLD + (255,))
        d.text((52, 8), tag, font=tf, fill=(255, 214, 70, 255))
        self.chip = Sp(chip.convert("RGB"), chip.getchannel("A"))
        # stat
        self.stat = None
        if (sc.get("stat") or "").strip():
            s = text_sp(sc["stat"].strip(), font(118), GOLD)
            self.stat = s
        self.cnt = text_sp(f"{idx + 1:02d}/{n:02d}", font(34), (255, 255, 255))
        self.base_words_t = 0.95

    def bg(self, t):
        z = 1 + 0.12 * clamp01(t / self.dur)
        sw, sh = self.src.size
        ww, hh = sw / z, sh / z
        cx = sw / 2 + math.sin(self.idx * 1.7) * 40 * (t / self.dur)
        box = (cx - ww / 2, sh / 2 - hh / 2, cx + ww / 2, sh / 2 + hh / 2)
        img = self.src.resize((W, H), Image.BILINEAR, box=box)
        img.paste((0, 0, 0), (0, 0), shade_mask())
        return img

    def frame(self, t):
        t = max(0.0, t)
        img = self.bg(t)
        # ---- HUD ----
        d = ImageDraw.Draw(img, "RGBA")
        segw = (W - 100 - (self.n - 1) * 10) / self.n
        for k in range(self.n):
            x0 = 50 + k * (segw + 10)
            d.rounded_rectangle((x0, 48, x0 + segw, 58), 5, fill=(255, 255, 255, 70))
            f = 1 if k < self.idx else (clamp01(t / self.dur) if k == self.idx else 0)
            if f > 0.01:
                d.rounded_rectangle((x0, 48, x0 + segw * f, 58), 5, fill=GOLD + (255,))
        if self.logo is not None:
            blit(img, self.logo, 50, 80, ease_out(t / 0.5))
        blit(img, self.cnt, W - 50 - self.cnt.w + 10, 96, ease_out(t / 0.5))
        # ---- stat glass pill ----
        if self.stat is not None:
            k = ease_out((t - 0.25) / 0.55)
            pw, ph = self.stat.w + 90, self.stat.h + 30
            px, py = (W - pw) // 2, 290 - int((1 - k) * 60)
            glass(img, px, py, pw, ph, ph // 2, k, dark=0.42)
            blit(img, self.stat, px + 45, py + 15, k)
        # ---- main glass card ----
        kc = ease_back((t - 0.3) / 0.75)
        ac = clamp01((t - 0.3) / 0.3)
        cy = self.cy + (1 - kc) * 340
        glass(img, self.cx, cy, self.cw, self.ch, 56, ac)
        sp_t = (t - 1.5) / 0.9
        if 0 < sp_t < 1:
            shine(img, self.cx, cy, self.cw, self.ch, 56, ease_io(sp_t))
        ox, oy = self.cx, cy
        blit(img, self.chip, ox + 40, oy + 36, ease_out((t - 0.75) / 0.35))
        for s, x, y, k in self.words:
            tw0 = self.base_words_t + 0.13 * k
            a = ease_out((t - tw0) / 0.32)
            blit(img, s, ox + x, oy + y + (1 - a) * 46, a)
        t_acc = self.base_words_t + 0.13 * self.nwords
        ay = oy + 40 + 64 + 26 + len(set(y for _, _, y, _ in self.words)) * self.hlh + 14
        aw = int(150 * ease_out((t - t_acc) / 0.45))
        if aw > 2:
            ImageDraw.Draw(img).rounded_rectangle(
                (ox + (self.cw - aw) // 2, ay, ox + (self.cw + aw) // 2, ay + 8), 4, fill=GOLD)
        for j, (s, x, y) in enumerate(self.dlines):
            a = ease_out((t - (t_acc + 0.3 + 0.28 * j)) / 0.4)
            blit(img, s, ox + x, oy + y + (1 - a) * 34, a)
        # ---- floating photo badge ----
        kb = ease_back((t - 0.6) / 0.6)
        bob = 7 * math.sin(2 * math.pi * t / 2.4) if t > 1.2 else 0
        bx = W - 60 - self.badge.w
        by = self.cy - 40 - self.badge.h // 2 + bob
        blit(img, self.badge, bx, by, clamp01((t - 0.6) / 0.25), kb)
        return img


# =============== 6. Transitions ===============
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
        z = 1 + 0.55 * p
        ww, hh = W / z, H / z
        Az = A.resize((W, H), Image.BILINEAR, box=((W - ww) / 2, (H - hh) / 2, (W + ww) / 2, (H + hh) / 2))
        z2 = 0.82 + 0.18 * p
        Bs = B.resize((int(W * z2), int(H * z2)), Image.BILINEAR)
        Bc = Image.new("RGB", (W, H))
        Bc.paste(Bs, ((W - Bs.width) // 2, (H - Bs.height) // 2))
        return Image.blend(Az, Bc, p)
    if kind == "wipe":                       # diagonal wipe with glowing glass edge
        u = _grid()
        w = 0.14
        thr = -w + p * (1 + 2 * w)
        m = np.clip((thr - u) / w, 0, 1)[:, :, None]
        a, b = np.asarray(A, np.float32), np.asarray(B, np.float32)
        out = a * (1 - m) + b * m
        band = (4 * m * (1 - m))
        out = out + band * 95
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    if kind == "flash":
        a, b = np.asarray(A, np.float32), np.asarray(B, np.float32)
        if p < 0.5:
            k = p * 2
            out = a * (1 - k) + 255 * k
        else:
            k = (p - 0.5) * 2
            out = 255 * (1 - k) + b * k
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    return B


KINDS = ["slide", "zoom", "wipe", "flash", "slide", "zoom"]


# =============== 7. Sound design (synthesised, copyright-free) ===============
rng = np.random.default_rng(7)


def whoosh(dur=0.8):
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
    ph = 2 * np.pi * np.cumsum(180 + 1900 * (t / dur) ** 2) / SR
    chirp = (np.sin(ph) * 0.18)[:, None]
    s = (out * 2.2 + chirp) * env
    return s / (np.abs(s).max() + 1e-6) * 0.55


def impact(dur=1.0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    s = np.sin(2 * np.pi * (48 + 40 * np.exp(-t * 9)) * t) * np.exp(-t * 4.5)
    s += rng.standard_normal(n) * np.exp(-t * 30) * 0.25
    return np.stack([s, s], 1).astype(np.float32) * 0.6


def pop(f0=880, dur=0.16, g=0.22):
    n = int(SR * dur)
    t = np.arange(n) / SR
    s = np.sin(2 * np.pi * f0 * t * (1 + 0.5 * np.exp(-t * 40))) * np.exp(-t * 28) * g
    return np.stack([s, s], 1).astype(np.float32)


def tick(g=0.07):
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
    n = int(total * SR)
    t = np.arange(n) / SR
    s = np.zeros(n, np.float32)
    for f, g in [(110, 1), (164.81, .8), (220, .7), (277.18, .5), (329.63, .4)]:
        s += g * np.sin(2 * np.pi * f * t + f) * (0.75 + 0.25 * np.sin(2 * np.pi * 0.11 * t + f))
    env = np.minimum(1, np.minimum(t / 2.0, (total - t) / 2.0))
    s = s * env * 0.016
    return np.stack([s, s], 1).astype(np.float32)


def write_wav(path, mix):
    pk = np.abs(mix).max()
    if pk > 0.97:
        mix = mix * (0.97 / pk)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())


# =============== 8. Build video ===============
def build_video(script, name):
    scenes_in = script["scenes"][:6]
    n = len(scenes_in)
    logo = None
    if os.path.exists(LOGO):
        lg = Image.open(LOGO).convert("RGBA")
        lg.thumbnail((250, 100))
        logo = Sp(lg.convert("RGB"), lg.getchannel("A"),
                  Sp(Image.new("RGB", lg.size, (0, 0, 0)), lg.getchannel("A").filter(ImageFilter.GaussianBlur(4))))

    # assets + timeline
    items, start = [], 0.0
    for i, sc in enumerate(scenes_in):
        p = f"{OUT}/{name}_{i}"
        bg = get_image(sc["bg_keywords"], p + "_bg.jpg")
        bd = get_image(sc["badge_keywords"], p + "_badge.jpg", "all")
        voice = load_audio(make_voice(sc["narration"], p + ".mp3"))
        lead = 0.35 if i == 0 else TR * 0.75
        dur = lead + len(voice) / SR + 0.45
        items.append(dict(sc=sc, bg=bg, bd=bd, voice=voice, start=start, lead=lead, dur=dur))
        start += dur - TR
    total = items[-1]["start"] + items[-1]["dur"] + 0.3
    scenes = [Scene(it["sc"], it["bg"], it["bd"], i, n, it["dur"], logo) for i, it in enumerate(items)]

    # ---- audio mix ----
    mix = np.zeros((int((total + 1) * SR), 2), np.float32)
    mix += pad_music(total + 1)[: len(mix)]
    add(mix, impact(), 0.0, 0.9)
    for i, it in enumerate(items):
        add(mix, it["voice"], it["start"] + it["lead"], 1.0)
        if i > 0:
            add(mix, whoosh(), it["start"] - 0.05, 0.8)
        s0 = it["start"]
        add(mix, pop(880), s0 + 0.25 + 0.0, 1.0)
        add(mix, pop(1320, g=0.16), s0 + 0.6, 1.0)
        for k in range(len(it["sc"]["headline"].split())):
            add(mix, tick(), s0 + 0.95 + 0.13 * k, 1.0)
    wav = f"{OUT}/{name}_mix.wav"
    write_wav(wav, mix[: int(total * SR)])

    # ---- video frames -> ffmpeg ----
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
    return path


# =============== 9. Facebook Reel ===============
def post_facebook_reel(video_path, title, hashtags):
    base = "https://graph.facebook.com/v21.0"
    desc = f"{title}\n\n{hashtags}"
    try:
        s = requests.post(f"{base}/{FB_PAGE_ID}/video_reels",
                          data={"upload_phase": "start", "access_token": FB_TOKEN}, timeout=60)
        s.raise_for_status()
        vid, up_url = s.json()["video_id"], s.json()["upload_url"]
        u = requests.post(up_url, headers={"Authorization": f"OAuth {FB_TOKEN}", "offset": "0",
                          "file_size": str(os.path.getsize(video_path))},
                          data=open(video_path, "rb"), timeout=900)
        u.raise_for_status()
        f = requests.post(f"{base}/{FB_PAGE_ID}/video_reels", data={
            "access_token": FB_TOKEN, "video_id": vid, "upload_phase": "finish",
            "video_state": "PUBLISHED", "description": desc}, timeout=120)
        print("Reel:", f.status_code, f.text)
        f.raise_for_status()
    except Exception as e:
        print("Reel upload failed, fallback to /videos:", e)
        r = requests.post(f"https://graph-video.facebook.com/v21.0/{FB_PAGE_ID}/videos",
                          data={"access_token": FB_TOKEN, "title": title, "description": desc},
                          files={"source": open(video_path, "rb")}, timeout=900)
        print("Video:", r.status_code, r.text)
        r.raise_for_status()


if __name__ == "__main__":
    print("Text shaping (raqm):", features.check("raqm"), "| font:", FONT)
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
