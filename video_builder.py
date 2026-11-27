import os, math, random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import numpy as np
from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips, ColorClip
from config import VIDEO_W,VIDEO_H,FPS,DURATION,PRESENTER_PATH,LOGO_PATH,MUSIC_PATH,FONT_PATH,FONT_REGULAR_PATH
from transitions import *

def _font(size,bold=True):
    p=FONT_PATH if bold else FONT_REGULAR_PATH
    try: return ImageFont.truetype(p,size)
    except: return ImageFont.load_default()

def _fit_crop(im,size):
    w,h=size; iw,ih=im.size
    scale=max(w/iw,h/ih)
    im=im.resize((int(iw*scale),int(ih*scale)),Image.Resampling.LANCZOS)
    x=(im.width-w)//2; y=(im.height-h)//2
    return im.crop((x,y,x+w,y+h))

def _background():
    # Smoky navy background with red glow, inspired by the supplied reference video.
    y,x=np.mgrid[0:VIDEO_H,0:VIDEO_W]
    arr=np.zeros((VIDEO_H,VIDEO_W,3),dtype=np.uint8)
    for c,(a,b) in enumerate(zip(BG_TOP,BG_BOTTOM)):
        arr[:,:,c]=(a+(b-a)*y/VIDEO_H).astype(np.uint8)
    glow=np.exp(-(((x-VIDEO_W*.5)/(VIDEO_W*.65))**2+((y-VIDEO_H*.08)/(VIDEO_H*.25))**2)*2.4)
    arr[:,:,0]=np.clip(arr[:,:,0]+glow*105,0,255)
    arr[:,:,1]=np.clip(arr[:,:,1]+glow*22,0,255)
    arr[:,:,2]=np.clip(arr[:,:,2]+glow*26,0,255)
    im=Image.fromarray(arr).convert("RGBA")
    # subtle smoke texture
    noise=Image.fromarray(np.uint8(np.random.default_rng(8).normal(128,18,(VIDEO_H,VIDEO_W)))).filter(ImageFilter.GaussianBlur(18))
    overlay=Image.new("RGBA",im.size,(170,180,200,0))
    overlay.putalpha(noise.point(lambda v:max(0,min(35,abs(v-128)))))
    return Image.alpha_composite(im,overlay)

def _text_card(story,photo_path,idx,total):
    bg=_background()
    draw=ImageDraw.Draw(bg)
    draw.rounded_rectangle((55,55,330,120),radius=20,fill=(205,25,42,245))
    draw.text((78,67),"HAMRO CHITWAN",font=_font(30),fill=WHITE)
    draw.text((58,155),"मुख्य समाचार",font=_font(45),fill=YELLOW)
    # main news image window
    photo=Image.open(photo_path).convert("RGB")
    photo=_fit_crop(photo,(970,610))
    bg.alpha_composite(photo.convert("RGBA"),(55,225))
    draw=ImageDraw.Draw(bg)
    draw.rounded_rectangle((55,225,1025,835),radius=18,outline=(255,255,255,190),width=4)
    # Headline wraps to max 3 lines
    title=story["title"]
    words=title.split()
    lines=[]; line=""
    for word in words:
        test=(line+" "+word).strip()
        if draw.textbbox((0,0),test,font=_font(48))[2] > 950 and line:
            lines.append(line); line=word
        else: line=test
    if line: lines.append(line)
    lines=lines[:3]
    yy=875
    for ln in lines:
        draw.text((60,yy),ln,font=_font(47),fill=WHITE,stroke_width=1,stroke_fill=(0,0,0))
        yy+=66
    draw.rounded_rectangle((55,1120,1025,1195),radius=15,fill=(180,20,38,245))
    draw.text((82,1137),"सत्य समाचार • विश्वसनीय जानकारी",font=_font(34),fill=WHITE)
    draw.text((60,1240),"सधै समाचार, सत्य समाचार",font=_font(30),fill=YELLOW)
    if os.path.exists(LOGO_PATH):
        logo=Image.open(LOGO_PATH).convert("RGBA"); logo.thumbnail((145,145))
        bg.alpha_composite(logo,(875,40))
    return bg.convert("RGB")

def build_video(story,visuals,audio_path,out_path):
    if not visuals: raise RuntimeError("No visual assets.")
    # Use a single editorial still with slow Ken Burns motion, then alternate visuals
    # across the timeline while retaining the same graphic system.
    segments=[]
    n=max(1,min(len(visuals),5))
    segdur=DURATION/n
    for i in range(n):
        frame=_text_card(story,visuals[i],i,n)
        p=Path("output")/f"frame_{i}.jpg"; frame.save(p,quality=94)
        clip=ImageClip(str(p)).set_duration(segdur)
        # Gentle slow zoom; consistent across all segments.
        clip=clip.resize(lambda t:1.0+0.035*(t/segdur)).set_position("center")
        clip=CompositeVideoClip([clip.set_position("center")],size=(VIDEO_W,VIDEO_H)).set_duration(segdur)
        segments.append(clip)
    video=concatenate_videoclips(segments,method="compose")
    voice=AudioFileClip(audio_path)
    if voice.duration>DURATION: voice=voice.subclip(0,DURATION)
    audio=voice
    if os.path.exists(MUSIC_PATH):
        music=AudioFileClip(MUSIC_PATH).volumex(0.10)
        if music.duration<DURATION: music=music.audio_loop(duration=DURATION)
        else: music=music.subclip(0,DURATION)
        audio=CompositeVideoClip([]) if False else None
        from moviepy.editor import CompositeAudioClip
        audio=CompositeAudioClip([voice.volumex(1.0),music])
    video=video.set_audio(audio)
    video.write_videofile(str(out_path),fps=FPS,codec="libx264",audio_codec="aac",preset="medium",threads=4)
    return str(out_path)
