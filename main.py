import os, re, json, time, hashlib, mimetypes, subprocess, tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from render import render

TZ = ZoneInfo('Asia/Kathmandu')
MAX_AGE_HOURS = 12
OUT_DIR = 'work'
os.makedirs(OUT_DIR, exist_ok=True)
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; HamroChitwanNewsBot/1.0)'}
SOURCES = [
    ('Naya Patrika', 'https://nayapatrikadaily.com/'),
    ('Janaastha', 'https://www.janaastha.com/'),
]


def log(*a): print('[HAMRO REELS]', *a, flush=True)


def parse_date(value):
    if not value: return None
    value = str(value).strip()
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception: pass
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S'):
        try: return datetime.strptime(value, fmt).replace(tzinfo=TZ).astimezone(timezone.utc)
        except Exception: pass
    return None


def meta_date(soup):
    for key in ('article:published_time','datePublished','pubdate','publishdate','date'):
        tag = soup.find('meta', attrs={'property':key}) or soup.find('meta', attrs={'name':key})
        if tag and tag.get('content'):
            dt = parse_date(tag['content'])
            if dt: return dt
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or tag.get_text())
            stack = data if isinstance(data,list) else [data]
            while stack:
                obj = stack.pop()
                if isinstance(obj,dict):
                    if obj.get('datePublished'):
                        dt = parse_date(obj['datePublished'])
                        if dt: return dt
                    stack.extend(v for v in obj.values() if isinstance(v,(dict,list)))
                elif isinstance(obj,list): stack.extend(obj)
        except Exception: continue
    return None


def image_candidates(soup, base):
    urls=[]
    og=soup.find('meta',property='og:image')
    if og and og.get('content'): urls.append(urljoin(base,og['content']))
    tw=soup.find('meta',attrs={'name':'twitter:image'})
    if tw and tw.get('content'): urls.append(urljoin(base,tw['content']))
    for img in soup.find_all('img'):
        src=img.get('src') or img.get('data-src') or img.get('data-original') or img.get('data-lazy-src')
        if src: urls.append(urljoin(base,src))
    out=[]; seen=set()
    for u in urls:
        if u.startswith('http') and u not in seen:
            seen.add(u); out.append(u)
    return out


def usable_photo(url, dest):
    try:
        r=requests.get(url,headers=HEADERS,timeout=20,stream=True); r.raise_for_status()
        ct=r.headers.get('content-type','').lower()
        if not ct.startswith('image/'): return None
        data=r.content
        if len(data)<12000: return None
        from PIL import Image
        p=dest
        with open(p,'wb') as f: f.write(data)
        im=Image.open(p); w,h=im.size
        if w<400 or h<250 or w/h>3.5 or h/w>3.5: return None
        im.verify()
        return p
    except Exception: return None


def discover_links(source_url):
    try:
        r=requests.get(source_url,headers=HEADERS,timeout=20); r.raise_for_status()
        soup=BeautifulSoup(r.text,'html.parser')
        links=[]
        host=urlparse(source_url).netloc.replace('www.','')
        for a in soup.find_all('a',href=True):
            u=urljoin(source_url,a['href']).split('#')[0]
            if urlparse(u).netloc.replace('www.','') != host: continue
            if not re.search(r'\.(html?|php)(\?|$)|/\d{4}/\d{2}/',u): continue
            title=' '.join(a.get_text(' ',strip=True).split())
            if len(title)<18: continue
            if u not in [x[0] for x in links]: links.append((u,title))
        return links[:70]
    except Exception as e:
        log('Homepage fetch failed:',source_url,e); return []


def inspect_article(source, url, listing_title):
    try:
        r=requests.get(url,headers=HEADERS,timeout=25); r.raise_for_status()
        soup=BeautifulSoup(r.text,'html.parser')
        published=meta_date(soup)
        if not published: return None
        now=datetime.now(timezone.utc)
        age=(now-published).total_seconds()/3600
        if age<0 or age>MAX_AGE_HOURS: return None
        title=''
        for sel in ['h1','meta[property="og:title"]','meta[name="twitter:title"]']:
            el=soup.select_one(sel)
            if el:
                title=el.get('content','') if el.name=='meta' else el.get_text(' ',strip=True)
                if title: break
        title=title or listing_title
        article=soup.find('article') or soup.find('main') or soup.body
        paragraphs=[]
        if article:
            for p in article.find_all('p'):
                t=' '.join(p.get_text(' ',strip=True).split())
                if len(t)>35 and t not in paragraphs: paragraphs.append(t)
        body=' '.join(paragraphs)
        if len(body)<100: body=' '.join(soup.get_text(' ',strip=True).split())
        body=body[:9000]
        if len(body)<120: return None
        return {'source':source,'url':url,'title':title,'body':body,'published':published,'age':age,'images':image_candidates(soup,url)}
    except Exception as e:
        log('Article parse failed:',url,e); return None


