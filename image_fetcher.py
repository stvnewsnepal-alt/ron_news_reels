import os, requests, logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from pathlib import Path
from config import PIXABAY_API_KEY

OUT=Path("output/visuals"); OUT.mkdir(parents=True,exist_ok=True)
UA={"User-Agent":"Mozilla/5.0 (compatible; HamroChitwanNews/1.0)"}

def _download(url,name):
    if not url or not url.startswith("https://"): return None
    try:
        r=requests.get(url,headers=UA,timeout=25,stream=True)
        r.raise_for_status()
        ctype=r.headers.get("content-type","")
        if not ctype.startswith("image/"): return None
        data=r.content
        if len(data)>15_000_000 or len(data)<10_000: return None
        ext=".jpg"
        if "png" in ctype: ext=".png"
        p=OUT/(name+ext); p.write_bytes(data)
        return str(p)
    except Exception as e:
        logging.warning("Image download failed: %s",e); return None

def _article_image(story):
    try:
        soup=BeautifulSoup(requests.get(story["url"],headers=UA,timeout=20).text,"html.parser")
        for sel in [('meta',{'property':'og:image'}),('meta',{'name':'twitter:image'})]:
            tag=soup.find(*sel)
            if tag and tag.get("content"):
                return urljoin(story["url"],tag["content"])
    except Exception as e: logging.warning("Article image lookup failed: %s",e)
    return story.get("image","")

def _pixabay(query):
    if not PIXABAY_API_KEY: return []
    try:
        r=requests.get("https://pixabay.com/api/",params={
            "key":PIXABAY_API_KEY,"q":query,"image_type":"photo",
            "safesearch":"true","per_page":8,"orientation":"horizontal"
        },timeout=20)
        r.raise_for_status()
        hits=r.json().get("hits",[])
        # Pixabay API terms require attribution and compliance with its license.
        return [h.get("largeImageURL") for h in hits if h.get("largeImageURL")]
    except Exception as e:
        logging.warning("Pixabay lookup failed: %s",e); return []

def collect_visuals(story):
    paths=[]
    article=_article_image(story)
    p=_download(article,"article")
    if p: paths.append(p)
    # Pixabay images are supplementary only; user should review relevance/licensing.
    query=" ".join(story["title"].split()[:5])
    for i,url in enumerate(_pixabay(query)[:4]):
        p=_download(url,f"pixabay_{i}")
        if p: paths.append(p)
    return paths
