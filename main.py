"""Hamro Chitwan News Reels — one verified fresh story per run."""
import os, re, json, hashlib, asyncio, random, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, unquote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
import edge_tts
from PIL import Image, ImageOps
from render import render

PAGE_ID = os.environ["FB_PAGE_ID"]
TOKEN = os.environ["FB_PAGE_TOKEN"]
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip()
GRAPH = "https://graph.facebook.com/" + os.environ.get("FB_API_VERSION", "v21.0")
VOICE = os.environ.get("TTS_VOICE", "ne-NP-SagarNeural")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "12"))
TZ = ZoneInfo("Asia/Kathmandu")
STATE, OUT = "posted.json", "out"
MAX_LINKS_PER_SOURCE = 35
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8",
}
SOURCES = [
    ("Naya Patrika", "https://www.nayapatrikadaily.com/"),
    ("Janaastha", "https://www.janaaastha.com/"),
]
EXCLUDED = (
    "/keyword/", "/category/", "/tag/", "/author/", "/page/", "/search/",
    "/opinion/", "/blog/", "/photo-gallery/", "/photo-feature/", "/video/",
    "/videos/", "/epaper/", "/e-paper/", "/archive/",
)
BAD_IMAGE_TOKENS = {
    "logo", "favicon", "icon", "header", "footer", "banner", "placeholder",
    "default", "avatar", "sprite", "loading", "advertisement", "social-share",
}
session = requests.Session()
session.headers.update(HEADERS)


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def load_state():
    try:
        obj = json.loads(Path(STATE).read_text(encoding="utf-8"))
        return set(obj if isinstance(obj, list) else [])
    except Exception:
        return set()


def save_state(items):
    Path(STATE).write_text(json.dumps(sorted(items)[-500:], ensure_ascii=False, indent=2), encoding="utf-8")


def get_page(url):
    r = session.get(url, timeout=(12, 35))
    r.raise_for_status()
    if "html" not in r.headers.get("content-type", "").lower():
        raise ValueError("URL ले HTML पेज दिएन")
    return r.text


def meta_values(soup, *keys):
    vals = []
    for key in keys:
        for tag in soup.find_all("meta", attrs={"property": key}) + soup.find_all("meta", attrs={"name": key}):
            val = clean(tag.get("content"))
            if val:
                vals.append(val)
    return vals


def parse_datetime(value):
    if not value:
        return None
    value = clean(value)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def jsonld_objects(soup):
    def walk(obj):
        if isinstance(obj, dict):
            yield obj
            for v in obj.values():
                yield from walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from walk(v)
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            yield from walk(json.loads(tag.string or tag.get_text()))
        except Exception:
            continue


def published_date(soup):
    """Use publication timestamps only; never substitute dateModified or generic time tags."""
    values = meta_values(soup, "article:published_time")
    for obj in jsonld_objects(soup):
        typ = obj.get("@type", [])
        typ = typ if isinstance(typ, list) else [typ]
        if any("Article" in str(t) for t in typ) and obj.get("datePublished"):
            values.append(obj["datePublished"])
    parsed = [parse_datetime(v) for v in values]
    parsed = [d for d in parsed if d]
    if not parsed:
        # Date-only Nepali/visible labels cannot prove a 12-hour window. Reject rather than guess.
        return None
    # Prefer the earliest explicit published timestamp if metadata fields disagree.
    return min(parsed)


