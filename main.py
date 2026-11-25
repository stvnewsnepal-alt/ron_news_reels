
"""HAMRO CHITWAN NEWS REELS
One verified fresh story per run.
1080x1350 | Up to 3 relevant photos | Nepali TTS | Facebook Reels
"""

import os
import re
import json
import hashlib
import asyncio
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, unquote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
import edge_tts
from PIL import Image, ImageOps
import nepali_datetime

from render import render


# ============================================================
# CONFIGURATION
# ============================================================

PAGE_ID = os.environ["FB_PAGE_ID"]
TOKEN = os.environ["FB_PAGE_TOKEN"]

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip()

GRAPH = "https://graph.facebook.com/" + os.environ.get(
    "FB_API_VERSION", "v21.0"
)

VOICE = os.environ.get("TTS_VOICE", "ne-NP-SagarNeural")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "12"))

TZ = ZoneInfo("Asia/Kathmandu")

STATE = "posted.json"
OUT = "out"

MAX_LINKS_PER_SOURCE = 35
MAX_ARTICLE_PHOTOS = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8",
}

SOURCES = [
    ("Naya Patrika", "https://www.nayapatrikadaily.com/"),
    ("Janaastha", "https://www.janaaastha.com/"),
]

EXCLUDED = (
    "/keyword/", "/category/", "/tag/", "/author/", "/page/",
    "/search/", "/opinion/", "/blog/", "/photo-gallery/",
    "/photo-feature/", "/video/", "/videos/", "/epaper/",
    "/e-paper/", "/archive/",
)

BAD_IMAGE_TOKENS = {
    "logo", "favicon", "icon", "header", "footer", "banner",
    "placeholder", "default", "avatar", "sprite", "loading",
    "advertisement", "social-share",
}

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_state():
    try:
        obj = json.loads(
            Path(STATE).read_text(encoding="utf-8")
        )
        return set(obj if isinstance(obj, list) else [])
    except (OSError, ValueError, TypeError):
        return set()