def collect_latest():
    found=[]
    for source,home in SOURCES:
        log('Checking',source)
        for url,title in discover_links(home):
            item=inspect_article(source,url,title)
            if item: found.append(item)
    found.sort(key=lambda x:x['published'],reverse=True)
    return found[0] if found else None


def gemini_script(article):
    client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    prompt=f'''तपाईं नेपाली भाषाका अनुभवी समाचार वाचकका लागि स्क्रिप्ट लेखक हुनुहुन्छ। तलको समाचारलाई तथ्य नबदलिकन २० देखि ४० सेकेन्डभित्र स्पष्ट, स्वाभाविक र व्यावसायिक नेपालीमा वाचन गर्न मिल्ने बनाउनुहोस्। मुख्य तथ्य, नाम, स्थान र संख्याहरू सुरक्षित राख्नुहोस्। कुनै नयाँ तथ्य नथप्नुहोस्। अभिवादन वा अनावश्यक भूमिका नराख्नुहोस्। ५० देखि ८० नेपाली शब्दभित्र राख्नुहोस्। केवल वाचन स्क्रिप्ट दिनुहोस्।\nशीर्षक: {article['title']}\nसमाचार: {article['body']}'''
    response=client.models.generate_content(model=os.getenv('GEMINI_TEXT_MODEL','gemini-2.5-flash'),contents=prompt)
    text=(response.text or '').strip()
    if not text: raise RuntimeError('Gemini returned an empty narration script')
    return text


def write_wav(path, pcm, sample_rate=24000, channels=1, sample_width=2):
    import wave
    with wave.open(path,'wb') as wf:
        wf.setnchannels(channels); wf.setsampwidth(sample_width); wf.setframerate(sample_rate); wf.writeframes(pcm)


def gemini_tts(script, out_path):
    client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    model=os.getenv('GEMINI_TTS_MODEL','gemini-2.5-flash-preview-tts')
    voice=os.getenv('TTS_VOICE','Cruz')
    chunks=[]; mime='audio/pcm;rate=24000'
    config=types.GenerateContentConfig(
        response_modalities=['AUDIO'],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        )
    )
    for response in client.models.generate_content_stream(model=model,contents=script,config=config):
        if not response.candidates: continue
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                chunks.append(part.inline_data.data)
                mime=part.inline_data.mime_type or mime
    if not chunks: raise RuntimeError(f'Gemini TTS returned no audio (model={model}, voice={voice})')
    audio=b''.join(chunks)
    # Gemini TTS normally returns 24 kHz, 16-bit mono PCM.
    if 'wav' in mime.lower():
        with open(out_path,'wb') as f: f.write(audio)
    else:
        rate_match=re.search(r'rate=(\d+)',mime)
        rate=int(rate_match.group(1)) if rate_match else 24000
        write_wav(out_path,audio,rate)
    return out_path


def pixabay_assets(query, count=2):
    key=os.getenv('PIXABAY_API_KEY')
    if not key: return []
    terms=re.sub(r'[^a-zA-Z0-9 ]',' ',query).strip()[:100]
    if not terms: return []
    assets=[]
    try:
        r=requests.get('https://pixabay.com/api/',params={'key':key,'q':terms,'image_type':'photo','safesearch':'true','per_page':20,'min_width':700},timeout=20); r.raise_for_status()
        hits=r.json().get('hits',[])
        for h in hits:
            u=h.get('largeImageURL') or h.get('webformatURL')
            if u: assets.append({'url':u,'kind':'image','tags':h.get('tags','')})
        rv=requests.get('https://pixabay.com/api/videos/',params={'key':key,'q':terms,'safesearch':'true','per_page':15},timeout=20); rv.raise_for_status()
        for h in rv.json().get('hits',[]):
            videos=h.get('videos',{})
            choice=videos.get('large') or videos.get('medium') or videos.get('small')
            if choice and choice.get('url'): assets.append({'url':choice['url'],'kind':'video','tags':h.get('tags','')})
    except Exception as e: log('Pixabay lookup failed:',e)
    # rank against meaningful words in the query; only retain reasonably related results
    words={w.lower() for w in re.findall(r'[a-zA-Z]{4,}',terms)}
    scored=[]
    for a in assets:
        tags={w.lower() for w in re.findall(r'[a-zA-Z]{3,}',a.get('tags',''))}
        score=len(words & tags)
        if score>=1: scored.append((score,a))
    scored.sort(key=lambda x:x[0],reverse=True)
    return [a for _,a in scored[:count]]