def article_image_urls(soup, article_url):
    found, seen = [], set()

    def add(raw):
        if not isinstance(raw, str) or not raw.strip():
            return
        u = urljoin(article_url, raw.strip())
        p = urlparse(unquote(u).lower())
        if p.scheme not in ("http", "https"):
            return
        tokens = re.split(r"[^a-z0-9]+", p.path)
        if any(t in BAD_IMAGE_TOKENS for t in tokens):
            return
        if u not in seen:
            seen.add(u)
            found.append(u)

    # Prioritise actual article-body images; OG images are only fallbacks.
    article = soup.find("article") or soup.find("main") or soup.body or soup
    for img in article.find_all("img"):
        if img.find_parent(["header", "footer", "nav"]):
            continue
        desc = " ".join([clean(img.get("alt")), " ".join(img.get("class", [])), clean(img.get("id"))]).lower()
        if any(x in desc for x in ("logo", "banner", "avatar", "placeholder", "advertisement")):
            continue
        for attr in ("data-src", "data-original", "data-lazy-src", "src"):
            add(img.get(attr))
        for srcset in (img.get("data-srcset"), img.get("srcset")):
            for entry in (srcset or "").split(","):
                parts = entry.strip().split()
                if parts:
                    add(parts[0])
    for val in meta_values(soup, "og:image", "og:image:url", "twitter:image"):
        add(val)
    return found[:25]


def candidate_links(home, html):
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(home).netloc.lower().removeprefix("www.")
    links, seen = [], set()
    for a in soup.select("a[href]"):
        url = urljoin(home, a.get("href", ""))
        p = urlparse(url)
        title = clean(a.get_text(" ", strip=True))
        if p.scheme not in ("http", "https") or p.netloc.lower().removeprefix("www.") != host:
            continue
        if not 18 <= len(title) <= 180 or url in seen:
            continue
        path = p.path.lower()
        if any(x in path for x in EXCLUDED) or path in ("", "/"):
            continue
        if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf")):
            continue
        seen.add(url)
        links.append((url, title))
    return links[:MAX_LINKS_PER_SOURCE]


