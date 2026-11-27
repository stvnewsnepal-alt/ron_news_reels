import json, re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from config import MAX_STORY_AGE_HOURS
HISTORY=Path("history.json")
BLOCK=re.compile(r"राशिफल|ज्योतिष|विज्ञापन|प्रायोजित|मनोरञ्जन|फोटोफिचर|भिडियो$",re.I)

def _history():
    try: return json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception: return {"urls":[]}

def choose_story(stories):
    now=datetime.now(timezone.utc)
    hist=set(_history().get("urls",[]))
    eligible=[]
    for s in stories:
        if s["url"] in hist or BLOCK.search(s["title"]): continue
        try: dt=datetime.fromisoformat(s["published"].replace("Z","+00:00"))
        except Exception: continue
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        age=(now-dt.astimezone(timezone.utc)).total_seconds()/3600
        if age<0 or age>MAX_STORY_AGE_HOURS: continue
        # Favor stories with a useful description and a title, while keeping newest first.
        s["_score"]=max(0,100-age*3)+min(len(s.get("summary","")),1000)/100
        eligible.append(s)
    if not eligible: return None
    eligible.sort(key=lambda x:(x["_score"],x["published"]),reverse=True)
    return eligible[0]

def mark_published(story):
    h=_history()
    urls=h.get("urls",[])
    urls.append(story["url"])
    h["urls"]=urls[-1000:]
    HISTORY.write_text(json.dumps(h,ensure_ascii=False,indent=2),encoding="utf-8")
