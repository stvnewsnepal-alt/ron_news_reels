
"""Hamro Chitwan — 1080x1350 News Reel Renderer."""
import os
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps, features

W, H, FPS = 1080, 1350, 25


def find_font():
    env = os.environ.get("FONT_PATH")
    if env and os.path.exists(env):
        return env

    try:
        p = subprocess.run(
            ["fc-match", "Noto Sans Devanagari:bold", "-f", "%{file}"],
            capture_output=True, text=True, check=False
        ).stdout.strip()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass

    for p in (
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    ):
        if os.path.exists(p):
            return p

    raise RuntimeError("उपयुक्त Nepali font भेटिएन।")


def get_font(size):
    layout = (
        ImageFont.Layout.RAQM
        if features.check("raqm")
        else ImageFont.Layout.BASIC
    )
    return ImageFont.truetype(find_font(), size, layout_engine=layout)


def duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", path
        ],
        capture_output=True, text=True, check=True
    )
    return float(result.stdout.strip())


def make_text_png(
    text, path, size, max_width,
    fill=(255, 255, 255, 255),
    bg=(0, 0, 0, 0),
    padding=20
):
    font = get_font(size)
    dummy = Image.new("RGBA", (max_width, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy)

    words = str(text).split()
    lines = []
    current = ""

    for word in words:
        trial = (current + " " + word).strip()

        if draw.textlength(trial, font=font) <= max_width - padding * 2:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    line_height = int(size * 1.4)
    height = max(
        line_height + padding * 2,
        line_height * len(lines) + padding * 2
    )

    image = Image.new("RGBA", (max_width, height), bg)
    painter = ImageDraw.Draw(image)

    for index, line in enumerate(lines):
        box = painter.textbbox((0, 0), line, font=font, stroke_width=2)
        text_width = box[2] - box[0]
        x = (max_width - text_width) // 2
        y = padding + index * line_height - box[1]

        painter.text(
            (x, y), line, font=font,
            fill=fill, stroke_width=2,
            stroke_fill=(0, 0, 0, 220)
        )

    image.save(path)
    return height


def prepare_photo(photo, output):
    """Keep the complete original photo visible; never crop source branding."""
    with Image.open(photo) as source:
        original = ImageOps.exif_transpose(source).convert("RGB")

    background = ImageOps.fit(
        original, (W, H),
        method=Image.Resampling.LANCZOS
    )
    background = background.filter(ImageFilter.GaussianBlur(30))
    background = background.point(lambda p: int(p * 0.58))

    foreground = ImageOps.contain(
        original, (W, H),
        method=Image.Resampling.LANCZOS
    )

    x = (W - foreground.width) // 2
    y = (H - foreground.height) // 2
    background.paste(foreground, (x, y))

    background.save(output, "JPEG", quality=94)
    return output


def run_ffmpeg(command):
    result = subprocess.run(
        command, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg rendering failed: " + result.stderr[-2500:]
        )


def render(
    photo,
    audio,
    headline,
    points,
    out,
    bg_video=None,
    brand="fb.com/ilovechitwan",
    work="",
    extra_images=None,
    source_credit="Hamro Chitwan"
):
    """
    Compatible with the main.py call:
    render(photo, audio, headline, points, out,
           bg_video=..., brand=..., work=...)

    Supports up to 3 photos total, including the main article photo.
    """

    work_dir = "out/render"
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    audio_duration = duration(audio)
    video_duration = max(20.0, min(audio_duration + 1.0, 40.0))
    total_frames = int(video_duration * FPS)

    # Main photo + up to two additional relevant photos.
    photo_list = [photo]

    for item in extra_images or []:
        if item and os.path.isfile(item) and item not in photo_list:
            photo_list.append(item)
        if len(photo_list) >= 3:
            break

    prepared = []

    for index, item in enumerate(photo_list):
        path = os.path.join(work_dir, f"photo_{index + 1}.jpg")
        prepare_photo(item, path)
        prepared.append(path)

    # Divide the Reel duration between available photos.
    segment_frames = [
        total_frames // len(prepared)
    ] * len(prepared)

    for index in range(total_frames % len(prepared)):
        segment_frames[index] += 1

    inputs = []
    filters = []

    def add_input(args):
        inputs.extend(args)
        return sum(1 for x in inputs if x == "-i") - 1

    # Create animated photo segments.
    segment_labels = []

    for index, path in enumerate(prepared):
        frames = segment_frames[index]
        seconds = frames / FPS

        image_input = add_input([
            "-loop", "1",
            "-t", str(seconds),
            "-i", path
        ])

        label = f"photo{index}"

        zoom = (
            f"[{image_input}:v]"
            f"fps={FPS},"
            f"scale={W}:{H},"
            f"zoompan="
            f"z='min(zoom+0.00045,1.07)':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={W}x{H}:fps={FPS},"
            f"setsar=1,"
            f"format=yuv420p"
            f"[{label}]"
        )

        filters.append(zoom)
        segment_labels.append(f"[{label}]")

    # Smooth crossfade between photos.
    current_label = segment_labels[0]

    if len(segment_labels) > 1:
        for index in range(1, len(segment_labels)):
            next_label = segment_labels[index]
            output_label = f"mix{index}"

            # 0.45 second transition.
            offset_frames = sum(segment_frames[:index]) - int(FPS * 0.45)
            offset = max(0, offset_frames) / FPS

            filters.append(
                f"{current_label}{next_label}"
                f"xfade=transition=fade:"
                f"duration=0.45:"
                f"offset={offset:.3f}"
                f"[{output_label}]"
            )

            current_label = f"[{output_label}]"

    last = current_label

    # Related Pixabay video as a small cutaway.
    if bg_video and os.path.isfile(bg_video):
        try:
            video_input = add_input([
                "-stream_loop", "-1",
                "-t", str(video_duration),
                "-i", bg_video
            ])

            filters.append(
                f"[{video_input}:v]"
                f"fps={FPS},"
                f"scale=420:520:force_original_aspect_ratio=increase,"
                f"crop=420:520,"
                f"setsar=1,"
                f"drawbox=x=0:y=0:w=iw:h=ih:"
                f"color=white@0.95:t=5"
                f"[related]"
            )

            filters.append(
                f"{last}[related]"
                f"overlay=x=625:y=600:"
                f"enable='between(t,8,14)'"
                f"[withrelated]"
            )

            last = "[withrelated]"

        except Exception as exc:
            print("Pixabay video overlay skipped:", str(exc)[:180])

    # Headline overlay.
    headline_path = os.path.join(work_dir, "headline.png")
    make_text_png(
        headline, headline_path, 54, 1000,
        fill=(255, 255, 255, 255),
        bg=(5, 18, 55, 225),
        padding=22
    )

    headline_input = add_input([
        "-loop", "1", "-t", str(video_duration),
        "-i", headline_path
    ])

    filters.append(
        f"[{headline_input}:v]"
        f"format=rgba,"
        f"fade=t=out:st=3.2:d=0.5:alpha=1"
        f"[headline]"
    )

    filters.append(
        f"{last}[headline]"
        f"overlay=(W-w)/2:65:"
        f"enable='lt(t,3.7)'"
        f"[withheadline]"
    )

    last = "[withheadline]"

    # Animated page address.
    brand_path = os.path.join(work_dir, "brand.png")
    make_text_png(
        brand, brand_path, 38, 700,
        fill=(255, 225, 35, 255),
        bg=(3, 12, 42, 220),
        padding=18
    )

    brand_input = add_input([
        "-loop", "1", "-t", str(video_duration),
        "-i", brand_path
    ])

    filters.append(
        f"[{brand_input}:v]"
        f"format=rgba,"
        f"fade=t=in:st=5:d=0.5:alpha=1,"
        f"fade=t=out:st=9:d=0.5:alpha=1"
        f"[brand]"
    )

    filters.append(
        f"{last}[brand]"
        f"overlay=x='(W-w)/2+18*sin(t*3)':y='H*0.48':"
        f"enable='between(t,5,9)'"
        f"[withbrand]"
    )

    last = "[withbrand]"

    # Source photo credit.
    credit_path = os.path.join(work_dir, "credit.png")
    make_text_png(
        "Photo Credit: " + source_credit,
        credit_path, 23, 850,
        fill=(255, 255, 255, 255),
        bg=(0, 0, 0, 175),
        padding=12
    )

    credit_input = add_input([
        "-loop", "1", "-t", str(video_duration),
        "-i", credit_path
    ])

    filters.append(
        f"[{credit_input}:v]format=rgba[credit]"
    )

    filters.append(
        f"{last}[credit]"
        f"overlay=(W-w)/2:H-h-30"
        f"[finalvideo]"
    )

    # Audio input.
    audio_input = add_input(["-i", audio])

    command = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex",
        ";".join(filters) + ";[finalvideo]format=yuv420p[vout]",
        "-map", "[vout]",
        "-map", f"{audio_input}:a",
        "-t", str(video_duration),
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        out
    ]

    print("Rendering photos:", len(prepared))
    print("Rendering duration:", round(video_duration, 2), "seconds")

    run_ffmpeg(command)

    return out
