"""1080x1350 Reel renderer with source photo credit and related Pixabay cutaway."""
import os, glob, subprocess
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
    raise RuntimeError("उपयुक्त font भेटिएन")


def get_font(size):
    layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    return ImageFont.truetype(find_font(), size, layout_engine=layout)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def make_text_png(text, path, size, max_width, fill=(255,255,255,255),
                  bg=(0,0,0,0), padding=20, align="center"):
    font = get_font(size)
    tmp = Image.new("RGBA", (max_width, 500), (0,0,0,0))
    draw = ImageDraw.Draw(tmp)
    words, lines, current = str(text).split(), [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width - 2*padding:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    line_h = int(size * 1.38)
    height = max(line_h + padding*2, line_h*len(lines) + padding*2)
    img = Image.new("RGBA", (max_width, height), bg)
    dr = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        box = dr.textbbox((0,0), line, font=font, stroke_width=2)
        tw = box[2]-box[0]
        x = (max_width-tw)/2 if align == "center" else padding
        dr.text((x, padding+i*line_h-box[1]), line, font=font, fill=fill,
                stroke_width=2, stroke_fill=(0,0,0,210))
    img.save(path)
    return height


def prepare_photo(photo, path):
    im = Image.open(photo).convert("RGB")
    # Do not crop source branding. Fit the complete source photo in the canvas.
    im.thumbnail((W, H), Image.Resampling.LANCZOS)
    bg = ImageOps.fit(im, (W, H), method=Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(28)).point(lambda p: int(p * 0.62))
    x = (W-im.width)//2
    y = (H-im.height)//2
    bg.paste(im, (x,y))
    bg.save(path, quality=94)


def render(photo, audio, headline, out, bg_video=None, logo_candidates=None,
           source_credit="News source", extra_image=None):
    work = "out/render"
    os.makedirs(work, exist_ok=True)
    audio_len = duration(audio)
    dur = max(20.0, min(audio_len + 0.9, 40.0))

    still = os.path.join(work, "still.jpg")
    prepare_photo(photo, still)

    head = os.path.join(work, "headline.png")
    make_text_png(headline, head, 56, 1000, fill=(255,255,255,255),
                  bg=(5,18,55,225), padding=22)

    url_png = os.path.join(work, "page_url.png")
    make_text_png("fb.com/ilovechitwan", url_png, 38, 650,
                  fill=(255,225,35,255), bg=(3,12,42,205), padding=18)

    credit_png = os.path.join(work, "photo_credit.png")
    make_text_png("Photo Credit: " + source_credit, credit_png, 24, 700,
                  fill=(255,255,255,255), bg=(0,0,0,170), padding=12, align="center")

    inputs, filters = [], []
    def add(args):
        inputs.extend(args)
        return inputs.count("-i") - 1

    ib = add(["-loop", "1", "-t", str(dur), "-i", still])
    filters.append(f"[{ib}:v]fps={FPS},scale={W}:{H},zoompan=z='min(zoom+0.00035,1.08)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},setsar=1[base]")

    # Related Pixabay video appears as a clearly visible cutaway card, never replacing
    # the article photo. If absent, the original article photo remains throughout.
    last = "base"
    if bg_video and os.path.exists(bg_video):
        iv = add(["-stream_loop", "-1", "-t", str(dur), "-i", bg_video])
        filters.append(f"[{iv}:v]fps={FPS},scale=500:620:force_original_aspect_ratio=increase,crop=500:620,setsar=1,drawbox=x=0:y=0:w=iw:h=ih:color=white@0.9:t=5[related]")
        filters.append(f"[{last}][related]overlay=x=560:y=590:enable='between(t,8,15)'[vcut]")
        last = "vcut"
    elif extra_image and os.path.exists(extra_image):
        ii = add(["-loop", "1", "-t", str(dur), "-i", extra_image])
        filters.append(f"[{ii}:v]scale=500:620:force_original_aspect_ratio=increase,crop=500:620,setsar=1,drawbox=x=0:y=0:w=iw:h=ih:color=white@0.9:t=5[related]")
        filters.append(f"[{last}][related]overlay=x=560:y=590:enable='between(t,8,15)'[vcut]")
        last = "vcut"

    ih = add(["-loop", "1", "-t", str(dur), "-i", head])
    filters.append(f"[{ih}:v]format=rgba,fade=t=out:st=3.2:d=0.4:alpha=1[head]")
    filters.append(f"[{last}][head]overlay=(W-w)/2:65:enable='lt(t,3.7)'[vhead]")
    last = "vhead"

    iu = add(["-loop", "1", "-t", str(dur), "-i", url_png])
    filters.append(f"[{iu}:v]format=rgba,fade=t=in:st=5:d=0.45:alpha=1,fade=t=out:st=9:d=0.45:alpha=1[url]")
    filters.append(f"[{last}][url]overlay=x='(W-w)/2+20*sin(t*3)':y='H*0.48':enable='between(t,5,9)'[vurl]")
    last = "vurl"

    ic = add(["-loop", "1", "-t", str(dur), "-i", credit_png])
    filters.append(f"[{ic}:v]format=rgba[credit]")
    filters.append(f"[{last}][credit]overlay=(W-w)/2:H-h-28:enable='gte(t,0)'[vcredit]")
    last = "vcredit"

    ia = add(["-i", audio])
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters) + f";[{last}]format=yuv420p[vout]",
        "-map", "[vout]", "-map", f"{ia}:a", "-t", str(dur), "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", out
    ]
    subprocess.run(cmd, check=True)
    return out


# Keep the import name available for Pillow versions where ImageOps is used by the
# photo preparation helper.
from PIL import ImageOps
