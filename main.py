import os, re, json, glob, asyncio, subprocess, hashlib, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import edge_tts

W, H = 1080, 1350
ROOT = Path(".")
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsReelsBot/1.0)"}
SOURCE_URLS = [x.strip() for x in os.getenv(
    "SOURCE_URLS",
    "https://www.nayapatrikadaily.com/,https://www.janaaastha.com/"
).split(",") if x.strip()]
MAX_AGE = int(os.getenv("MAX_NEWS_AGE_HOURS", "18"))
VOICE = os.getenv("NEPALI_VOICE", "ne-NP-SagarNeural")
BRAND = os.getenv("BRAND_NAME", "Hamro Chitwan Ramro Chitwan")
BRAND_URL = os.getenv("BRAND_URL", "fb.com/ilovechitwan")
GRAPH = os.getenv("FB_GRAPH_VERSION", "v23.0")
PAGE_ID = os.getenv("FB_PAGE_ID", "")
TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
POSTED_FILE = ROOT / "posted.json"

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r

def text_clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def discover_candidates():
    found = []
    for home in SOURCE_URLS:
        try:
            soup = BeautifulSoup(get(home).text, "html.parser")
            for a in soup.select("a[href]"):
                href = urljoin(home, a.get("href", ""))
                title = text_clean(a.get_text(" ", strip=True))
                if len(title) < 24 or len(title) > 220:
                    continue
                if urlparse(href).netloc != urlparse(home).netloc:
                    continue
                if any(x in href.lower() for x in ["/category/", "/tag/", "/author/", "facebook", "twitter"]):
                    continue
                if href.rstrip("/") == home.rstrip("/"):
                    continue
                found.append((href, title))
        except Exception as e:
            print("Homepage error:", home, str(e)[:180])
    # keep unique links
    unique, seen = [], set()
    for u, t in found:
        if u not in seen:
            seen.add(u); unique.append((u, t))
    return unique[:80]

def parse_article(url, fallback_title):
    soup = BeautifulSoup(get(url).text, "html.parser")
    title = ""
    for sel in ["meta[property='og:title']", "meta[name='twitter:title']", "h1"]:
        el = soup.select_one(sel)
        if el:
            title = text_clean(el.get("content") if el.has_attr("content") else el.get_text(" ", strip=True))
            if title: break
    title = title or fallback_title
    desc = ""
    for sel in ["meta[property='og:description']", "meta[name='description']"]:
        el = soup.select_one(sel)
        if el and el.get("content"):
            desc = text_clean(el["content"]); break
    article = soup.find("article") or soup
    paragraphs = [text_clean(p.get_text(" ", strip=True)) for p in article.select("p")]
    paragraphs = [p for p in paragraphs if len(p) > 45 and not any(k in p.lower() for k in ["subscribe", "advertisement", "प्रतिक्रिया"])]
    body = " ".join(paragraphs[:8])
    summary = body if len(body) > 100 else desc
    images = []
    for im in article.select("img"):
        src = im.get("src") or im.get("data-src") or im.get("data-original")
        if not src and im.get("srcset"):
            src = im["srcset"].split(",")[0].strip().split(" ")[0]
        if src:
            full = urljoin(url, src)
            if full.startswith("http") and full not in images:
                images.append(full)
    if not images:
        for sel in ["meta[property='og:image']", "meta[name='twitter:image']"]:
            el = soup.select_one(sel)
            if el and el.get("content"):
                images.append(urljoin(url, el["content"]))
    pub = ""
    for sel in ["meta[property='article:published_time']", "meta[name='pubdate']", "time[datetime]"]:
        el = soup.select_one(sel)
        if el:
            pub = el.get("content") or el.get("datetime") or ""
            if pub: break
    return {"url":url, "title":title, "summary":summary, "images":images[:5], "published":pub}

def parse_date(value):
    if not value: return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception: return None

def load_history():
    try: return json.loads(POSTED_FILE.read_text(encoding="utf-8"))
    except Exception: return []

def save_history(items):
    POSTED_FILE.write_text(json.dumps(items[-500:], ensure_ascii=False, indent=2), encoding="utf-8")