def download_asset(asset, index):
    ext='.mp4' if asset['kind']=='video' else '.jpg'
    path=os.path.join(OUT_DIR,f'extra_{index}{ext}')
    try:
        r=requests.get(asset['url'],headers=HEADERS,timeout=35); r.raise_for_status()
        if len(r.content)<15000: return None
        with open(path,'wb') as f: f.write(r.content)
        if asset['kind']=='image':
            from PIL import Image
            im=Image.open(path); im.verify()
        else:
            probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',path],capture_output=True,text=True)
            if probe.returncode: return None
        return {'path':path,'kind':asset['kind']}
    except Exception as e: log('Asset download failed:',e); return None


def main():
    if os.getenv('DRY_RUN','0')=='1': log('DRY RUN enabled: will render but will not publish')
    history_path='posted.json'
    try:
        history=json.load(open(history_path,encoding='utf-8'))
        if not isinstance(history,list): history=[]
    except Exception: history=[]
    article=collect_latest()
    if not article:
        log('No article with a verified publication timestamp within the last 12 hours. Stop.'); return
    digest=hashlib.sha256(article['url'].encode()).hexdigest()
    if digest in history:
        log('Latest eligible story already posted. Stop.'); return
    log('Selected:',article['source'],article['title'],'age_hours=',round(article['age'],2))
    main_photo=None
    for i,u in enumerate(article['images'][:12]):
        p=usable_photo(u,os.path.join(OUT_DIR,f'article_{i}.jpg'))
        if p: main_photo=p; break
    if not main_photo: raise RuntimeError('Could not retrieve a valid article photo; refusing to use unrelated stock')
    script=gemini_script(article)
    log('Narration:',script)
    audio=os.path.join(OUT_DIR,'narration.wav')
    gemini_tts(script,audio)
    # Search terms derived from the actual article, not generic stock footage.
    query_prompt=f'Give 2-4 concise English Pixabay search keywords that specifically describe this news story. Do not give generic words. Return only keywords.\n{article["title"]}\n{article["body"][:1200]}'
    client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    qr=client.models.generate_content(model=os.getenv('GEMINI_TEXT_MODEL','gemini-2.5-flash'),contents=query_prompt)
    query=(qr.text or article['title']).strip()
    extras=[]
    for idx,asset in enumerate(pixabay_assets(query,2),1):
        got=download_asset(asset,idx)
        if got: extras.append(got)
    logo=os.getenv('LOGO_PATH','file_000000008d8c8208b201454c7871862d.png')
    if not os.path.exists(logo):
        for candidate in ('assets/logo.png','logo.png'):
            if os.path.exists(candidate): logo=candidate; break
    if not os.path.exists(logo): raise RuntimeError('Hamro Chitwan logo file missing. Upload file_000000008d8c8208b201454c7871862d.png to repository root.')
    output=os.path.join(OUT_DIR,'daily_news.mp4')
    render(main_photo,audio,output,extras=extras,logo=logo)
    if os.getenv('DRY_RUN','0')=='1':
        log('DRY RUN complete. Video:',output); return
    publish(output,article['title'])
    history.append(digest); history=history[-500:]
    with open(history_path,'w',encoding='utf-8') as f: json.dump(history,f,ensure_ascii=False,indent=2)


def publish(video_path, title):
    page=os.environ['FB_PAGE_ID']; token=os.environ['FB_PAGE_TOKEN']
    base=f'https://graph.facebook.com/{os.getenv("FB_GRAPH_VERSION","v23.0")}/{page}/video_reels'
    start=requests.post(base, data={'upload_phase':'start','access_token':token},timeout=30); start.raise_for_status()
    data=start.json(); video_id=data['video_id']; upload_url=data.get('upload_url')
    if upload_url:
        with open(video_path,'rb') as f:
            up=requests.post(upload_url,headers={'Authorization':f'OAuth {token}','file_size':str(os.path.getsize(video_path)),'Content-Type':'application/octet-stream'},data=f,timeout=180)
            up.raise_for_status()
    else:
        with open(video_path,'rb') as f:
            up=requests.post(f'https://graph-video.facebook.com/{os.getenv("FB_GRAPH_VERSION","v23.0")}/{video_id}',headers={'Authorization':f'OAuth {token}'},files={'file':f},timeout=180)
            up.raise_for_status()
    caption=f'{title}\n\n#HamroChitwan #RamroChitwan #NepalNews'
    finish=requests.post(base,data={'upload_phase':'finish','video_id':video_id,'video_state':'PUBLISHED','description':caption,'access_token':token},timeout=60)
    finish.raise_for_status()
    log('Facebook Reel publish response:',finish.text)

if __name__=='__main__': main()