def parse_article(source, url, link_title):
    soup = BeautifulSoup(get_page(url), "html.parser")
    canonical = soup.find("link", rel="canonical")
    canonical_url = urljoin(url, canonical.get("href")) if canonical and canonical.get("href") else url
    if urlparse(canonical_url).netloc.lower().removeprefix("www.") != urlparse(url).netloc.lower().removeprefix("www."):
        return None
    path = urlparse(canonical_url).path.lower()
    if any(x in path for x in EXCLUDED):
        return None

    article_types = []
    for obj in jsonld_objects(soup):
        typ = obj.get("@type", [])
        article_types.extend(typ if isinstance(typ, list) else [typ])
    og_type = (meta_values(soup, "og:type") or [""])[0].lower()
    if og_type and og_type != "article" and not any("Article" in str(t) for t in article_types):
        return None

    title = ((meta_values(soup, "og:title") or [""])[0]
             or (meta_values(soup, "twitter:title") or [""])[0]
             or clean((soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else "")
             or link_title)
    description = (meta_values(soup, "og:description", "description") or [""])[0]
    body = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = [clean(p.get_text(" ", strip=True)) for p in body.find_all("p")]
    text = " ".join(p for p in paragraphs if len(p) > 35) or description
    if not title or len(text) < 60:
        return None
    if any(x in (title + " " + canonical_url).lower() for x in ("sponsored", "advertisement", "प्रायोजित", "विज्ञापन")):
        return None

    published = published_date(soup)
    if not published:
        print("Rejected: exact publication timestamp missing:", canonical_url)
        return None
    now = datetime.now(timezone.utc)
    age = now - published
    if age < timedelta(minutes=-5) or age > timedelta(hours=MAX_AGE_HOURS):
        print(f"Rejected: outside {MAX_AGE_HOURS}-hour window:", published.isoformat())
        return None

    return {
        "source": source, "url": canonical_url, "title": title,
        "text": text[:10000], "images": article_image_urls(soup, canonical_url),
        "published_at": published.isoformat(),
        "key": hashlib.sha256(canonical_url.encode()).hexdigest(),
    }


def discover_news():
    items = []
    for source, home in SOURCES:
        print("\nChecking:", source)
        try:
            links = candidate_links(home, get_page(home))
            print("Article links:", len(links))
            for url, title in links:
                try:
                    item = parse_article(source, url, title)
                    if item:
                        items.append(item)
                except Exception as exc:
                    print("Skipped:", url, str(exc)[:180])
        except Exception as exc:
            print("Source unavailable:", source, str(exc)[:180])

    # Reject a timestamp repeated across several different stories from the same portal.
    counts = {}
    for item in items:
        key = (item["source"], item["published_at"])
        counts[key] = counts.get(key, 0) + 1
    suspicious = {k for k, n in counts.items() if n >= 4}
    if suspicious:
        before = len(items)
        items = [a for a in items if (a["source"], a["published_at"]) not in suspicious]
        print("Rejected repeated/template timestamps:", before - len(items))
    items.sort(key=lambda a: a["published_at"], reverse=True)
    return items


def download_article_image(url, path):
    tmp = path + ".part"
    try:
        for attempt in range(2):
            try:
                with session.get(url, stream=True, timeout=(12, 40)) as r:
                    r.raise_for_status()
                    if not r.headers.get("content-type", "").lower().startswith("image/"):
                        raise ValueError("URL बाट image आएन")
                    total = 0
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(65536):
                            if chunk:
                                total += len(chunk)
                                if total > 20 * 1024 * 1024:
                                    raise ValueError("Image 20MB भन्दा ठूलो")
                                f.write(chunk)
                with Image.open(tmp) as im:
                    im.verify()
                with Image.open(tmp) as im:
                    if im.width < 300 or im.height < 180:
                        raise ValueError("Image resolution धेरै सानो")
                    # Keep the original composition. Only normalise file format/orientation.
                    frame = ImageOps.exif_transpose(im.copy()).convert("RGB")
                    frame.save(path, "JPEG", quality=94)
                return path
            except requests.RequestException:
                if attempt:
                    raise
                time.sleep(1.5)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def choose_article_photo(articles):
    for article in articles:
        print("\nPhoto check:", article["source"], article["title"])
        for url in article["images"]:
            try:
                p = download_article_image(url, f"{OUT}/article.jpg")
                if p:
                    article["image_url"] = url
                    return article, p
            except Exception as exc:
                print("Image skipped:", str(exc)[:140])
    return None, None


PROMPT = """तपाईं नेपाली समाचारका छोटा Facebook Reel स्क्रिप्ट लेखक हुनुहुन्छ।
तल दिइएको समाचारका तथ्य मात्र प्रयोग गर्नुहोस्। नाम, संख्या, मिति, उद्धरण वा कारण नथप्नुहोस्।
४५ देखि ८० नेपाली शब्दमा स्पष्ट र स्वाभाविक वाचन लेख्नुहोस्; मुख्य तथ्य, परिणाम र सान्दर्भिक विवरण राख्नुहोस्।
headline छोटो र आकर्षक तर तथ्यपरक होस् (अधिकतम ८ शब्द)।
keywords मा Pixabay खोज्न मिल्ने ३-६ वटा ठोस English search terms दिनुहोस्। सामान्य शब्द (news, people, city) नदिनुहोस्।
JSON मात्र फर्काउनुहोस्: headline, voice, keywords, caption."""


def fallback_script(article):
    # Deterministic, fact-preserving fallback. No invented names, dates or numbers.
    title = clean(article["title"]).strip(" ।")
    sentences = re.split(r"(?<=[।!?])\s+", clean(article["text"]))
    selected, words = [], 0
    for sentence in sentences:
        sentence = clean(sentence)
        if len(sentence) < 20:
            continue
        n = len(sentence.split())
        if words + n > 78:
            break
        selected.append(sentence)
        words += n
        if words >= 48:
            break
    voice = title + "। " + " ".join(selected)
    if len(voice.split()) > 90:
        voice = " ".join(voice.split()[:90])
    return {
        "headline": title[:100],
        "voice": voice,
        "keywords": title,
        "caption": title + "। #HamroChitwan #News",
    }


def gemini_model():
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY छैन")
    r = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": GEMINI_KEY}, timeout=(10, 30)
    )
    r.raise_for_status()
    models = [
        m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    preferred = [GEMINI_MODEL, "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
    for name in preferred:
        if name and name in models:
            return name
    if models:
        return models[0]
    raise RuntimeError("यस API key मा generateContent model उपलब्ध छैन")


def make_script(article):
    try:
        model = gemini_model()
        prompt = PROMPT + "\nशीर्षक: " + article["title"] + "\nसमाचार: " + article["text"][:7000]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.15}
        }
        response = None
        for attempt in range(3):
            try:
                response = requests.post(url, params={"key": GEMINI_KEY}, json=payload, timeout=(12, 65))
                if response.status_code == 429:
                    raise RuntimeError("Gemini quota 429 — retry नगरी fallback प्रयोग")
                if response.status_code in (500, 502, 503, 504):
                    if attempt < 2:
                        time.sleep(2 ** attempt * 2)
                        continue
                response.raise_for_status()
                break
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt * 2)
        data = response.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        obj = json.loads(raw)
        result = {
            "headline": clean(obj.get("headline"))[:110],
            "voice": clean(obj.get("voice")),
            "keywords": clean(obj.get("keywords")),
            "caption": clean(obj.get("caption")),
        }
        if not result["headline"] or not result["voice"] or not result["keywords"]:
            raise ValueError("Gemini script अपूरो")
        return result
    except Exception as exc:
        print("Gemini unavailable; using factual fallback:", str(exc)[:220])
        return fallback_script(article)


