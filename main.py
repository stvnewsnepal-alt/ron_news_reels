"""Hamro Chitwan news reels: Naya Patrika + Janaastha -> short Nepali voice -> Facebook Reel."""
import os, re, json, time, hashlib, random, asyncio
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import edge_tts
from render import render

PAGE_ID = os.environ["FB_PAGE_ID"]
TOKEN = os.environ["FB_PAGE_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GRAPH = "https://graph.facebook.com/" + os.environ.get("FB_API_VERSION", "v21.0")
VOICE = os.environ.get("TTS_VOICE", "ne-NP-SagarNeural")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
ALLOW_SOURCE_IMAGES = os.environ.get("ALLOW_SOURCE_IMAGES", "1") == "1"
STATE = "posted.json"
OUT = "out"
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "48"))
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HamroChitwanNewsBot/1.0; +https://www.facebook.com/ilovechitwan)"}
SOURCES = [
    ("Naya Patrika", "https://www.nayapatrikadaily.com/"),
    ("Janaastha", "https://www.janaaastha.com/"),
]

def load_state():
    try:
        data = json.load(open(STATE, encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_state(items):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(items[-500:], f, ensure_ascii=False, indent=2)

def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def get_page(url):
    r = requests.get(url, headers=HEADERS, timeout=35)
    r.raise_for_status()
    if "text/html" not in r.headers.get("content-type", "").lower():
        raise ValueError("Not an HTML page")
    return r.text

def meta(soup, *keys):
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return clean(tag["content"])
    return ""

def candidate_links(home_url, html):
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(home_url).netloc.replace("www.", "")
    found, seen = [], set()
    for a in soup.select("a[href]"):
        href = urljoin(home_url, a.get("href", ""))
        p = urlparse(href)
        title = clean(a.get_text(" ", strip=True))
        if p.scheme not in ("http", "https") or p.netloc.replace("www.", "") != host:
            continue
        if len(title) < 18 or len(title) > 180:
            continue
        if any(x in href.lower() for x in ("/category/", "/tag/", "/author/", "/page/", "javascript:")):
            continue
        if href in seen:
            continue
        seen.add(href)
        found.append((href, title))
    return found[:35]

def parse_article(source, url, link_title):
    html = get_page(url)
    soup = BeautifulSoup(html, "html.parser")
    title = meta(soup, "og:title", "twitter:title") or clean((soup.find("h1") or {}).get_text(" ", strip=True)) or link_title
    description = meta(soup, "og:description", "description")
    image = meta(soup, "og:image", "twitter:image")
    if image:
        image = urljoin(url, image)
    # Prefer article body text; avoid menus, footer, related-news lists.
    body = soup.find("article") or soup.find("main") or soup
    paragraphs = [clean(p.get_text(" ", strip=True)) for p in body.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p) > 35]
    article_text = " ".join(paragraphs[:12])
    if len(article_text) < 100:
        article_text = description or title
    if not image:
        img = body.find("img")
        if img:
            image = urljoin(url, img.get("src") or img.get("data-src") or "")
    if not title or len(article_text) < 40:
        return None
    # Exclude galleries, opinion pieces, ads and non-news categories.
    low = (title + " " + url).lower()
    excluded = ("photo-feature", "photo-gallery", "advertisement", "sponsored", "/opinion/", "/blog/")
    if any(x in low for x in excluded):
        return None
    return {"source": source, "url": url, "title": title, "text": article_text[:9000], "image": image}

def discover_news():
    items = []
    for source, home in SOURCES:
        try:
            html = get_page(home)
            for url, title in candidate_links(home, html):
                try:
                    item = parse_article(source, url, title)
                    if item:
                        item["key"] = hashlib.sha256(url.encode()).hexdigest()
                        items.append(item)
                        if len(items) >= 12:
                            break
                except Exception as e:
                    print("Article skipped:", url, str(e)[:160])
        except Exception as e:
            print("Source unavailable:", source, str(e)[:200])
    return items

PROMPT = """तपाईं नेपाली समाचारका छोटा Facebook Reel का स्क्रिप्ट लेखक हुनुहुन्छ।
तलको समाचारका तथ्य मात्र प्रयोग गर्नुहोस्। कुनै नाम, मिति, संख्या, कारण वा उद्धरण नबनाउनुहोस्।
समाचारको मुख्य कुरा नछुटाई सकेसम्म छोटो बनाउनुहोस्।
२० सेकेन्डमा भन्न मिल्ने ३०-४० नेपाली शब्द प्राथमिकता हो। धेरै महत्वपूर्ण तथ्य छुट्ने भए अधिकतम ६५ शब्दमा ४० सेकेन्डको स्क्रिप्ट दिनुहोस्।
headline: अधिकतम ६-८ शब्द, छोटो र स्पष्ट।
voice: स्वाभाविक नेपाली देवनागरी, समाचार वाचक शैली, अनावश्यक सुरुवात/अन्त्य/अभिवादन नराख्ने।
keywords: दृश्य खोज्नका लागि २-३ English शब्द।
caption: एउटा छोटो नेपाली वाक्य र २-३ hashtag।
JSON मा यी key मात्र फर्काउनुहोस्: headline, voice, keywords, caption.
समाचार स्रोत र शीर्षक:
"""

def make_script(article):
    prompt = PROMPT + "\nस्रोत: " + article["source"] + "\nशीर्षक: " + article["title"] + "\nविवरण: " + article["text"][:6500]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_KEY},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.25}},
        timeout=90)
    r.raise_for_status()
    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(raw)
    data["headline"] = clean(data.get("headline"))[:100]
    data["voice"] = clean(data.get("voice"))
    data["caption"] = clean(data.get("caption"))
    data["keywords"] = clean(data.get("keywords", "Nepal news"))
    if not data["voice"] or not data["headline"]:
        raise ValueError("Gemini returned an incomplete script")
    return data

