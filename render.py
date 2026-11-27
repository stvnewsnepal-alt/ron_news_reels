"""Hamro Chitwan - vertical cinematic news renderer.
Drop-in compatible with the existing main.py render(photo, audio, headline, points, out, ...).
Use only the article photo and closely related photos passed by main.py.
"""
import os
import math
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
TOP_H = 390
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagariUI-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagariUI-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

def _font(candidates, size):
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

def _paths(value):
    """Accept one image path or a list/tuple of image paths."""
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]
    result = []
    for item in items:
        if isinstance(item, str) and os.path.isfile(item):
            ext = Path(item).suffix.lower()
            if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                result.append(item)
    return result[:3]

def _load_image(path):
    im = Image.open(path).convert("RGB")
    return im

def _cover(im, size):
    sw, sh = size
    iw, ih = im.size
    scale = max(sw / iw, sh / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    x = max(0, (nw - sw) // 2)
    y = max(0, (nh - sh) // 2)
    return im.crop((x, y, x + sw, y + sh))

def _contain(im, size):
    sw, sh = size
    iw, ih = im.size
    scale = min(sw / iw, sh / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    return im.resize((nw, nh), Image.Resampling.LANCZOS)

def _wrap(draw, text, font, max_width):
    words = str(text).split()
    lines, line = [], ""
    for word in words:
        test = word if not line else line + " " + word
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines

def _make_header(headline, points, brand):
    # Warm paper-like header similar to the supplied reference video.
    arr = np.zeros((TOP_H, W, 3), dtype=np.uint8)
    for y in range(TOP_H):
        t = y / max(1, TOP_H - 1)
        c1 = np.array([247, 222, 184], dtype=float)
        c2 = np.array([224, 166, 139], dtype=float)
        arr[y, :, :] = (c1 * (1 - t) + c2 * t).astype(np.uint8)
    # Subtle texture, not a flat colour block.
    rng = np.random.default_rng(17)
    noise = rng.normal(0, 2.2, (TOP_H, W, 1))
    arr = np.clip(arr.astype(float) + noise, 0, 255).astype(np.uint8)
    canvas = Image.fromarray(arr).convert("RGBA")
    d = ImageDraw.Draw(canvas)

    brand_font = _font(BOLD_CANDIDATES, 30)
    title_font = _font(BOLD_CANDIDATES, 43)
    point_font = _font(FONT_CANDIDATES, 30)

    # Brand mark in the top-right, like the reference video.
    brand_text = (brand or "HAMRO CHITWAN").strip()
    bb = d.textbbox((0, 0), brand_text, font=brand_font)
    bw = bb[2] - bb[0]
    d.rounded_rectangle((W - bw - 62, 22, W - 22, 70),
                        radius=12, fill=(255, 255, 255, 65))
    d.text((W - bw - 42, 28), brand_text, font=brand_font,
           fill=(76, 35, 31, 255))

    # Main headline and up to three short supporting points.
    title_lines = _wrap(d, headline or "मुख्य समाचार", title_font, W - 76)[:2]
    y = 92
    for line in title_lines:
        d.text((38, y), line, font=title_font, fill=(55, 35, 28, 255),
               stroke_width=1, stroke_fill=(255, 239, 218, 170))
        y += 54

    for point in (points or [])[:3]:
        if y > TOP_H - 48:
            break
        lines = _wrap(d, "• " + str(point), point_font, W - 90)
        for line in lines[:1]:
            d.text((48, y), line, font=point_font, fill=(61, 43, 35, 255))
            y += 40
    return canvas.convert("RGB")

def render(photo, audio, headline, points, out, bg_video=None,
           brand="HAMRO CHITWAN | RAMRO CHITWAN", work=None,
           extra_photos=None):
    """
    photo: primary article image path, or a list of image paths.
    extra_photos: optional list of closely related article/Pixabay images.
    audio: narration audio path (optional).
    out: output mp4 path.
    """
    candidates = []
    candidates.extend(_paths(photo))
    candidates.extend(_paths(extra_photos))
    # De-duplicate while retaining article photo first.
    unique = []
    for p in candidates:
        if p not in unique:
            unique.append(p)
    image_paths = unique[:3]
    if not image_paths:
        raise RuntimeError("No usable article photo was supplied; refusing unrelated fallback images.")

    images = [_load_image(p) for p in image_paths]
    header = _make_header(headline, points, brand)

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp_video = str(Path(out).with_suffix(".silent.mp4"))
    total_seconds = 30.0
    if audio and os.path.isfile(audio):
        try:
            probe = subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", audio
            ], text=True).strip()
            total_seconds = max(8.0, float(probe))
        except Exception:
            pass

    total_frames = int(total_seconds * FPS)
    segment = total_frames / len(images)
    fade_frames = int(0.7 * FPS)
    photo_top = TOP_H
    photo_h = H - photo_top

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-pix_fmt", "yuv420p", tmp_video
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)

    try:
        for frame_no in range(total_frames):
            idx = min(len(images) - 1, int(frame_no / segment))
            local = frame_no - idx * segment
            progress = min(1.0, max(0.0, local / max(1.0, segment)))

            # Blurred full-width photo backdrop keeps the vertical canvas filled.
            base = _cover(images[idx], (W, H)).filter(ImageFilter.GaussianBlur(34))
            base = Image.blend(base, Image.new("RGB", (W, H), (28, 20, 22)), 0.42)

            # Slow Ken-Burns zoom, while the original photo remains uncropped.
            zoom = 1.0 + 0.045 * progress
            box_w = int(W * 0.94 * zoom)
            box_h = int(photo_h * 0.91 * zoom)
            contained = _contain(images[idx], (box_w, box_h))
            # Keep the whole source image visible (including its photo credit/watermark).
            x = (W - contained.width) // 2
            y = photo_top + (photo_h - contained.height) // 2
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            layer.alpha_composite(contained.convert("RGBA"), (x, y))
            frame = base.convert("RGBA")
            frame.alpha_composite(layer)

            # Smooth crossfade to the next related image.
            if idx < len(images) - 1 and local < fade_frames:
                alpha = local / max(1, fade_frames)
                next_im = _cover(images[idx + 1], (W, H)).filter(ImageFilter.GaussianBlur(34))
                next_base = Image.blend(next_im, Image.new("RGB", (W, H), (28, 20, 22)), 0.42)
                next_contained = _contain(images[idx + 1], (int(W * 0.94), int(photo_h * 0.91)))
                nx = (W - next_contained.width) // 2
                ny = photo_top + (photo_h - next_contained.height) // 2
                next_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                next_layer.alpha_composite(next_contained.convert("RGBA"), (nx, ny))
                next_frame = next_base.convert("RGBA")
                next_frame.alpha_composite(next_layer)
                frame = Image.blend(frame, next_frame, alpha)

            frame.alpha_composite(header.convert("RGBA"), (0, 0))
            # Small lower-left brand label; source watermark is never removed.
            d = ImageDraw.Draw(frame)
            small = _font(BOLD_CANDIDATES, 25)
            d.rounded_rectangle((28, H - 72, 460, H - 25), radius=12,
                                fill=(15, 18, 25, 175))
            d.text((45, H - 64), "fb.com/ilovechitwan", font=small,
                   fill=(255, 255, 255, 245))

            proc.stdin.write(np.asarray(frame.convert("RGB"), dtype=np.uint8).tobytes())
    finally:
        if proc.stdin:
            proc.stdin.close()
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError("FFmpeg failed while rendering the video.")

    # Add the narration audio; if there is no audio, keep the silent render.
    if audio and os.path.isfile(audio):
        subprocess.run([
            "ffmpeg", "-y", "-i", tmp_video, "-i", audio,
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out
        ], check=True)
        os.remove(tmp_video)
    else:
        os.replace(tmp_video, out)

    return out
