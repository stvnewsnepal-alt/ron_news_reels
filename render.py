import os, subprocess, json, math, tempfile
from PIL import Image

W,H=1080,1350
FPS=30


def run(cmd):
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if p.returncode: raise RuntimeError('FFmpeg failed: '+p.stderr[-5000:])
    return p


def media_duration(path):
    p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',path],capture_output=True,text=True)
    try: return max(1.0,float(p.stdout.strip()))
    except: return 1.0


def make_music(path,duration):
    # Soft, original ambient bed synthesized locally; no external copyrighted track.
    # Three quiet tones with slow fades create a subtle instrumental-like pad.
    cmd=['ffmpeg','-y','-f','lavfi','-i',f'sine=frequency=196:sample_rate=44100:duration={duration}',
         '-f','lavfi','-i',f'sine=frequency=246.94:sample_rate=44100:duration={duration}',
         '-f','lavfi','-i',f'sine=frequency=293.66:sample_rate=44100:duration={duration}',
         '-filter_complex','[0:a]volume=0.035,afade=t=in:st=0:d=1,afade=t=out:st='+str(max(0,duration-2))+':d=2[a0];[1:a]volume=0.022,afade=t=in:st=0:d=1,afade=t=out:st='+str(max(0,duration-2))+':d=2[a1];[2:a]volume=0.016,afade=t=in:st=0:d=1,afade=t=out:st='+str(max(0,duration-2))+':d=2[a2];[a0][a1][a2]amix=inputs=3:normalize=0,lowpass=f=1200[m]',
         '-map','[m]','-c:a','pcm_s16le',path]
    run(cmd)


def still_segment(image, duration, output, motion=0):
    # Crop-to-fill, then subtle Ken Burns pan/zoom; no captions or text.
    zoom_expr=['min(zoom+0.00045,1.12)','if(lte(on,1),1.0,min(zoom+0.00035,1.10))','min(zoom+0.00055,1.14)'][motion%3]
    x_expr=['iw/2-(iw/zoom/2)+sin(on/35)*18','iw/2-(iw/zoom/2)+cos(on/40)*15','iw/2-(iw/zoom/2)+sin(on/30)*22'][motion%3]
    y_expr=['ih/2-(ih/zoom/2)+cos(on/38)*18','ih/2-(ih/zoom/2)+sin(on/42)*15','ih/2-(ih/zoom/2)+cos(on/33)*22'][motion%3]
    vf=f"scale=1600:2000:force_original_aspect_ratio=increase,crop=1600:2000,zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d=1:s={W}x{H}:fps={FPS},trim=duration={duration},setpts=PTS-STARTPTS,setsar=1,format=yuv420p"
    run(['ffmpeg','-y','-loop','1','-i',image,'-t',str(duration),'-vf',vf,'-r',str(FPS),'-an','-c:v','libx264','-preset','veryfast','-crf','21',output])


def video_segment(video, duration, output):
    vf=f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},trim=duration={duration},setpts=PTS-STARTPTS,setsar=1,format=yuv420p"
    run(['ffmpeg','-y','-stream_loop','-1','-i',video,'-t',str(duration),'-vf',vf,'-an','-c:v','libx264','-preset','veryfast','-crf','22',output])


def render(main_photo,audio_path,output,extras=None,logo=None):
    extras=extras or []
    total=max(8.0,media_duration(audio_path)+0.8)
    # Article photo always leads. Use exactly three visual sections, filling missing slots with its photo.
    visual=[{'path':main_photo,'kind':'image'}]+extras[:2]
    while len(visual)<3: visual.append({'path':main_photo,'kind':'image'})
    # Three clips overlap by 0.45s; total duration is matched to narration.
    overlap=0.45
    seg=(total+2*overlap)/3
    with tempfile.TemporaryDirectory() as td:
        clips=[]
        for i,item in enumerate(visual):
            p=os.path.join(td,f'clip{i}.mp4')
            if item['kind']=='video': video_segment(item['path'],seg,p)
            else: still_segment(item['path'],seg,p,i)
            clips.append(p)
        # Crossfade three clips with matching audio duration.
        fc=f'[0:v][1:v]xfade=transition=fade:duration={overlap}:offset={seg-overlap}[v01];[v01][2:v]xfade=transition=fade:duration={overlap}:offset={2*(seg-overlap)}[vbase]'
        inputs=[]
        for c in clips: inputs += ['-i',c]
        # Logo is an image overlay only; no text is drawn anywhere.
        if logo and os.path.exists(logo):
            inputs += ['-i',logo]
            fc += f";[3:v]scale=170:-1,format=rgba,colorchannelmixer=aa=0.92[lg];[vbase][lg]overlay=W-w-42:42:format=auto[vout]"
        else: fc += ';[vbase]null[vout]'
        # Mix a quiet original ambient bed under the narration.
        music=os.path.join(td,'music.wav')
        make_music(music,total)
        inputs += ['-i',audio_path,'-i',music]
        fc += f";[4:a]aresample=44100,volume=1.0,apad,atrim=duration={total}[voice];[5:a]volume=0.22,apad,atrim=duration={total}[music];[voice][music]amix=inputs=2:duration=first:dropout_transition=2, loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
        run(['ffmpeg','-y',*inputs,'-filter_complex',fc,'-map','[vout]','-map','[aout]','-t',str(total),'-r',str(FPS),'-c:v','libx264','-preset','medium','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',output])
    print('Rendered:',output)