def save_state(items):
    Path(STATE).write_text(
        json.dumps(
            sorted(items)[-500:],
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def get_page(url):
    response = session.get(url, timeout=(12, 35))
    response.raise_for_status()

    if "html" not in response.headers.get("content-type", "").lower():
        raise ValueError("URL ले HTML पेज दिएन")

    return response.text


def meta_values(soup, *keys):
    values = []

    for key in keys:
        tags = (
            soup.find_all("meta", attrs={"property": key})
            + soup.find_all("meta", attrs={"name": key})
        )

        for tag in tags:
            value = clean(tag.get("content"))
            if value:
                values.append(value)

    return values


# ============================================================
# DATE VERIFICATION
# ============================================================

def parse_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            clean(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)

        return dt.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


def jsonld_objects(soup):

    def walk(obj):
        if isinstance(obj, dict):
            yield obj

            for value in obj.values():
                yield from walk(value)

        elif isinstance(obj, list):
            for value in obj:
                yield from walk(value)

    for tag in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
    ):
        try:
            yield from walk(
                json.loads(tag.string or tag.get_text())
            )
        except Exception:
            continue


def nepali_digits(value):
    return str(value).translate(
        str.maketrans("०१२३४५६७८९", "0123456789")
    )


MONTHS = {
    "बैशाख": 1, "वैशाख": 1,
    "जेठ": 2, "जेष्ठ": 2,
    "असार": 3,
    "श्रावण": 4, "साउन": 4,
    "भदौ": 5, "भाद्र": 5,
    "असोज": 6, "आश्विन": 6,
    "कात्तिक": 7, "कार्तिक": 7,
    "मंसिर": 8, "मङ्सिर": 8,
    "पुष": 9, "पुस": 9,
    "माघ": 10,
    "फागुन": 11,
    "चैत": 12, "चैत्र": 12,
}


def visible_nepali_date(soup):

    candidates = []

    for selector in (
        "time", "[class*='date']",
        "[class*='time']", "[class*='publish']"
    ):
        for node in soup.select(selector):
            candidates.append(
                clean(
                    node.get("datetime")
                    or node.get_text(" ", strip=True)
                )
            )

    candidates.append(
        clean(soup.get_text(" ", strip=True))[:18000]
    )

    pattern = re.compile(
        r"([0-9]{1,2})\s*"
        r"(बैशाख|वैशाख|जेठ|जेष्ठ|असार|श्रावण|साउन|भदौ|भाद्र|"
        r"असोज|आश्विन|कात्तिक|कार्तिक|मंसिर|मङ्सिर|पुष|पुस|"
        r"माघ|फागुन|चैत|चैत्र)"
        r"\s*,?\s*([0-9]{4})"
    )

    today = nepali_datetime.date.today()

    for candidate in candidates:
        candidate = nepali_digits(candidate)
        match = pattern.search(candidate)

        if not match:
            continue

        try:
            article_date = nepali_datetime.date(
                int(match.group(3)),
                MONTHS[match.group(2)],
                int(match.group(1))
            )

            if article_date == today:
                return datetime.now(timezone.utc), "date_only"

        except (ValueError, TypeError):
            continue

    return None, None


def published_date(soup):

    values = meta_values(
        soup, "article:published_time"
    )

    for obj in jsonld_objects(soup):
        typ = obj.get("@type", [])
        typ = typ if isinstance(typ, list) else [typ]

        if (
            any("Article" in str(item) for item in typ)
            and obj.get("datePublished")
        ):
            values.append(obj["datePublished"])

    parsed = [
        parse_datetime(value)
        for value in values
    ]

    parsed = [item for item in parsed if item]

    if parsed:
        return min(parsed), "timestamp"

    return visible_nepali_date(soup)


# ============================================================
# ARTICLE IMAGE EXTRACTION
# ============================================================

def article_image_urls(soup, article_url):

    found = []
    seen = set()

    def add(raw):

        if not isinstance(raw, str) or not raw.strip():
            return

        url = urljoin(article_url, raw.strip())

        parsed = urlparse(unquote(url).lower())

        if parsed.scheme not in ("http", "https"):
            return

        tokens = re.split(r"[^a-z0-9]+", parsed.path)

        if any(token in BAD_IMAGE_TOKENS for token in tokens):
            return

        if url not in seen:
            seen.add(url)
            found.append(url)

    article = (
        soup.find("article")
        or soup.find("main")
        or soup.body
        or soup
    )

    for img in article.find_all("img"):

        if img.find_parent(["header", "footer", "nav"]):
            continue

        description = " ".join([
            clean(img.get("alt")),
            " ".join(img.get("class", [])),
            clean(img.get("id")),
        ]).lower()

        if any(
            word in description
            for word in (
                "logo", "banner", "avatar",
                "placeholder", "advertisement"
            )
        ):
            continue

        for attr in (
            "data-src", "data-original",
            "data-lazy-src", "src"
        ):
            add(img.get(attr))

        for srcset in (
            img.get("data-srcset"),
            img.get("srcset")
        ):
            for entry in (srcset or "").split(","):
                parts = entry.strip().split()

                if parts:
                    add(parts[0])

    for value in meta_values(
        soup, "og:image", "og:image:url", "twitter:image"
    ):
        add(value)

    return found[:25]


# ============================================================
# ARTICLE DISCOVERY
# ============================================================

def candidate_links(home, html):

    soup = BeautifulSoup(html, "html.parser")

    host = (
        urlparse(home).netloc.lower()
        .removeprefix("www.")
    )

    links = []
    seen = set()

    for anchor in soup.select("a[href]"):

        url = urljoin(home, anchor.get("href", ""))
        parsed = urlparse(url)

        title = clean(
            anchor.get_text(" ", strip=True)
        )

        current_host = (
            parsed.netloc.lower()
            .removeprefix("www.")
        )

        if parsed.scheme not in ("http", "https"):
            continue

        if current_host != host:
            continue

        if not 18 <= len(title) <= 180:
            continue

        if url in seen:
            continue

        path = parsed.path.lower()

        if any(item in path for item in EXCLUDED):
            continue

        if path in ("", "/"):
            continue

        if path.endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".pdf")
        ):
            continue

        seen.add(url)
        links.append((url, title))

    return links[:MAX_LINKS_PER_SOURCE]


