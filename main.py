"""Hamro Chitwan News Reels: Naya Patrika + Janaastha.
Only selects articles with a verifiable publication date within MAX_AGE_HOURS
and a valid editorial image. Saves duplicate history only after successful publish.
"""
import os, re, json, hashlib, asyncio, random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
import edge_tts
from PIL import Image, ImageOps
import nepali_datetime
from render import render

PAGE_ID = os.environ["FB_PAGE_ID"]
TOKEN = os.environ["FB_PAGE_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip()
GRAPH = "https://graph.facebook.com/" + os.environ.get("FB_API_VERSION", "v21.0")
VOICE = os.environ.get("TTS_VOICE", "ne-NP-SagarNeural")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
ALLOW_SOURCE_IMAGES = os.environ.get("ALLOW_SOURCE_IMAGES", "1") == "1"

STATE, OUT = "posted.json", "out"
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "12"))
MAX_ARTICLES_PER_SOURCE = 35
MAX_ARTICLES_TO_CHECK = 50
TZ = ZoneInfo("Asia/Kathmandu")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
}
SOURCES = [
    ("Naya Patrika", "https://www.nayapatrikadaily.com/"),
    ("Janaastha", "https://www.janaaastha.com/"),
]
# Do not use broad substrings such as "ads": that also matches "uploads".
BAD_IMAGE_MARKERS = (
    "logo", "favicon", "icon", "header", "footer", "banner",
    "placeholder", "default", "no-image", "no_image", "avatar",
    "sprite", "loading", "advertisement", "social-share", "share-icon",
)
EXCLUDED_URL_PARTS = (
    "/keyword/", "/category/", "/tag/", "/author/", "/page/",
    "/search/", "/opinion/", "/blog/", "/photo-gallery/",
    "/photo-feature/", "/video/", "/videos/",
)
session = requests.Session()
session.headers.update(HEADERS)