def choose_story():
    history = set(load_history())
    candidates = discover_candidates()
    for url, title in candidates:
        key = hashlib.sha256(url.encode()).hexdigest()
        if key in history: continue
        try:
            story = parse_article(url, title)
            if len(story["summary"]) < 80 or not story["images"]: continue
            pub = parse_date(story["published"])
            if pub and datetime.now(timezone.utc) - pub > timedelta(hours=MAX_AGE): continue
            story["key"] = key
            return story
        except Exception as e:
            print("Article skipped:", url, str(e)[:140])
    return None

def download_images(urls):
    paths = []
    for i, url in enumerate(urls[:4]):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            p = OUT / f"photo_{i}.jpg"
            p.write_bytes(r.content)
            im = Image.open(p).convert("RGB")
            if im.width < 400 or im.height < 250: continue
            im.save(p, quality=92)
            paths.append(p)
        except Exception as e:
            print("Image skipped:", str(e)[:100])
    return paths

def font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagariUI-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansDevanagariUI-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    for f in candidates:
        if os.path.exists(f): return ImageFont.truetype(f, size)
    return ImageFont.load_default()

def find_logo():
    explicit = os.getenv("LOGO_PATH")
    if explicit and Path(explicit).exists(): return Path(explicit)
    candidates = []
    for pattern in ["*logo*.png","*logo*.jpg","*.png","*.jpg","*.jpeg","*.webp"]:
        candidates += [Path(x) for x in glob.glob(pattern) if Path(x).is_file()]
    # In this repo the uploaded logo may have an opaque file name; use the first root image.
    return candidates[0] if candidates else None

def wrap(draw, txt, fnt, maxw):
    words = txt.split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        if draw.textbbox((0,0), test, font=fnt)[2] <= maxw: line = test
        else:
            if line: lines.append(line)
            line = word
    if line: lines.append(line)
    return lines