def parse_article(source, url, link_title):

    soup = BeautifulSoup(
        get_page(url), "html.parser"
    )

    canonical = soup.find(
        "link", rel="canonical"
    )

    canonical_url = (
        urljoin(url, canonical.get("href"))
        if canonical and canonical.get("href")
        else url
    )

    original_host = (
        urlparse(url).netloc.lower()
        .removeprefix("www.")
    )

    canonical_host = (
        urlparse(canonical_url).netloc.lower()
        .removeprefix("www.")
    )

    if canonical_host != original_host:
        return None

    if any(
        item in urlparse(canonical_url).path.lower()
        for item in EXCLUDED
    ):
        return None

    article_types = []

    for obj in jsonld_objects(soup):
        typ = obj.get("@type", [])
        article_types.extend(
            typ if isinstance(typ, list) else [typ]
        )

    og_type = (
        meta_values(soup, "og:type") or [""]
    )[0].lower()

    if (
        og_type
        and og_type != "article"
        and not any(
            "Article" in str(item)
            for item in article_types
        )
    ):
        return None

    h1 = soup.find("h1")

    title = (
        (meta_values(soup, "og:title") or [""])[0]
        or (meta_values(soup, "twitter:title") or [""])[0]
        or (
            clean(h1.get_text(" ", strip=True))
            if h1 else ""
        )
        or link_title
    )

    description = (
        meta_values(soup, "og:description", "description")
        or [""]
    )[0]

    body = (
        soup.find("article")
        or soup.find("main")
        or soup.body
        or soup
    )

    paragraphs = [
        clean(p.get_text(" ", strip=True))
        for p in body.find_all("p")
    ]

    text = (
        " ".join(
            p for p in paragraphs if len(p) > 35
        )
        or description
    )

    if not title or len(text) < 60:
        return None

    if any(
        word in (title + " " + canonical_url).lower()
        for word in (
            "sponsored", "advertisement",
            "प्रायोजित", "विज्ञापन"
        )
    ):
        return None

    published, precision = published_date(soup)

    if not published:
        print(
            "Rejected: publication date not verified:",
            canonical_url
        )
        return None

    now = datetime.now(timezone.utc)

    if precision == "timestamp":

        age = now - published

        if (
            age < timedelta(minutes=-5)
            or age > timedelta(hours=MAX_AGE_HOURS)
        ):
            print(
                "Rejected: timestamp outside window:",
                published.isoformat()
            )
            return None

    else:
        print(
            "Accepted: today's Nepali date verified."
        )

    return {
        "source": source,
        "url": canonical_url,
        "title": title,
        "text": text[:10000],
        "images": article_image_urls(
            soup, canonical_url
        ),
        "published_at": published.isoformat(),
        "date_precision": precision,
        "key": hashlib.sha256(
            canonical_url.encode()
        ).hexdigest(),
    }


def discover_news():

    items = []

    for source, home in SOURCES:

        print("\nChecking:", source)

        try:
            links = candidate_links(
                home, get_page(home)
            )

            print("Article links:", len(links))

            for url, title in links:

                try:
                    item = parse_article(
                        source, url, title
                    )

                    if item:
                        items.append(item)

                except Exception as exc:
                    print(
                        "Skipped:",
                        url,
                        str(exc)[:180]
                    )

        except Exception as exc:
            print(
                "Source unavailable:",
                source,
                str(exc)[:180]
            )

    counts = {}

    for item in items:
        key = (
            item["source"],
            item["published_at"]
        )
        counts[key] = counts.get(key, 0) + 1

    suspicious = {
        key for key, count in counts.items()
        if count >= 4
    }

    if suspicious:
        before = len(items)

        items = [
            item for item in items
            if (
                item["source"],
                item["published_at"]
            ) not in suspicious
        ]

        print(
            "Rejected repeated timestamps:",
            before - len(items)
        )

    items.sort(
        key=lambda item: item["published_at"],
        reverse=True
    )

    return items


# ============================================================
# PHOTO DOWNLOAD
# ============================================================

def download_article_image(url, path):

    tmp = path + ".part"

    try:

        for attempt in range(2):

            try:

                with session.get(
                    url,
                    stream=True,
                    timeout=(12, 40)
                ) as response:

                    response.raise_for_status()

                    if not response.headers.get(
                        "content-type", ""
                    ).lower().startswith("image/"):
                        raise ValueError(
                            "URL बाट image आएन"
                        )

                    total = 0

                    with open(tmp, "wb") as file:

                        for chunk in response.iter_content(
                            65536
                        ):

                            if chunk:
                                total += len(chunk)

                                if total > 20 * 1024 * 1024:
                                    raise ValueError(
                                        "Image 20MB भन्दा ठूलो"
                                    )

                                file.write(chunk)

                with Image.open(tmp) as image:
                    image.verify()

                with Image.open(tmp) as image:

                    image = ImageOps.exif_transpose(
                        image.copy()
                    ).convert("RGB")

                    if image.width < 300 or image.height < 180:
                        raise ValueError(
                            "Image resolution धेरै सानो"
                        )

                    image.save(
                        path,
                        "JPEG",
                        quality=94
                    )

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

        print(
            "\nPhoto check:",
            article["source"],
            article["title"]
        )

        for url in article["images"]:

            try:

                path = download_article_image(
                    url, f"{OUT}/article.jpg"
                )

                article["image_url"] = url

                return article, path

            except Exception as exc:

                print(
                    "Image skipped:",
                    str(exc)[:140]
                )

    return None, None


