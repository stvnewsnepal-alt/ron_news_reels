import feedparser, requests, re, logging
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# RSS feeds are preferred over scraping pages. Verify feed URLs before production;
# publishers can change or restrict them without notice.
FEEDS=[
    ("Onlinekhabar","https://www.onlinekhabar.com/feed"),
    ("eKantipur","https://ekantipur.com/rss"),
]
HEADERS={"User-Agent":"HamroChitwanNewsBot/1.0 (contact: page administrator)"}

def _date(entry):
    for key in ("published","updated","created"):
        val=entry.get(key)
        if val:
            try:
                dt=parsedate_to_datetime(val)
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)

def _clean(html):
    return re.sub(r"\s+"," ",BeautifulSoup(html or "","html.parser").get_text(" ",strip=True))

def fetch_latest():
    items=[]
    for source,url in FEEDS:
        try:
            response=requests.get(url,headers=HEADERS,timeout=25)
            response.raise_for_status()
            parsed=feedparser.parse(response.content)
            for e in parsed.entries[:40]:
                link=e.get("link","").strip()
                title=_clean(e.get("title",""))
                summary=_clean(e.get("summary","") or e.get("description",""))
                if not link or not title: continue
                items.append({
                    "source":source,"title":title,"summary":summary[:2500],
                    "url":link,"published":_date(e).isoformat(),
                    "image":_feed_image(e)
                })
        except Exception as exc:
            logging.warning("Feed failed %s: %s",source,exc)
    return items

def _feed_image(entry):
    for media in entry.get("media_content",[]) or []:
        if media.get("url"): return media["url"]
    for media in entry.get("media_thumbnail",[]) or []:
        if media.get("url"): return media["url"]
    for link in entry.get("links",[]) or []:
        if link.get("type","").startswith("image/") and link.get("href"):
            return link["href"]
    html=entry.get("summary","") or entry.get("description","")
    img=BeautifulSoup(html,"html.parser").find("img")
    return img.get("src") if img else ""