def make_tts(text, path):
    async def run():
        await edge_tts.Communicate(text, VOICE, rate="+7%", pitch="+0Hz").save(path)
    asyncio.run(run())


def media_download(url, path, max_bytes=35 * 1024 * 1024):
    tmp = path + ".part"
    try:
        with session.get(url, stream=True, timeout=(12, 60)) as r:
            r.raise_for_status()
            total = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(65536):
                    if chunk:
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("Pixabay media धेरै ठूलो")
                        f.write(chunk)
        if os.path.getsize(tmp) < 12000:
            raise ValueError("Pixabay media फाइल सानो छ")
        os.replace(tmp, path)
        return path
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def query_tokens(text):
    stop = {"the","and","for","with","from","news","nepal","latest","about","this","that"}
    return {x for x in re.findall(r"[a-z0-9]{3,}", clean(text).lower()) if x not in stop}


def relevance_score(tags, query):
    a, b = query_tokens(tags), query_tokens(query)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def pixabay_related_media(keywords, path_prefix):
    """Return only a clearly query-matched Pixabay video/image; otherwise no stock media."""
    if not PIXABAY_KEY:
        print("PIXABAY_API_KEY छैन; अतिरिक्त दृश्य राखिएन।")
        return None, None
    query = clean(keywords)[:100]
    if not query:
        return None, None
    candidates = []
    try:
        rv = requests.get("https://pixabay.com/api/videos/", params={
            "key": PIXABAY_KEY, "q": query, "per_page": 20, "safesearch": "true"
        }, timeout=(10, 25))
        rv.raise_for_status()
        for hit in rv.json().get("hits", []):
            tags = clean(hit.get("tags"))
            score = relevance_score(tags, query)
            variants = hit.get("videos", {})
            variant = variants.get("medium") or variants.get("small")
            if variant and score >= 0.34 and int(hit.get("duration", 0)) >= 5:
                candidates.append((score, "video", variant.get("url"), tags))
    except Exception as exc:
        print("Pixabay video search skipped:", str(exc)[:140])

    try:
        ri = requests.get("https://pixabay.com/api/", params={
            "key": PIXABAY_KEY, "q": query, "image_type": "photo",
            "per_page": 20, "safesearch": "true"
        }, timeout=(10, 25))
        ri.raise_for_status()
        for hit in ri.json().get("hits", []):
            tags = clean(hit.get("tags"))
            score = relevance_score(tags, query)
            url = hit.get("largeImageURL") or hit.get("webformatURL")
            if url and score >= 0.34:
                candidates.append((score, "image", url, tags))
    except Exception as exc:
        print("Pixabay image search skipped:", str(exc)[:140])

    if not candidates:
        print("Pixabay मा समाचारसँग स्पष्ट मेल खाने दृश्य भेटिएन; stock media प्रयोग गरिएन।")
        return None, None

    candidates.sort(key=lambda x: x[0], reverse=True)
    score, kind, url, tags = candidates[0]
    try:
        ext = ".mp4" if kind == "video" else ".jpg"
        dest = path_prefix + ext
        media_download(url, dest)
        if kind == "image":
            with Image.open(dest) as im:
                im.verify()
        print(f"Pixabay related {kind} selected (match={score:.2f}):", tags)
        return kind, dest
    except Exception as exc:
        print("Pixabay media download failed:", str(exc)[:150])
        return None, None