def download_article_photos(
    article, primary_path, limit=3
):

    photos = [primary_path]

    used_urls = {
        article.get("image_url")
    }

    for url in article.get("images", []):

        if len(photos) >= limit:
            break

        if url in used_urls:
            continue

        used_urls.add(url)

        try:

            path = (
                f"{OUT}/article_{len(photos) + 1}.jpg"
            )

            download_article_image(url, path)

            photos.append(path)

            print(
                "Additional article photo:",
                path
            )

        except Exception as exc:

            print(
                "Additional photo skipped:",
                str(exc)[:140]
            )

    return photos


# ============================================================
# SCRIPT GENERATION
# ============================================================

PROMPT = """तपाईं नेपाली समाचारका छोटा Facebook Reel स्क्रिप्ट लेखक हुनुहुन्छ।
तल दिइएको समाचारका तथ्य मात्र प्रयोग गर्नुहोस्।
नाम, संख्या, मिति, उद्धरण वा कारण नथप्नुहोस्।
४५ देखि ८० नेपाली शब्दमा स्पष्ट र स्वाभाविक वाचन लेख्नुहोस्।
headline अधिकतम ८ शब्दको तथ्यपरक होस्।
keywords मा Pixabay खोज्न मिल्ने ३-६ ठोस English search terms दिनुहोस्।
JSON मात्र फर्काउनुहोस्: headline, voice, keywords, caption."""


def fallback_script(article):

    title = clean(
        article["title"]
    ).strip(" ।")

    sentences = re.split(
        r"(?<=[।!?])\s+",
        clean(article["text"])
    )

    selected = []
    words = 0

    for sentence in sentences:

        sentence = clean(sentence)

        if len(sentence) < 20:
            continue

        count = len(sentence.split())

        if words + count > 78:
            break

        selected.append(sentence)
        words += count

        if words >= 48:
            break

    voice = title + "। " + " ".join(selected)

    if len(voice.split()) > 90:
        voice = " ".join(
            voice.split()[:90]
        )

    return {
        "headline": title[:100],
        "voice": voice,
        "keywords": title,
        "caption": (
            title
            + "। #HamroChitwan #News"
        ),
    }


def gemini_model():

    if not GEMINI_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY छैन"
        )

    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": GEMINI_KEY},
        timeout=(10, 30)
    )

    response.raise_for_status()

    models = [
        model["name"].split("/", 1)[-1]
        for model in response.json().get("models", [])
        if "generateContent" in model.get(
            "supportedGenerationMethods", []
        )
    ]

    for name in (
        GEMINI_MODEL,
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
    ):

        if name and name in models:
            return name

    if models:
        return models[0]

    raise RuntimeError(
        "यस API key मा generateContent model उपलब्ध छैन"
    )


def make_script(article):

    try:

        model = gemini_model()

        prompt = (
            PROMPT
            + "\nशीर्षक: "
            + article["title"]
            + "\nसमाचार: "
            + article["text"][:7000]
        )

        url = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{model}:generateContent"
        )

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.15
            }
        }

        response = None

        for attempt in range(3):

            response = requests.post(
                url,
                params={"key": GEMINI_KEY},
                json=payload,
                timeout=(12, 65)
            )

            if response.status_code == 429:
                raise RuntimeError(
                    "Gemini quota 429"
                )

            if (
                response.status_code in
                (500, 502, 503, 504)
                and attempt < 2
            ):

                time.sleep(
                    2 ** attempt * 2
                )

                continue

            response.raise_for_status()
            break

        raw = response.json()[
            "candidates"
        ][0]["content"]["parts"][0]["text"]

        obj = json.loads(raw)

        result = {
            "headline": clean(
                obj.get("headline")
            )[:110],

            "voice": clean(
                obj.get("voice")
            ),

            "keywords": clean(
                obj.get("keywords")
            ),

            "caption": clean(
                obj.get("caption")
            ) or clean(obj.get("headline")),
        }

        if (
            not result["headline"]
            or not result["voice"]
            or not result["keywords"]
        ):
            raise ValueError(
                "Gemini script अपूरो"
            )

        return result

    except Exception as exc:

        print(
            "Gemini unavailable; factual fallback:",
            str(exc)[:220]
        )

        return fallback_script(article)


