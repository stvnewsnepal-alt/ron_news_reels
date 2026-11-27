import asyncio, re
from pathlib import Path
import edge_tts
from bs4 import BeautifulSoup

VOICE="ne-NP-SagarNeural"  # Microsoft Edge TTS Nepali male voice; availability can vary.
OUT=Path("output/voice.mp3"); OUT.parent.mkdir(exist_ok=True)

def script_for(story):
    title=story["title"].strip()
    summary=BeautifulSoup(story.get("summary",""),"html.parser").get_text(" ",strip=True)
    summary=re.sub(r"\s+"," ",summary)
    # Do not fabricate facts. If the RSS summary is too short, speak a short intro and headline.
    if len(summary)<80:
        body=f"{title}। यस समाचारको विस्तृत विवरणका लागि हाम्रो वेबसाइटमा उपलब्ध समाचार पढ्नुहोस्।"
    else:
        body=f"{title}। {summary[:1100]}"
    return f"नमस्कार। हाम्रा चितवन राम्रा चितवन समाचारमा स्वागत छ। {body}। थप जानकारीका लागि समाचारको मूल स्रोत हेर्नुहोस्।"

async def _make(text):
    communicate=edge_tts.Communicate(text,VOICE,rate="-4%",volume="+0%")
    await communicate.save(str(OUT))

def make_voice(story):
    asyncio.run(_make(script_for(story)))
    return str(OUT)
