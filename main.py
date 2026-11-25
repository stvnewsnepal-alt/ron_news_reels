"""Routine of Nepal Band: newest page post -> 30s Nepali script -> voice -> reel -> Facebook Reel."""
import os, sys, json, random, asyncio, requests
import edge_tts
from render import render

PAGE_ID = os.environ["FB_PAGE_ID"]
TOKEN = os.environ["FB_PAGE_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
GRAPH = "https://graph.facebook.com/" + os.environ.get("FB_API_VERSION", "v21.0")
VOICE = os.environ.get("TTS_VOICE", "ne-NP-SagarNeural")
UPLOAD_MODE = os.environ.get("UPLOAD_MODE", "video")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
STATE = "posted.json"
OUT = "out"


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return []


def save_state(ids):
    json.dump(ids[-500:], open(STATE, "w"))


def pick_post(done):
    r = requests.get(f"{GRAPH}/{PAGE_ID}/posts", params={
        "fields": "id,message,full_picture,created_time,attachments{media_type}",
        "limit": 25, "access_token": TOKEN}, timeout=60)
    r.raise_for_status()
    for p in r.json().get("data", []):
        types = [a.get("media_type") for a in p.get("attachments", {}).get("data", [])]
        if p["id"] in done or not p.get("message") or not p.get("full_picture"):
            continue
        if "video" in types:
            continue
        return p
    return None


PROMPT = """You write 30-second Nepali news reel scripts for the Facebook page "Routine of Nepal Band".
Below is the page's own post. It may mix English, Nepali (Devanagari) and Roman Nepali.
Use ONLY facts present in the post. Do not invent names, numbers, causes or quotes.
Return JSON with these keys:
- headline: Nepali (Devanagari), max 8 words, punchy
- points: list of exactly 3 short Nepali (Devanagari) key lines, max 6 words each
- voice: Nepali (Devanagari) voiceover, 60-75 words, energetic news-anchor tone, short sentences, starts with a hook, ends with a one-line closing
- keywords: 2 simple English words for a stock-video search (e.g. "road traffic")
- caption: 1-2 Nepali lines plus 3-5 hashtags
POST:
"""


def gen_script(text):
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_KEY},
        json={"contents": [{"parts": [{"text": PROMPT + text}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.6}},
        timeout=90)
    r.raise_for_status()
    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    d = json.loads(raw)
    d["points"] = [str(x) for x in d["points"]][:3]
    return d


def tts(text, path):
    async def go():
        await edge_tts.Communicate(text, VOICE, rate="+10%", pitch="+3Hz", volume="+10%").save(path)
    asyncio.run(go())


def get(url, path):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for c in r.iter_content(1 << 16):
                f.write(c)
    return path


def pixabay_clip(keywords, path):
    if not PIXABAY_KEY:
        return None
    try:
        r = requests.get("https://pixabay.com/api/videos/",
                         params={"key": PIXABAY_KEY, "q": keywords, "per_page": 15, "safesearch": "true"},
                         timeout=30)
        hits = [h for h in r.json().get("hits", []) if h.get("duration", 0) >= 8]
        if not hits:
            return None
        v = random.choice(hits)["videos"]
        url = (v.get("small") or v.get("medium") or v.get("tiny"))["url"]
        return get(url, path)
    except Exception as e:
        print("pixabay skipped:", e)
        return None


def post_video(path, caption):
    with open(path, "rb") as f:
        r = requests.post(f"{GRAPH}/{PAGE_ID}/videos", data={"description": caption, "access_token": TOKEN},
                          files={"source": f}, timeout=600)
    r.raise_for_status()
    return r.json().get("id")


def post_reel(path, caption):
    s = requests.post(f"{GRAPH}/{PAGE_ID}/video_reels",
                      data={"upload_phase": "start", "access_token": TOKEN}, timeout=60)
    s.raise_for_status()
    vid, up = s.json()["video_id"], s.json()["upload_url"]
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        u = requests.post(up, headers={"Authorization": f"OAuth {TOKEN}", "offset": "0",
                                       "file_size": str(size)}, data=f, timeout=600)
    u.raise_for_status()
    fin = requests.post(f"{GRAPH}/{PAGE_ID}/video_reels", data={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
        "description": caption, "access_token": TOKEN}, timeout=120)
    fin.raise_for_status()
    return vid


def main():
    os.makedirs(OUT, exist_ok=True)
    done = load_state()
    post = pick_post(done)
    if not post:
        print("No new post to turn into a reel.")
        return
    print("Using post", post["id"])
    photo = get(post["full_picture"], f"{OUT}/photo.jpg")
    s = gen_script(post["message"])
    print(json.dumps(s, ensure_ascii=False, indent=2))
    tts(s["voice"], f"{OUT}/voice.mp3")
    clip = pixabay_clip(s.get("keywords", "news"), f"{OUT}/bg.mp4")
    video = render(photo, f"{OUT}/voice.mp3", s["headline"], s["points"], f"{OUT}/reel.mp4",
                   bg_video=clip, work=f"{OUT}/work")
    if DRY_RUN:
        print("DRY_RUN: not uploading", video)
        return
    vid = (post_reel if UPLOAD_MODE == "reel" else post_video)(video, s.get("caption", ""))
    print("Posted", UPLOAD_MODE, vid)
    done.append(post["id"])
    save_state(done)


if __name__ == "__main__":
    main()
