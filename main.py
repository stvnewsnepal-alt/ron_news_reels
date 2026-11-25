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
VOICE = os.environ.get("TTS_VOICE", "ne-NP-SagarNeural")  # or ne-NP-HemkalaNeural
UPLOAD_MODE = os.environ.get("UPLOAD_MODE", "video")  # "video" = 4:5 feed video, "reel" = 9:16 only
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
        if "video" in types:  # skip our own reels / video posts
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
        await edge_tts.Communicate(text, VOICE, rate="+10%", pitch="+3Hz", volume="+10%")
