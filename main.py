
"""Hamro Chitwan News Reels
Naya Patrika + Janaastha -> verified article image -> Nepali voice -> Facebook Reel
"""

import os
import re
import json
import time
import hashlib
import random
import asyncio
from urllib.parse import urljoin, urlparse, unquote

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
MAX_ARTICLES_PER_SOURCE = 35
MAX_ARTICLES_TO_CHECK = 50

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8"
}

SOURCES = [
    ("Naya Patrika", "https://www.nayapatrikadaily.com/"),
    ("Janaastha", "https://www.janaaastha.com/"),
]

BAD_IMAGE_WORDS = (
    "logo", "favicon", "icon", "header", "footer",
    "banner", "placeholder", "default", "no-image",
    "no_image", "avatar", "sprite", "loading",
    "advertisement", "ads", "social-share"
)

EXCLUDED_WORDS = (
    "photo-feature",
    "photo-gallery",
    "advertisement",
    "sponsored",
    "/opinion/",
    "/blog/",
)


# --------------------------------------------------
# STATE
# --------------------------------------------------

def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def save_state(items):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(
            list(items)[-500:],
            f,
            ensure_ascii=False,
            indent=2
        )


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


# --------------------------------------------------
# HTTP
# --------------------------------------------------

def get_page(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=35
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()

    if "text/html" not in content_type:
        raise ValueError("Not an HTML page")

    return response.text


# --------------------------------------------------
# METADATA
# --------------------------------------------------

def meta_values(soup, *keys):
    results = []

    for key in keys:
        for tag in soup.find_all(
            "meta",
            attrs={"property": key}
        ) + soup.find_all(
            "meta",
            attrs={"name": key}
        ):
            value = clean(tag.get("content"))

            if value:
                results.append(value)

    return results


def jsonld_images(soup):
    results = []

    def extract(obj):
        if isinstance(obj, dict):
            for key in (
                "image",
                "thumbnailUrl",
                "thumbnailURL"
            ):
                value = obj.get(key)

                if isinstance(value, str):
                    results.append(value)

                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            results.append(item)
                        elif isinstance(item, dict):
                            extract(item)

                elif isinstance(value, dict):
                    extract(value)

            for value in obj.values():
                if isinstance(value, (dict, list)):
                    extract(value)

        elif isinstance(obj, list):
            for item in obj:
                extract(item)

    for script in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
    ):
        try:
            extract(json.loads(script.string or script.get_text()))
        except Exception:
            continue

    return results


# --------------------------------------------------
# IMAGE DISCOVERY
# --------------------------------------------------

def bad_image_url(url):
    value = unquote(url).lower()
    filename = urlparse(value).path.split("/")[-1]

    return any(
        word in value
        for word in BAD_IMAGE_WORDS
    ) or filename in ("", ".", "..")


def image_candidates(soup, article_url):
    candidates = []
    seen = set()

    def add(value):
        if not value or not isinstance(value, str):
            return

        value = value.strip()

        if value.startswith("//"):
            value = "https:" + value

        value = urljoin(article_url, value)

        parsed = urlparse(value)

        if parsed.scheme not in ("http", "https"):
            return

        if bad_image_url(value):
            print("Rejected logo/placeholder:", value[:150])
            return

        if value not in seen:
            seen.add(value)
            candidates.append(value)

    # 1. Open Graph images
    for value in meta_values(soup, "og:image", "og:image:url"):
        add(value)

    # 2. Twitter images
    for value in meta_values(soup, "twitter:image", "twitter:image:src"):
        add(value)

    # 3. JSON-LD images
    for value in jsonld_images(soup):
        add(value)

    # 4. Article body images
    article = (
        soup.find("article")
        or soup.find("main")
        or soup.body
        or soup
    )

    for img in article.find_all("img"):
        parent = img.parent

        # Ignore images inside website navigation/header/footer
        if img.find_parent(
            ["header", "footer", "nav"]
        ):
            continue

        if parent and parent.find_parent(
            ["header", "footer", "nav"]
        ):
            continue

        alt = clean(img.get("alt", ""))
        classes = " ".join(img.get("class", []))
        identifier = clean(img.get("id", ""))

        description = (
            alt + " " + classes + " " + identifier
        ).lower()

        if any(word in description for word in BAD_IMAGE_WORDS):
            continue

        # Standard and lazy-loaded images
        for attribute in (
            "data-src",
            "data-original",
            "data-lazy-src",
            "data-original-src",
            "src"
        ):
            add(img.get(attribute))

        # Responsive images
        srcset = (
            img.get("data-srcset")
            or img.get("srcset")
            or ""
        )

        for entry in srcset.split(","):
            parts = entry.strip().split()

            if parts:
                add(parts[0])

        # Picture/source elements
        picture = img.find_parent("picture")

        if picture:
            for source in picture.find_all("source"):
                add(source.get("data-src"))
                add(source.get("src"))

                srcset = (
                    source.get("data-srcset")
                    or source.get("srcset")
                    or ""
                )

                for entry in srcset.split(","):
                    parts = entry.strip().split()
                    if parts:
                        add(parts[0])

    return candidates[:20]