def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def load_state():
    try:
        data = json.load(open(STATE, encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()

def save_state(items):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(sorted(items)[-500:], f, ensure_ascii=False, indent=2)

def get_page(url):
    r = session.get(url, timeout=35)
    r.raise_for_status()
    if "text/html" not in r.headers.get("content-type", "").lower():
        raise ValueError("Page response is not HTML")
    return r.text

def meta_values(soup, *keys):
    vals = []
    for key in keys:
        for tag in soup.find_all("meta", attrs={"property": key}) + soup.find_all("meta", attrs={"name": key}):
            value = clean(tag.get("content"))
            if value:
                vals.append(value)
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
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %B %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=TZ).astimezone(timezone.utc)
        except ValueError:
            continue
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
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            yield from walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue

def parse_nepali_bs_date(value):
    """Parse visible Nepali BS dates such as 'मंसिर ८, २०८३'."""
    if not value:
        return None
    value = clean(value)
    devanagari = "०१२३४५६७८९"
    latin = "0123456789"
    value = value.translate(str.maketrans(devanagari, latin))

    months = {
        "बैशाख": 1, "वैशाख": 1,
        "जेठ": 2,
        "असार": 3,
        "साउन": 4, "श्रावण": 4,
        "भदौ": 5, "भाद्र": 5, "भाद्र": 5,
        "असोज": 6, "आश्विन": 6,
        "कात्तिक": 7, "कार्तिक": 7,
        "मंसिर": 8, "मङ्सिर": 8,
        "पुस": 9, "पौष": 9,
        "माघ": 10,
        "फागुन": 11, "फाल्गुन": 11,
        "चैत": 12, "चैत्र": 12,
    }

    month_pattern = "|".join(sorted(months, key=len, reverse=True))
    patterns = [
        rf"({month_pattern})\s*([0-9]{{1,2}})[,/\s-]+([0-9]{{4}})",
        rf"([0-9]{{1,2}})\s*({month_pattern})[,/\s-]+([0-9]{{4}})",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        groups = match.groups()
        if groups[0] in months:
            month, day, year = months[groups[0]], int(groups[1]), int(groups[2])
        else:
            day, month, year = int(groups[0]), months[groups[1]], int(groups[2])
        try:
            ad_date = nepali_datetime.date(year, month, day).to_datetime_date()
            # Site shows a date, not a publication time. Noon NPT is a neutral
            # midpoint so a date-only article is not treated as midnight-old.
            local_dt = datetime(ad_date.year, ad_date.month, ad_date.day, 12, 0, tzinfo=TZ)
            return local_dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None

def published_date(soup):
    # First prefer machine-readable publication timestamps.
    values = meta_values(
        soup, "article:published_time", "datePublished",
        "pubdate", "publishdate", "date"
    )
    for obj in jsonld_objects(soup):
        if obj.get("datePublished"):
            values.append(obj["datePublished"])
    for tag in soup.select("time[datetime]"):
        values.append(tag.get("datetime"))

    for value in values:
        dt = parse_datetime(value)
        if dt:
            return dt

    # Janaastha and other Nepali portals commonly render publication dates
    # visibly in Bikram Sambat (e.g. मंसिर ८, २०८३), not ISO meta tags.
    date_texts = []
    for tag in soup.find_all(["time", "span", "div", "p"]):
        marker = " ".join([
            clean(tag.get("class", "")),
            clean(tag.get("id", "")),
            clean(tag.get("itemprop", "")),
        ]).lower()
        if any(word in marker for word in ("date", "time", "publish", "posted")):
            date_texts.append(tag.get_text(" ", strip=True))

    article = soup.find("article") or soup.find("main") or soup.body or soup
    date_texts.append(article.get_text(" ", strip=True)[:3500])
    for value in date_texts:
        dt = parse_nepali_bs_date(value)
        if dt:
            return dt
    return None

def image_values_from_jsonld(soup):
    found = []
    for obj in jsonld_objects(soup):
        for key in ("image", "thumbnailUrl", "thumbnailURL"):
            value = obj.get(key)
            if isinstance(value, str):
                found.append(value)
            elif isinstance(value, dict):
                value = value.get("url") or value.get("contentUrl")
                if isinstance(value, str):
                    found.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        found.append(item)
                    elif isinstance(item, dict):
                        u = item.get("url") or item.get("contentUrl")
                        if isinstance(u, str):
                            found.append(u)
    return found

def bad_image_url(url):
    parsed = urlparse(unquote(url).lower())
    path = parsed.path
    filename = path.rsplit("/", 1)[-1]
    if not filename or filename in (".", ".."):
        return True
    # Match known logo/banner tokens, not arbitrary substrings in directory names.
    tokens = re.split(r"[^a-z0-9]+", path)
    if any(marker in tokens for marker in BAD_IMAGE_MARKERS):
        return True
    if any(x in path for x in ("/ads/", "/advertisement/", "/social-share/")):
        return True
    return False

def image_candidates(soup, article_url):
    candidates, seen = [], set()
    def add(value):
        if not isinstance(value, str) or not value.strip():
            return
        value = urljoin(article_url, value.strip())
        p = urlparse(value)
        if p.scheme not in ("http", "https") or bad_image_url(value):
            return
        if value not in seen:
            seen.add(value)
            candidates.append(value)
    for value in meta_values(soup, "og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        add(value)
    for value in image_values_from_jsonld(soup):
        add(value)
    article = soup.find("article") or soup.find("main") or soup.body or soup
    for img in article.find_all("img"):
        if img.find_parent(["header", "footer", "nav"]):
            continue
        desc = " ".join([
            clean(img.get("alt", "")),
            " ".join(img.get("class", [])),
            clean(img.get("id", "")),
        ]).lower()
        if any(x in desc for x in ("logo", "banner", "avatar", "placeholder", "advertisement")):
            continue
        for attr in ("data-src", "data-original", "data-lazy-src", "data-original-src", "src"):
            add(img.get(attr))
        srcset = img.get("data-srcset") or img.get("srcset") or ""
        for entry in srcset.split(","):
            parts = entry.strip().split()
            if parts:
                add(parts[0])
        picture = img.find_parent("picture")
        if picture:
            for source in picture.find_all("source"):
                add(source.get("data-src"))
                add(source.get("src"))
                for entry in (source.get("data-srcset") or source.get("srcset") or "").split(","):
                    parts = entry.strip().split()
                    if parts:
                        add(parts[0])
    return candidates[:25]

def candidate_links(home_url, html):
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(home_url).netloc.lower().removeprefix("www.")
    found, seen = [], set()
    for a in soup.select("a[href]"):
        href = urljoin(home_url, a.get("href", ""))
        p = urlparse(href)
        title = clean(a.get_text(" ", strip=True))
        if p.scheme not in ("http", "https") or p.netloc.lower().removeprefix("www.") != host:
            continue
        if not 18 <= len(title) <= 180 or href in seen:
            continue
        if any(part in p.path.lower() for part in EXCLUDED_URL_PARTS):
            continue
        if p.path in ("", "/") or p.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        seen.add(href)
        found.append((href, title))
    return found[:MAX_ARTICLES_PER_SOURCE]

def parse_article(source, url, link_title):
    soup = BeautifulSoup(get_page(url), "html.parser")
    canonical = soup.find("link", rel="canonical")
    canonical_url = urljoin(url, canonical.get("href")) if canonical and canonical.get("href") else url
    if urlparse(canonical_url).netloc.lower().removeprefix("www.") != urlparse(url).netloc.lower().removeprefix("www."):
        return None
    article_types = []
    for obj in jsonld_objects(soup):
        typ = obj.get("@type", [])
        article_types.extend(typ if isinstance(typ, list) else [typ])
    og_type = (meta_values(soup, "og:type") or [""])[0].lower()
    if og_type and og_type != "article" and not any("Article" in str(t) for t in article_types):
        return None
    if any(part in urlparse(canonical_url).path.lower() for part in EXCLUDED_URL_PARTS):
        return None
    title = ((meta_values(soup, "og:title") or [""])[0]
             or (meta_values(soup, "twitter:title") or [""])[0]
             or clean((soup.find("h1") or {}).get_text(" ", strip=True))
             or link_title)
    description = (meta_values(soup, "og:description", "description") or [""])[0]
    body = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = [clean(p.get_text(" ", strip=True)) for p in body.find_all("p")]
    text = " ".join(p for p in paragraphs if len(p) > 35)
    if len(text) < 100:
        text = description
    if not title or len(text) < 40:
        return None
    if any(x in (title + " " + canonical_url).lower() for x in ("sponsored", "advertisement", "प्रायोजित", "विज्ञापन")):
        return None
    published = published_date(soup)
    if not published:
        print("Rejected: publication date missing/unreadable:", canonical_url)
        return None
    age = datetime.now(timezone.utc) - published
    if age < timedelta(minutes=-30) or age > timedelta(hours=MAX_AGE_HOURS):
        print(f"Rejected: outside {MAX_AGE_HOURS}h freshness window:", published.isoformat())
        return None
    return {
        "source": source, "url": canonical_url, "title": title,
        "text": text[:9000], "images": image_candidates(soup, canonical_url),
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
                except Exception as e:
                    print("Skipped:", url, str(e)[:160])
        except Exception as e:
            print("Source unavailable:", source, str(e)[:160])
    items.sort(key=lambda x: x["published_at"], reverse=True)
    return items

def valid_image_file(path):
    try:
        if os.path.getsize(path) < 10000:
            return False
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            w, h = im.size
            if w < 300 or h < 180 or w / max(h, 1) > 3.5:
                return False
            # Convert first frame to RGB JPEG so render.py gets a consistent image.
            im.seek(0)
            frame = ImageOps.exif_transpose(im.convert("RGB"))
            frame.save(path + ".jpg", "JPEG", quality=94)
        os.replace(path + ".jpg", path)
        return True
    except Exception:
        return False

def download_article_image(url, path):
    if not ALLOW_SOURCE_IMAGES:
        return None
    if not url or bad_image_url(url):
        return None
    tmp = path + ".download"
    try:
        with session.get(url, stream=True, timeout=45, allow_redirects=True) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower()
            if not ctype.startswith("image/"):
                raise ValueError("Not an image response: " + ctype)
            total = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(65536):
                    if chunk:
                        total += len(chunk)
                        if total > 20 * 1024 * 1024:
                            raise ValueError("Image exceeds 20 MB")
                        f.write(chunk)
        if not valid_image_file(tmp):
            raise ValueError("Image invalid, too small, or unsupported")
        os.replace(tmp, path)
        return path
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def find_article_with_photo(articles):
    if not ALLOW_SOURCE_IMAGES:
        raise RuntimeError("ALLOW_SOURCE_IMAGES=0: source images disabled.")
    for article in articles[:MAX_ARTICLES_TO_CHECK]:
        print("\nChecking:", article["source"], article["title"])
        for idx, url in enumerate(article["images"], 1):
            print(f"Image {idx}/{len(article['images'])}: {url[:140]}")
            try:
                result = download_article_image(url, f"{OUT}/article.jpg")
                if result:
                    article["image"] = url
                    print("Valid article photo selected.")
                    return article, result
            except Exception as e:
                print("Image rejected:", str(e)[:150])
    return None, None

PROMPT = """तपाईं नेपाली समाचारका Facebook Reel स्क्रिप्ट लेखक हुनुहुन्छ।
तलको समाचारका तथ्य मात्र प्रयोग गर्नुहोस्। कुनै नाम, मिति, संख्या वा कारण नबनाउनुहोस्।
मुख्य तथ्य नछुटाई सकेसम्म छोटो बनाउनुहोस्।
voice ३०-६५ नेपाली शब्दको स्वाभाविक समाचार वाचन होस्। अनावश्यक अभिवादन/भूमिका नराख्नुहोस्।
headline ६-८ शब्दभित्र होस्। keywords २-३ English शब्द। caption एउटा छोटो नेपाली वाक्य र २-३ hashtag।
JSON मा headline, voice, keywords, caption मात्र फर्काउनुहोस्।"""

def gemini_model():
    # Discover the account's currently available models; avoid hard-coded invalid model IDs.
    r = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": GEMINI_KEY}, timeout=40
    )
    r.raise_for_status()
    models = r.json().get("models", [])
    usable = [
        m["name"].split("/", 1)[-1] for m in models
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    if GEMINI_MODEL and GEMINI_MODEL in usable:
        return GEMINI_MODEL
    preferences = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash-lite"]
    for name in preferences:
        if name in usable:
            print("Using available Gemini model:", name)
            return name
    if usable:
        print("Configured model unavailable; using:", usable[0])
        return usable[0]
    raise RuntimeError("यो API key मा generateContent समर्थन गर्ने Gemini model भेटिएन। Google AI Studio मा API key/model access जाँच्नुहोस्।")

def make_script(article):
    prompt = (PROMPT + "\nस्रोत: " + article["source"] + "\nशीर्षक: " +
              article["title"] + "\nसमाचार: " + article["text"][:6500])
    model = gemini_model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    r = requests.post(url, params={"key": GEMINI_KEY}, json={
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}
    }, timeout=90)
    if not r.ok:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:1200]}")
    data = r.json()
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(raw)
    except (KeyError, IndexError, ValueError) as e:
        raise RuntimeError("Gemini ले उपयोगी JSON फर्काएन: " + str(data)[:1000]) from e
    result = {
        "headline": clean(result.get("headline"))[:110],
        "voice": clean(result.get("voice")),
        "keywords": clean(result.get("keywords") or "Nepal news"),
        "caption": clean(result.get("caption")),
    }
    if not result["headline"] or not result["voice"]:
        raise ValueError("Gemini script is incomplete")
    return result

def make_tts(text, path):
    async def run():
        await edge_tts.Communicate(text, VOICE, rate="+5%", pitch="+0Hz").save(path)
    asyncio.run(run())

def download_file(url, path):
    r = session.get(url, timeout=60)
    r.raise_for_status()
    if len(r.content) < 10000:
        raise ValueError("Downloaded media too small")
    with open(path, "wb") as f:
        f.write(r.content)
    return path

def pixabay_clip(keywords, path):
    if not PIXABAY_KEY:
        return None
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={
            "key": PIXABAY_KEY, "q": keywords, "per_page": 12, "safesearch": "true"
        }, timeout=30)
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if h.get("duration", 0) >= 6]
        if not hits:
            return None
        variants = random.choice(hits).get("videos", {})
        video = variants.get("medium") or variants.get("small") or variants.get("tiny")
        return download_file(video["url"], path) if video else None
    except Exception as e:
        print("Pixabay optional clip skipped:", str(e)[:160])
        return None

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
        r = requests.post(upload_url, headers={
            "Authorization": f"OAuth {TOKEN}", "offset": "0", "file_size": str(size)
        }, data=f, timeout=600)
    r.raise_for_status()
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
    print("\nFresh, dated articles found:", len(articles))
    fresh = [a for a in articles if a["key"] not in done]
    if not fresh:
        print("नयाँ प्रकाशित समाचार भेटिएन। यो पटक Reel बनाइएन।")
        return
    article, photo_path = find_article_with_photo(fresh)
    if not article or not photo_path:
        raise RuntimeError("४८ घण्टाभित्रका मिति प्रमाणित समाचारमध्ये प्रयोगयोग्य article photo भेटिएन। गलत logo/banner प्रयोग गरिएको छैन।")
    print("\nSELECTED:", article["source"], article["title"])
    print("Published:", article["published_at"])
    print("URL:", article["url"])
    script = make_script(article)
    print("\nSCRIPT:\n", json.dumps(script, ensure_ascii=False, indent=2))
    voice_path = f"{OUT}/voice.mp3"
    make_tts(script["voice"], voice_path)
    clip = pixabay_clip(script["keywords"], f"{OUT}/bg.mp4")
    video = render(
        photo_path, voice_path, script["headline"], f"{OUT}/reel.mp4",
        bg_video=clip,
        logo_candidates=[
            "assets/file_000000008d8c8208b201454c7871862d.png",
            "file_000000008d8c8208b201454c7871862d.png",
            "assets/hamro_chitwan.png"
        ],
    )
    if not video or not os.path.exists(video):
        raise RuntimeError("Reel video बनेन।")
    print("REEL CREATED:", video)
    if DRY_RUN:
        print("DRY_RUN=1: भिडियो बन्यो, Facebook मा publish गरिएन; history पनि save गरिएन।")
        return
    video_id = upload_reel(video, script["caption"])
    print("FACEBOOK REEL PUBLISHED:", video_id)
    done.add(article["key"])
    save_state(done)
    print("Published history saved.")

if __name__ == "__main__":
    main()