def tts(text, path):
    async def run():
        await edge_tts.Communicate(text, VOICE, rate="+5%", pitch="+0Hz").save(path)
    asyncio.run(run())

def download(url, path, allowed_image=False):
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if not host.startswith(("www.nayapatrikadaily.com", "nayapatrikadaily.com", "www.janaaastha.com", "janaaastha.com")):
        # Images may be served from a CDN; only article-provided OG image URLs are passed here.
        pass
    with requests.get(url, headers=HEADERS, stream=True, timeout=60) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "").lower()
        if allowed_image and not ctype.startswith("image/"):
            raise ValueError("Article image URL did not return an image")
        with open(path, "wb") as f:
            for chunk in r.iter_content(65536):
                if chunk:
                    f.write(chunk)
    if os.path.getsize(path) < 5000:
        raise ValueError("Downloaded file is too small")
    return path

def pixabay_clip(keywords, path):
    if not PIXABAY_KEY:
        return None
    try:
        r = requests.get("https://pixabay.com/api/videos/",
            params={"key": PIXABAY_KEY, "q": keywords, "per_page": 12, "safesearch": "true"},
            timeout=30)
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if h.get("duration", 0) >= 6]
        if not hits:
            return None
        videos = random.choice(hits)["videos"]
        v = videos.get("medium") or videos.get("small") or videos.get("tiny")
        return download(v["url"], path) if v else None
    except Exception as e:
        print("Pixabay clip skipped:", str(e)[:160])
        return None

def upload_reel(path, caption):
    start = requests.post(f"{GRAPH}/{PAGE_ID}/video_reels",
        data={"upload_phase": "start", "access_token": TOKEN}, timeout=60)
    start.raise_for_status()
    info = start.json()
    video_id, upload_url = info["video_id"], info["upload_url"]
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        up = requests.post(upload_url,
            headers={"Authorization": f"OAuth {TOKEN}", "offset": "0", "file_size": str(size)},
            data=f, timeout=600)
    up.raise_for_status()
    finish = requests.post(f"{GRAPH}/{PAGE_ID}/video_reels",
        data={"upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
              "description": caption, "access_token": TOKEN}, timeout=120)
    finish.raise_for_status()
    return video_id

def main():
    os.makedirs(OUT, exist_ok=True)
    done = set(load_state())
    candidates = discover_news()
    fresh = [x for x in candidates if x["key"] not in done]
    if not fresh:
        print("No new article found; skipping this scheduled run.")
        return
    # Prefer most recently listed stories; sources' homepages list newest first.
    article = fresh[0]
    print("Selected:", article["source"], article["title"], article["url"])
    photo_path = None
    if ALLOW_SOURCE_IMAGES and article.get("image"):
        try:
            photo_path = download(article["image"], f"{OUT}/article.jpg", allowed_image=True)
        except Exception as e:
            print("Source image unavailable:", str(e)[:180])
    if not photo_path:
        raise RuntimeError("समाचारको मुख्य फोटो भेटिएन। अनुमति भएको फोटो उपलब्ध नभएकाले Reel रोकियो।")
    script = make_script(article)
    print(json.dumps(script, ensure_ascii=False, indent=2))
    tts(script["voice"], f"{OUT}/voice.mp3")
    clip = pixabay_clip(script.get("keywords", "Nepal news"), f"{OUT}/bg.mp4")
    video = render(photo_path, f"{OUT}/voice.mp3", script["headline"], f"{OUT}/reel.mp4",
                   bg_video=clip, logo_candidates=[
                       "assets/file_000000008d8c8208b201454c7871862d.png",
                       "file_000000008d8c8208b201454c7871862d.png",
                       "assets/hamro_chitwan.png"
                   ])
    if DRY_RUN:
        print("DRY_RUN: video created, not uploaded:", video)
        return
    video_id = upload_reel(video, script.get("caption", ""))
    print("Facebook Reel published:", video_id)
    done.add(article["key"])
    save_state(sorted(done))

if __name__ == "__main__":
    main()