# --------------------------------------------------
# ARTICLE DISCOVERY
# --------------------------------------------------

def candidate_links(home_url, html):
    soup = BeautifulSoup(html, "html.parser")

    host = urlparse(home_url).netloc.replace("www.", "")

    found = []
    seen = set()

    for a in soup.select("a[href]"):
        href = urljoin(home_url, a.get("href", ""))
        parsed = urlparse(href)

        title = clean(a.get_text(" ", strip=True))

        if parsed.scheme not in ("http", "https"):
            continue

        if parsed.netloc.replace("www.", "") != host:
            continue

        if len(title) < 18 or len(title) > 180:
            continue

        if href in seen:
            continue

        if any(
            x in href.lower()
            for x in (
                "/category/",
                "/tag/",
                "/author/",
                "/page/",
                "/search/",
                "javascript:"
            )
        ):
            continue

        if href.rstrip("/") == home_url.rstrip("/"):
            continue

        seen.add(href)
        found.append((href, title))

    return found[:MAX_ARTICLES_PER_SOURCE]


def parse_article(source, url, link_title):
    html = get_page(url)
    soup = BeautifulSoup(html, "html.parser")

    title = (
        (meta_values(soup, "og:title") or [""])[0]
        or (meta_values(soup, "twitter:title") or [""])[0]
        or clean(
            (soup.find("h1") or {}).get_text(" ", strip=True)
        )
        or link_title
    )

    description = (
        (meta_values(soup, "og:description", "description") or [""])[0]
    )

    article_body = (
        soup.find("article")
        or soup.find("main")
        or soup.body
        or soup
    )

    paragraphs = [
        clean(p.get_text(" ", strip=True))
        for p in article_body.find_all("p")
    ]

    paragraphs = [
        p for p in paragraphs if len(p) > 35
    ]

    article_text = " ".join(paragraphs[:12])

    if len(article_text) < 100:
        article_text = description or title

    if not title or len(article_text) < 40:
        return None

    low = (title + " " + url).lower()

    if any(word in low for word in EXCLUDED_WORDS):
        return None

    photos = image_candidates(soup, url)

    return {
        "source": source,
        "url": url,
        "title": title,
        "text": article_text[:9000],
        "images": photos
    }


def discover_news():
    items = []

    for source, home in SOURCES:
        print("\nChecking source:", source)

        try:
            html = get_page(home)
            links = candidate_links(home, html)

            print("Article links found:", len(links))

            for url, title in links:
                try:
                    item = parse_article(source, url, title)

                    if not item:
                        continue

                    item["key"] = hashlib.sha256(
                        url.encode()
                    ).hexdigest()

                    items.append(item)

                except Exception as e:
                    print(
                        "Article skipped:",
                        url,
                        str(e)[:150]
                    )

        except Exception as e:
            print(
                "Source unavailable:",
                source,
                str(e)[:180]
            )

    return items


# --------------------------------------------------
# IMAGE VALIDATION AND DOWNLOAD
# --------------------------------------------------

def valid_image_file(path):
    try:
        with open(path, "rb") as f:
            header = f.read(32)

        valid = (
            header.startswith(b"\xff\xd8\xff")
            or header.startswith(b"\x89PNG\r\n\x1a\n")
            or header.startswith(b"GIF87a")
            or header.startswith(b"GIF89a")
            or (
                len(header) >= 12
                and header[0:4] == b"RIFF"
                and header[8:12] == b"WEBP"
            )
        )

        if not valid:
            return False

        if os.path.getsize(path) < 10000:
            return False

        # Check actual image dimensions when Pillow is available.
        try:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
                image.verify()

            if width < 300 or height < 180:
                return False

            if width <= 0 or height <= 0:
                return False

        except ImportError:
            print("Pillow unavailable; using image signature validation.")

        except Exception as e:
            print("Image decode failed:", str(e)[:120])
            return False

        return True

    except Exception:
        return False