# ============================================================
# NEPALI TEXT TO SPEECH
# ============================================================

def make_tts(text, path):

    async def run():

        await edge_tts.Communicate(
            text,
            VOICE,
            rate="+7%",
            pitch="+0Hz"
        ).save(path)

    asyncio.run(run())


# ============================================================
# PIXABAY RELATED MEDIA
# ============================================================

def media_download(
    url, path,
    max_bytes=35 * 1024 * 1024
):

    tmp = path + ".part"

    try:

        with session.get(
            url,
            stream=True,
            timeout=(12, 60)
        ) as response:

            response.raise_for_status()

            total = 0

            with open(tmp, "wb") as file:

                for chunk in response.iter_content(
                    65536
                ):

                    if chunk:

                        total += len(chunk)

                        if total > max_bytes:
                            raise ValueError(
                                "Pixabay media धेरै ठूलो"
                            )

                        file.write(chunk)

        if os.path.getsize(tmp) < 12000:
            raise ValueError(
                "Pixabay media फाइल सानो छ"
            )

        os.replace(tmp, path)

        return path

    finally:

        if os.path.exists(tmp):
            os.remove(tmp)


def query_tokens(text):

    stop = {
        "the", "and", "for", "with",
        "from", "news", "nepal",
        "latest", "about", "this", "that"
    }

    return {
        word
        for word in re.findall(
            r"[a-z0-9]{3,}",
            clean(text).lower()
        )
        if word not in stop
    }


def relevance_score(tags, query):

    a = query_tokens(tags)
    b = query_tokens(query)

    return (
        len(a & b)
        / max(1, min(len(a), len(b)))
        if a and b else 0.0
    )


def pixabay_related_media(
    keywords, path_prefix
):

    if not PIXABAY_KEY:

        print(
            "PIXABAY_API_KEY छैन; अतिरिक्त दृश्य राखिएन।"
        )

        return None, None

    query = clean(keywords)[:100]

    candidates = []

    # Search related video.
    try:

        response = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_KEY,
                "q": query,
                "per_page": 20,
                "safesearch": "true"
            },
            timeout=(10, 25)
        )

        response.raise_for_status()

        for hit in response.json().get("hits", []):

            tags = clean(hit.get("tags"))

            variant = (
                hit.get("videos", {}).get("medium")
                or hit.get("videos", {}).get("small")
            )

            score = relevance_score(
                tags, query
            )

            if (
                variant
                and score >= 0.34
                and int(hit.get("duration", 0)) >= 5
            ):

                candidates.append(
                    (
                        score,
                        "video",
                        variant.get("url"),
                        tags
                    )
                )

    except Exception as exc:

        print(
            "Pixabay video search skipped:",
            str(exc)[:140]
        )

    # Search related photo.
    try:

        response = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": PIXABAY_KEY,
                "q": query,
                "image_type": "photo",
                "per_page": 20,
                "safesearch": "true"
            },
            timeout=(10, 25)
        )

        response.raise_for_status()

        for hit in response.json().get("hits", []):

            tags = clean(hit.get("tags"))

            score = relevance_score(
                tags, query
            )

            url = (
                hit.get("largeImageURL")
                or hit.get("webformatURL")
            )

            if url and score >= 0.34:

                candidates.append(
                    (
                        score,
                        "image",
                        url,
                        tags
                    )
                )

    except Exception as exc:

        print(
            "Pixabay image search skipped:",
            str(exc)[:140]
        )

    if not candidates:

        print(
            "समाचारसँग मेल खाने Pixabay दृश्य भेटिएन। "
            "Article photos मात्र प्रयोग गरिनेछ।"
        )

        return None, None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    score, kind, url, tags = candidates[0]

    try:

        destination = (
            path_prefix
            + (
                ".mp4"
                if kind == "video"
                else ".jpg"
            )
        )

        media_download(
            url, destination
        )

        if kind == "image":

            with Image.open(destination) as image:
                image.verify()

        print(
            f"Pixabay related {kind} selected "
            f"(match={score:.2f}):",
            tags
        )

        return kind, destination

    except Exception as exc:

        print(
            "Pixabay download failed:",
            str(exc)[:150]
        )

        return None, None


# ============================================================
# FACEBOOK REEL PUBLISHING
# ============================================================

