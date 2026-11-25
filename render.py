"""Render a clean 1080x1350 news Reel with a brief headline and animated page URL."""
import os, glob, subprocess, math
from PIL import Image, ImageDraw, ImageFont, ImageFilter, features

W, H, FPS = 1080, 1350, 25

def find_font():
    env = os.environ.get("FONT_PATH")
    if env and os.path.exists(env):
        return env
    try:
        p = subprocess.run(["fc-match", "Noto Sans Devanagari:bold", "-f", "%{file}"],
                           capture_output=True, text=True, check=False).stdout.strip()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    for p in glob.glob("/usr/share/fonts/**/*.ttf", recursive=True):
        if "DejaVuSans" in p:
            return p
    raise RuntimeError("No usable font found")

def get_font(size):
    layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    return ImageFont.truetype(find_font(), size, layout_engine=layout)

def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())

def make_text_png(text, path, size, max_width, fill=(255,255,255,255), bg=(0,0,0,0), padding=20):
    f = get_font(size)
    temp = Image.new("RGBA", (max_width, 300), (0,0,0,0))
    d = ImageDraw.Draw(temp)
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if d.textlength(trial, font=f) <= max_width - 2*padding:
            current = trial
        else:
            if current: lines.append(current)
            current = word
    if current: lines.append(current)
    line_h = int(size*1.35)
    h = line_h*len(lines) + padding*2
    img = Image.new("RGBA", (max_width,h), bg)
    dr = ImageDraw.Draw(img)
    for i,line in enumerate(lines):
        box = dr.textbbox((0,0),line,font=f,stroke_width=2)
        tw = box[2]-box[0]
        dr.text(((max_width-tw)/2,padding+i*line_h-box[1]),line,font=f,
                fill=fill,stroke_width=2,stroke_fill=(0,0,0,190))
    img.save(path)
    return h

def find_logo(candidates):
    for p in candidates or []:
        if os.path.exists(p):
            return p
    # Also search repository root and assets for the uploaded logo.
    matches = glob.glob("**/file_000000008d8c8208b201454c7871862d.png", recursive=True)
    matches += glob.glob("assets/*hamro*chitwan*.png")
    return matches[0] if matches else None

def render(photo, audio, headline, out, bg_video=None, logo_candidates=None):
    work = "out/render"
    os.makedirs(work, exist_ok=True)
    audio_len = duration(audio)
    # Default ~20 sec; allow up to 40 sec only when the voice needs it.
    dur = max(12.0, min(audio_len + 0.8, 40.0))
    # Use the supplied article photo as the main visual.
    im = Image.open(photo).convert("RGB")
    im.thumbnail((W,H))
    still = os.path.join(work,"still.jpg")
    im.save(still, quality=94)
    # Headline appears only briefly at the beginning.
    head = os.path.join(work,"headline.png")
    make_text_png(headline, head, 58, 1000, fill=(255,255,255,255),
                  bg=(5,18,55,225), padding=24)
    # Create a compact semi-transparent page URL banner.
    url_png = os.path.join(work,"page_url.png")
    make_text_png("fb.com/ilovechitwan", url_png, 34, 600,
                  fill=(255,225,35,255), bg=(3,12,42,185), padding=16)
    logo = find_logo(logo_candidates)
    inputs, filters = [], []
    def add(args):
        inputs.extend(args)
        return inputs.count("-i") - 1
    if bg_video and os.path.exists(bg_video):
        ib = add(["-stream_loop","-1","-t",str(dur),"-i",bg_video])
        filters.append(f"[{ib}:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,eq=brightness=-0.28,boxblur=2[bg]")
    else:
        ib = add(["-loop","1","-t",str(dur),"-i",still])
        filters.append(f"[{ib}:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=34,eq=brightness=-0.38,setsar=1[bg]")
    ip = add(["-loop","1","-t",str(dur),"-i",still])
    filters.append(f"[{ip}:v]fps={FPS},scale=980:1220:force_original_aspect_ratio=decrease,pad=980:1220:(ow-iw)/2:(oh-ih)/2:color=black@0,zoompan=z='min(zoom+0.00045,1.10)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=980x1220:fps={FPS},format=rgba[photo]")
    filters.append("[bg][photo]overlay=(W-w)/2:(H-h)/2:format=auto[v1]")
    last = "v1"
    ih = add(["-loop","1","-t",str(dur),"-i",head])
    filters.append(f"[{ih}:v]format=rgba,fade=t=out:st=3.0:d=0.45:alpha=1[head]")
    filters.append(f"[{last}][head]overlay=(W-w)/2:70:enable='lt(t,3.5)'[v2]")
    last = "v2"
    iu = add(["-loop","1","-t",str(dur),"-i",url_png])
    filters.append(f"[{iu}:v]format=rgba,fade=t=in:st=5.0:d=0.5:alpha=1,fade=t=out:st=8.5:d=0.5:alpha=1[url]")
    filters.append(f"[{last}][url]overlay=x='(W-w)/2+18*sin(t*3)':y='H*0.52':enable='between(t,5,9)'[v3]")
    last = "v3"
    if logo:
        il = add(["-loop","1","-t",str(dur),"-i",logo])
        filters.append(f"[{il}:v]scale=250:-1,format=rgba,colorchannelmixer=aa=0.92[logo]")
        filters.append(f"[{last}][logo]overlay=(W-w)/2:H-h-28:format=auto[v4]")
        last = "v4"
    ia = add(["-i",audio])
    cmd = ["ffmpeg","-y","-v","error",*inputs,"-filter_complex",";".join(filters)+f";[{last}]format=yuv420p[vout]",
           "-map","[vout]","-map",f"{ia}:a","-t",str(dur),"-r",str(FPS),
           "-c:v","libx264","-preset","veryfast","-crf","22","-pix_fmt","yuv420p",
           "-c:a","aac","-b:a","128k","-movflags","+faststart",out]
    subprocess.run(cmd,check=True)
    return out