def download_article_image(url, path):
    if not ALLOW_SOURCE_IMAGES:
        return None

    if not url or bad_image_url(url):
        return None

    try:
        with requests.get(
            url,
            headers=HEADERS,
            stream=True,
            timeout=45,
            allow_redirects=True
        ) as response:

            response.raise_for_status()

            content_type = response.headers.get(
                "content-type", ""
            ).lower()

            if not content_type.startswith("image/"):
                raise ValueError(
                    "URL did not return an image: " + content_type
                )

            temp_path = path + ".tmp"

            total = 0

            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue

                    total += len(chunk)

                    if total > 20 * 1024 * 1024:
                        raise ValueError("Image exceeds 20 MB")

                    f.write(chunk)

            if not valid_image_file(temp_path):
                raise ValueError("Invalid, damaged or very small image")

            os.replace(temp_path, path)

            return path

    except Exception:
        try:
            if os.path.exists(path + ".tmp"):
                os.remove(path + ".tmp")
        except Exception:
            pass

        raise


def find_article_with_photo(articles):
    if not ALLOW_SOURCE_IMAGES:
        raise RuntimeError(
            "ALLOW_SOURCE_IMAGES=0 भएकाले स्रोत फोटो प्रयोग बन्द छ।"
        )

    checked = 0

    for article in articles:
        if checked >= MAX_ARTICLES_TO_CHECK:
            break

        checked += 1

        print("\nChecking article:", article["title"])
        print("Source:", article["source"])

        photos = article.get("images", [])

        if not photos:
            print("No image candidates found.")
            continue

        for index, photo_url in enumerate(photos, start=1):
            print(
                f"Testing image {index}/{len(photos)}:",
                photo_url[:160]
            )

            try:
                path = f"{OUT}/article.jpg"

                result = download_article_image(
                    photo_url,
                    path
                )

                if result:
                    article["image"] = photo_url

                    print("VALID ARTICLE PHOTO FOUND!")
                    print("Selected article:", article["title"])

                    return article, result

            except Exception as e:
                print(
                    "Image rejected:",
                    str(e)[:160]
                )

    return None, None


# --------------------------------------------------
# GEMINI SCRIPT
# --------------------------------------------------

PROMPT = """तपाईं नेपाली समाचारका छोटा Facebook Reel का स्क्रिप्ट लेखक हुनुहुन्छ।

तलको समाचारका तथ्य मात्र प्रयोग गर्नुहोस्।
कुनै नाम, मिति, संख्या, कारण वा उद्धरण नबनाउनुहोस्।

समाचारको मुख्य कुरा नछुटाई सकेसम्म छोटो बनाउनुहोस्।

२० सेकेन्डमा भन्न मिल्ने ३०-४० नेपाली शब्द प्राथमिकता हो।
धेरै महत्वपूर्ण तथ्य छुट्ने भए अधिकतम ६५ शब्दमा ४० सेकेन्डको स्क्रिप्ट दिनुहोस्।

headline: अधिकतम ६-८ शब्द, छोटो र स्पष्ट।
voice: स्वाभाविक नेपाली देवनागरी, समाचार वाचक शैली।
अनावश्यक सुरुवात, अन्त्य र अभिवादन नराख्ने।
keywords: दृश्य खोज्नका लागि २-३ English शब्द।
caption: एउटा छोटो नेपाली वाक्य र २-३ hashtag।

JSON मा यी key मात्र फर्काउनुहोस्:
headline, voice, keywords, caption.

"""


def make_script(article):
    prompt = (
        PROMPT
        + "\nस्रोत: " + article["source"]
        + "\nशीर्षक: " + article["title"]
        + "\nविवरण: " + article["text"][:6500]
    )

    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_KEY},
        json={
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.25
            }
        },
        timeout=90
    )

    response.raise_for_status()

    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]

    data = json.loads(raw)

    data["headline"] = clean(data.get("headline"))[:100]
    data["voice"] = clean(data.get("voice"))
    data["caption"] = clean(data.get("caption"))
    data["keywords"] = clean(
        data.get("keywords", "Nepal news")
    )

    if not data["voice"] or not data["headline"]:
        raise ValueError("Gemini returned incomplete script")

    return data


