"""Render a 1080x1350 (4:5) video with ffmpeg: blurred bg, animated photo, text layers, voice."""
import os, subprocess, glob
from PIL import Image, ImageDraw, ImageFont, features

W, H, FPS = 1080, 1350, 25
RED = (211, 47, 47, 255)


def find_font():
    if os.environ.get("FONT_PATH"):
        return os.environ["FONT_PATH"]
    try:
        out = subprocess.run(["fc-match", "Noto Sans Devanagari:bold", "-f", "%{file}"],
                             capture_output=True, text=True).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    for p in glob.glob("/usr/share/fonts/**/*.ttf", recursive=True):
        return p
    raise RuntimeError("No font found")


def headline_font_path():
    """Custom headline font: env HEADLINE_FONT or assets/headline_font.ttf (Unicode fonts only)."""
    p = os.environ.get("HEADLINE_FONT") or "assets/headline_font.ttf"
    return p if os.path.exists(p) else None


def font(size, path=None):
    eng = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    return ImageFont.truetype(path or find_font(), size, layout_engine=eng)


def wrap(draw, text, f, maxw):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if draw.textlength(t, font=f) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def text_png(text, path, size, width, fill=(255, 255, 255, 255), bg=(0, 0, 0, 170), pad=36, font_path=None):
    f = font(size, font_path)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = wrap(probe, text, f, width - 2 * pad)
    lh = int(size * 1.45)
    h = lh * len(lines) + 2 * pad
    img = Image.new("RGBA", (width, h), bg)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=f)
        d.text(((width - tw) / 2, pad + i * lh), line, font=f, fill=fill)
    img.save(path)
    return h


def audio_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True).stdout
    return float(out.strip())


def render(photo, audio, headline, points, out, bg_video=None, brand="Routine of Nepal Band", work="work"):
    os.makedirs(work, exist_ok=True)
    dur = min(audio_duration(audio) + 0.6, 59)

    # photo fitted into a 960x1000 card (even dims)
    im = Image.open(photo).convert("RGB")
    im.thumbnail((960, 620))
    pw, ph = im.size[0] // 2 * 2, im.size[1] // 2 * 2
    im = im.resize((pw, ph))
    pfit = f"{work}/photo.png"
    im.save(pfit)
    full = Image.open(photo).convert("RGB")
    full.save(f"{work}/bgphoto.jpg")

    head_png = f"{work}/head.png"
    text_png(headline, head_png, 70, W, bg=(211, 47, 47, 235), font_path=headline_font_path())
    brand_png = f"{work}/brand.png"
    text_png(brand, brand_png, 40, W, bg=(0, 0, 0, 200), pad=24)
    bar_png = f"{work}/bar.png"
    Image.new("RGBA", (W, 12), RED).save(bar_png)

    pts_png = []
    for i, p in enumerate(points[:3]):
        fp = f"{work}/pt{i}.png"
        text_png(p, fp, 44, 960, bg=(0, 0, 0, 190), pad=28, font_path=headline_font_path())
        pts_png.append(fp)

    inputs, fc = [], []

    def add(args):
        inputs.extend(args)
        return inputs.count("-i") - 1

    # 0: background
    if bg_video:
        i_bg = add(["-stream_loop", "-1", "-t", f"{dur}", "-i", bg_video])
        fc.append(f"[{i_bg}:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H},setsar=1,eq=brightness=-0.25,boxblur=3[bg]")
    else:
        i_bg = add(["-loop", "1", "-t", f"{dur}", "-i", f"{work}/bgphoto.jpg"])
        fc.append(f"[{i_bg}:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H},setsar=1,boxblur=40,eq=brightness=-0.3[bg]")

    # photo with slow zoom + fade in
    i_ph = add(["-loop", "1", "-t", f"{dur}", "-framerate", str(FPS), "-i", pfit])
    fc.append(f"[{i_ph}:v]scale=iw*2:ih*2,zoompan=z='min(1+0.0006*on,1.12)':d=1:"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={pw}x{ph}:fps={FPS},"
              f"format=rgba,fade=t=in:st=0.2:d=0.6:alpha=1[ph]")
    fc.append(f"[bg][ph]overlay=x=(W-w)/2:y=(H-h)/2+40:format=auto[v1]")
    last = "v1"

    # headline slides in from the left
    i_h = add(["-loop", "1", "-t", f"{dur}", "-i", head_png])
    fc.append(f"[{last}][{i_h}:v]overlay=x='-w*(1-min(t/0.5,1))':y=50:format=auto[v2]")
    last = "v2"

    # key points appear one by one
    n = max(len(pts_png), 1)
    seg = (dur - 3.0) / n
    for k, fp in enumerate(pts_png):
        a = 2.0 + k * seg
        b = a + seg
        i_p = add(["-loop", "1", "-t", f"{dur}", "-i", fp])
        fc.append(f"[{i_p}:v]format=rgba,fade=t=in:st={a:.2f}:d=0.4:alpha=1,"
                  f"fade=t=out:st={b-0.3:.2f}:d=0.3:alpha=1[p{k}]")
        fc.append(f"[{last}][p{k}]overlay=x=60:y=H-h-175:format=auto[vp{k}]")
        last = f"vp{k}"

    # brand strip slides up
    i_b = add(["-loop", "1", "-t", f"{dur}", "-i", brand_png])
    fc.append(f"[{last}][{i_b}:v]overlay=x=0:y='H-h*min(t/0.6,1)-30':format=auto[vb]")
    last = "vb"

    # progress bar
    i_r = add(["-loop", "1", "-t", f"{dur}", "-i", bar_png])
    fc.append(f"[{last}][{i_r}:v]overlay=x='-w+w*min(t/{dur:.2f},1)':y=H-12:format=auto[vr]")
    last = "vr"

    # optional user PNG layer (e.g. exported from Canva, 1080x1920 transparent)
    if os.path.exists("assets/overlay.png"):
        i_o = add(["-loop", "1", "-t", f"{dur}", "-i", "assets/overlay.png"])
        fc.append(f"[{last}][{i_o}:v]overlay=0:0:format=auto[vo]")
        last = "vo"

    i_a = add(["-i", audio])
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(fc) + f";[{last}]format=yuv420p[vout]",
            "-map", "[vout]", "-map", f"{i_a}:a", "-t", f"{dur}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    return out