def upload_reel(path, caption):

    print(
        "Uploading Reel to Facebook..."
    )

    start = requests.post(
        f"{GRAPH}/{PAGE_ID}/video_reels",
        data={
            "upload_phase": "start",
            "access_token": TOKEN
        },
        timeout=60
    )

    start.raise_for_status()

    info = start.json()

    video_id = info["video_id"]
    upload_url = info["upload_url"]

    size = os.path.getsize(path)

    with open(path, "rb") as file:

        upload = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {TOKEN}",
                "offset": "0",
                "file_size": str(size)
            },
            data=file,
            timeout=600
        )

    upload.raise_for_status()

    finish = requests.post(
        f"{GRAPH}/{PAGE_ID}/video_reels",
        data={
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption,
            "access_token": TOKEN
        },
        timeout=120
    )

    finish.raise_for_status()

    result = finish.json()

    print(
        "Facebook finish response:",
        json.dumps(result, ensure_ascii=False)
    )

    return video_id


# ============================================================
# MAIN
# ============================================================

def main():

    os.makedirs(OUT, exist_ok=True)

    print("=" * 55)
    print("HAMRO CHITWAN NEWS REELS")
    print("=" * 55)

    done = load_state()

    articles = discover_news()

    fresh = [
        article
        for article in articles
        if article["key"] not in done
    ]

    print(
        "\nVerified fresh, unused articles:",
        len(fresh)
    )

    if not fresh:

        raise RuntimeError(
            "१२ घण्टाभित्रको नयाँ timestamp वा "
            "आजको प्रमाणित नेपाली मिति भएको "
            "नयाँ समाचार भेटिएन। Reel बनाइएन।"
        )

    # Select a fresh story with a valid article image.
    article, photo_path = choose_article_photo(
        fresh
    )

    if not article or not photo_path:

        raise RuntimeError(
            "नयाँ समाचारको वास्तविक article photo भेटिएन।"
        )

    print("\nSELECTED:")
    print(article["source"], article["title"])
    print("Published:", article["published_at"])

    # Download up to 3 article photos.
    article_photos = download_article_photos(
        article,
        photo_path,
        limit=MAX_ARTICLE_PHOTOS
    )

    print(
        "Article photos available:",
        len(article_photos)
    )

    # Create Nepali script.
    script = make_script(article)

    print(
        "\nSCRIPT:\n",
        json.dumps(
            script,
            ensure_ascii=False,
            indent=2
        )
    )

    # Generate voice.
    voice_path = f"{OUT}/voice.mp3"

    make_tts(
        script["voice"],
        voice_path
    )

    if (
        not os.path.exists(voice_path)
        or os.path.getsize(voice_path) < 1000
    ):

        raise RuntimeError(
            "नेपाली voice फाइल बनेन।"
        )

    # Search for strictly related Pixabay cutaway.
    media_kind, extra_media = pixabay_related_media(
        script["keywords"],
        f"{OUT}/pixabay_related"
    )

    # Only Pixabay images supplement article photos.
    extra_images = article_photos[1:]

    if (
        media_kind == "image"
        and extra_media
        and len(article_photos) < MAX_ARTICLE_PHOTOS
    ):
        extra_images.append(extra_media)

    # Render Reel.
    video = render(
        photo_path,
        voice_path,
        script["headline"],
        [],
        f"{OUT}/reel.mp4",
        bg_video=(
            extra_media
            if media_kind == "video"
            else None
        ),
        brand="fb.com/ilovechitwan",
        work="",
        extra_images=extra_images,
        source_credit=article["source"]
    )

    if (
        not video
        or not os.path.exists(video)
        or os.path.getsize(video) < 10000
    ):

        raise RuntimeError(
            "Reel video बनेन वा फाइल अपूरो छ।"
        )

    print(
        "\nREEL CREATED:",
        video,
        "bytes:",
        os.path.getsize(video)
    )

    # Dry run prevents publishing.
    if DRY_RUN:

        print(
            "DRY_RUN=1: Reel बनेको; "
            "Facebook publish र history update रोकियो।"
        )

        return

    # Publish to Facebook.
    video_id = upload_reel(
        video,
        script["caption"]
    )

    print(
        "\nFACEBOOK REEL PUBLISHED:",
        video_id
    )

    # Save history only after successful publish.
    done.add(article["key"])
    save_state(done)

    print(
        "Published history saved successfully."
    )


if __name__ == "__main__":
    main()