def make_scene(photo, title, idx, total, logo_path):
    im = Image.open(photo).convert("RGB")
    # Blurred full-frame background, matching the reference reel's filled canvas.
    bg = ImageOps.fit(im, (W,H)).filter(ImageFilter.GaussianBlur(28))
    bg = Image.blend(bg, Image.new("RGB",(W,H),(18,12,20)), 0.42)
    # Main photograph centered with a subtle shadow and 4:5 editorial framing.
    fg = ImageOps.contain(im, (W-90, 900))
    x, y = (W-fg.width)//2, 145
    shadow = Image.new("RGBA",(W,H),(0,0,0,0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((x-8,y-8,x+fg.width+8,y+fg.height+8), radius=20, fill=(0,0,0,150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas = bg.convert("RGBA")
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(fg.convert("RGBA"), (x,y))
    d = ImageDraw.Draw(canvas)
    # Red broadcast frame and top accent.
    d.rounded_rectangle((18,18,W-18,H-18), radius=34, outline=(210,28,48,235), width=9)
    d.rectangle((20,20,W-20,115), fill=(150,0,25,185))
    d.text((48,42), "ताजा समाचार", font=font(42,True), fill="white", stroke_width=1, stroke_fill="#5b0010")
    # Logo: preserve original asset; fit it into a small badge.
    if logo_path:
        try:
            lg = Image.open(logo_path).convert("RGBA")
            lg.thumbnail((150,90))
            canvas.alpha_composite(lg, (W-lg.width-45,30))
        except Exception: pass
    # Lower news strap
    d = ImageDraw.Draw(canvas)
    d.rectangle((20,1070,W-20,1328), fill=(5,10,20,220))
    d.rectangle((20,1070,W-20,1084), fill=(218,25,48,255))
    lines = wrap(d, title, font(40,True), W-100)[:3]
    yy = 1110
    for line in lines:
        d.text((52,yy), line, font=font(40,True), fill="white", stroke_width=1, stroke_fill="#111111")
        yy += 58
    # Animated brand is added as a separate overlay in the final FFmpeg pass.
    d.rounded_rectangle((W//2-250,1000,W//2+250,1055), radius=24, fill=(190,10,35,220))
    brandf = font(27,True)
    bb = d.textbbox((0,0), BRAND_URL, font=brandf)
    d.text(((W-(bb[2]-bb[0]))//2,1009), BRAND_URL, font=brandf, fill="white")
    out = OUT / f"scene_{idx:02d}.jpg"
    canvas.convert("RGB").save(out, quality=93)
    return out

# ImageOps imported separately for a clear failure message on minimal runners.
from PIL import ImageOps

async def make_voice(script, output):
    communicate = edge_tts.Communicate(script, VOICE, rate="+2%", volume="+0%")
    await communicate.save(str(output))

def run(cmd):
    subprocess.run(cmd, check=True)

def render_video(scene_paths, voice_path, output):
    # Each still gets a gentle Ken Burns movement; concatenate and attach narration.
    clips = []
    for i, p in enumerate(scene_paths):
        clip = OUT / f"clip_{i:02d}.mp4"
        vf = "scale=1080:1350,zoompan=z='min(zoom+0.0007,1.045)':d=150:s=1080x1350:fps=30,format=yuv420p"
        run(["ffmpeg","-y","-loop","1","-i",str(p),"-t","5","-vf",vf,"-an","-r","30","-c:v","libx264","-preset","veryfast","-crf","20",str(clip)])
        clips.append(clip)
    concat = OUT / "concat.txt"
    concat.write_text("".join(f"file '{p.resolve()}'\n" for p in clips), encoding="utf-8")
    silent = OUT / "silent.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy",str(silent)])
    # Fit video duration to narration by looping the visual sequence if required.
    run(["ffmpeg","-y","-stream_loop","-1","-i",str(silent),"-i",str(voice_path),
         "-map","0:v:0","-map","1:a:0","-shortest","-vf","fps=30,format=yuv420p",
         "-c:v","libx264","-preset","veryfast","-crf","21","-c:a","aac","-b:a","160k",
         "-movflags","+faststart","-s","1080x1350",str(output)])

def publish_reel(path, caption):
    if not PAGE_ID or not TOKEN:
        raise RuntimeError("Missing FB_PAGE_ID or FB_PAGE_ACCESS_TOKEN GitHub Secrets.")
    base = f"https://graph.facebook.com/{GRAPH}/{PAGE_ID}/video_reels"
    start = requests.post(base, data={"upload_phase":"start","access_token":TOKEN}, timeout=30)
    start.raise_for_status()
    data = start.json()
    video_id = data["video_id"]
    upload_url = data.get("upload_url")
    if not upload_url:
        raise RuntimeError("Meta did not return upload_url; check Page Reels API permissions.")
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        up = requests.post(upload_url, headers={
            "Authorization": f"OAuth {TOKEN}",
            "offset": "0",
            "file_size": str(size),
            "Content-Type": "application/octet-stream"
        }, data=f, timeout=300)
    up.raise_for_status()
    finish = requests.post(base, data={
        "upload_phase":"finish", "video_id":video_id,
        "video_state":"PUBLISHED", "description":caption,
        "access_token":TOKEN
    }, timeout=60)
    finish.raise_for_status()
    print("FACEBOOK REEL PUBLISHED:", finish.json())

def main():
    print("HAMRO CHITWAN REELS —", datetime.now(timezone.utc).isoformat())
    story = choose_story()
    if not story:
        print("No fresh, usable article found. Nothing posted.")
        return
    print("Selected:", story["title"], story["url"])
    photos = download_images(story["images"])
    if not photos:
        raise RuntimeError("Article has no downloadable usable photo; refusing unrelated stock imagery.")
    summary = text_clean(story["summary"])
    # Short narration; source facts only, no invented details.
    narration = f"{story['title']}। {summary[:650]}"
    voice = OUT / "voice.mp3"
    asyncio.run(make_voice(narration, voice))
    logo = find_logo()
    scenes = [make_scene(p, story["title"], i, len(photos), logo) for i,p in enumerate(photos)]
    video = OUT / "daily_news_reel.mp4"
    render_video(scenes, voice, video)
    caption = f"{story['title']}\\n\\nपूरा समाचार: {story['url']}\\n\\n{BRAND} | {BRAND_URL} #NepalNews #Chitwan"
    publish_reel(video, caption)
    history = load_history()
    history.append(story["key"])
    save_history(history)
    print("DONE:", video)

if __name__ == "__main__":
    main()