def upload_reel(path, caption):
    print("Uploading Reel to Facebook...")
    start = requests.post(f"{GRAPH}/{PAGE_ID}/video_reels", data={
        "upload_phase": "start", "access_token": TOKEN
    }, timeout=60)
    start.raise_for_status()
    info = start.json()
    video_id, upload_url = info["video_id"], info["upload_url"]
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        up = requests.post(upload_url, headers={
            "Authorization": f"OAuth {TOKEN}", "offset": "0", "file_size": str(size)
        }, data=f, timeout=600)
    up.raise_for_status()
    finish = requests.post(f"{GRAPH}/{PAGE_ID}/video_reels", data={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "description": caption, "access_token": TOKEN
    }, timeout=120)
    finish.raise_for_status()
    return video_id


def main():
    os.makedirs(OUT, exist_ok=True)
    print("=" * 55, "\nHAMRO CHITWAN NEWS REELS\n", "=" * 55)
    done = load_state()
    articles = discover_news()
    fresh = [a for a in articles if a["key"] not in done]
    print("\nVerified fresh, unused articles:", len(fresh))
    if not fresh:
        print(f"{MAX_AGE_HOURS} घण्टाभित्रको नयाँ र मिति प्रमाणित समाचार भेटिएन। Reel बनाइएन।")
        return

    article, photo_path = choose_article_photo(fresh)
    if not article or not photo_path:
        raise RuntimeError("नयाँ समाचारको वास्तविक article photo भेटिएन; गलत वा generic फोटो प्रयोग गरिएन।")

    print("\nSELECTED:", article["source"], article["title"])
    print("Published:", article["published_at"])
    script = make_script(article)
    print("\nSCRIPT:\n", json.dumps(script, ensure_ascii=False, indent=2))

    voice_path = f"{OUT}/voice.mp3"
    make_tts(script["voice"], voice_path)
    media_kind, extra_media = pixabay_related_media(script["keywords"], f"{OUT}/pixabay_related")
    video = render(
        photo_path, voice_path, script["headline"], f"{OUT}/reel.mp4",
        bg_video=extra_media if media_kind == "video" else None,
        logo_candidates=[],
        source_credit=article["source"],
        extra_image=extra_media if media_kind == "image" else None,
    )
    if not video or not os.path.exists(video):
        raise RuntimeError("Reel video बनेन।")
    print("REEL CREATED:", video)

    if DRY_RUN:
        print("DRY_RUN=1: भिडियो बन्यो; publish र history update गरिएन।")
        return

    video_id = upload_reel(video, script["caption"])
    print("FACEBOOK REEL PUBLISHED:", video_id)
    done.add(article["key"])
    save_state(done)
    print("Published history saved only after successful Facebook publish.")


if __name__ == "__main__":
    main()