# --------------------------------------------------
# NEPALI VOICE
# --------------------------------------------------

def tts(text, path):
    async def run():
        await edge_tts.Communicate(
            text,
            VOICE,
            rate="+5%",
            pitch="+0Hz"
        ).save(path)

    asyncio.run(run())


# --------------------------------------------------
# PIXABAY BACKGROUND VIDEO
# --------------------------------------------------

def download(url, path):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60
    )

    response.raise_for_status()

    if len(response.content) < 10000:
        raise ValueError("Downloaded video too small")

    with open(path, "wb") as f:
        f.write(response.content)

    return path


def pixabay_clip(keywords, path):
    if not PIXABAY_KEY:
        return None

    try:
        response = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_KEY,
                "q": keywords,
                "per_page": 12,
                "safesearch": "true"
            },
            timeout=30
        )

        response.raise_for_status()

        hits = [
            h for h in response.json().get("hits", [])
            if h.get("duration", 0) >= 6
        ]

        if not hits:
            return None

        videos = random.choice(hits)["videos"]

        video = (
            videos.get("medium")
            or videos.get("small")
            or videos.get("tiny")
        )

        return download(video["url"], path) if video else None

    except Exception as e:
        print("Pixabay clip skipped:", str(e)[:160])
        return None


# --------------------------------------------------
# FACEBOOK REEL PUBLISHING
# --------------------------------------------------

def upload_reel(path, caption):
    print("\nStarting Facebook Reel upload...")

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

    with open(path, "rb") as f:
        upload = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {TOKEN}",
                "offset": "0",
                "file_size": str(size)
            },
            data=f,
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

    return video_id


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    os.makedirs(OUT, exist_ok=True)

    print("=" * 55)
    print("HAMRO CHITWAN NEWS REELS")
    print("=" * 55)

    done = load_state()

    # Step 1: Discover news
    candidates = discover_news()

    print("\nTotal articles discovered:", len(candidates))

    fresh = [
        article
        for article in candidates
        if article["key"] not in done
    ]

    if not fresh:
        print("No new article found. Skipping this run.")
        return

    print("New articles:", len(fresh))

    # Step 2: Find a usable source image.
    article, photo_path = find_article_with_photo(fresh)

    if not article or not photo_path:
        raise RuntimeError(
            "Naya Patrika र Janaastha का समाचार जाँच गरियो, "
            "तर प्रयोग गर्न मिल्ने वैध समाचार फोटो भेटिएन। "
            "गलत Logo वा placeholder प्रयोग गरिएको छैन।"
        )

    print("\nSELECTED NEWS:")
    print(article["source"])
    print(article["title"])
    print(article["url"])

    # Step 3: Generate Nepali script
    script = make_script(article)

    print("\nGENERATED SCRIPT:")
    print(json.dumps(script, ensure_ascii=False, indent=2))

    # Step 4: Generate Nepali voice
    voice_path = f"{OUT}/voice.mp3"

    tts(
        script["voice"],
        voice_path
    )

    # Step 5: Optional background footage
    clip = pixabay_clip(
        script.get("keywords", "Nepal news"),
        f"{OUT}/bg.mp4"
    )

    # Step 6: Render Reel
    video = render(
        photo_path,
        voice_path,
        script["headline"],
        f"{OUT}/reel.mp4",
        bg_video=clip,
        logo_candidates=[
            "assets/file_000000008d8c8208b201454c7871862d.png",
            "file_000000008d8c8208b201454c7871862d.png",
            "assets/hamro_chitwan.png"
        ]
    )

    if not video or not os.path.exists(video):
        raise RuntimeError("Reel video निर्माण हुन सकेन।")

    print("\nREEL CREATED:", video)

    if DRY_RUN:
        print("DRY_RUN: Video created, Facebook upload skipped.")
        return

    # Step 7: Publish Facebook Reel
    video_id = upload_reel(
        video,
        script.get("caption", "")
    )

    print("\nFACEBOOK REEL PUBLISHED:", video_id)

    # Save history only after successful publishing
    done.add(article["key"])
    save_state(done)

    print("Article history saved.")
    print("=" * 55)
    print("AUTOMATION COMPLETED SUCCESSFULLY")
    print("=" * 55)


if __name__ == "__main__":
    main()
