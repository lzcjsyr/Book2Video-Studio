"""Compile editable scene plans; render bounded HTML chunks and one verified audio mix."""
from __future__ import annotations
import argparse
import difflib
import html
import json
import os
from pathlib import Path
import shutil
import subprocess

from editing import estimated_captions, normal, read, validate, verify_inputs, write

FPS = 30
CHUNK_FRAMES = 120 * FPS


def run(args, **options):
    print(' '.join(str(a) for a in args[:3]), flush=True)
    return subprocess.run(list(map(str, args)), check=True, stdin=subprocess.DEVNULL, **options)


def ffmpeg(*args):
    return run([os.environ.get('HYPERFRAMES_FFMPEG_PATH') or shutil.which('ffmpeg') or 'ffmpeg', '-nostdin', '-y', '-v', 'error', *args])


def probe(path):
    result = subprocess.run([os.environ.get('HYPERFRAMES_FFPROBE_PATH') or 'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def align(work):
    """Map local ASR times back to canonical text, preserving every script character."""
    from core.infra.hyperframes.runtime import subprocess_env
    cli = shutil.which('whisper-cli', path=subprocess_env()['PATH'])
    model = Path(os.environ.get('STUDIO_WHISPER_MODEL', str(Path.home()/'.cache/hyperframes/whisper/models/ggml-small.bin')))
    if not cli or not model.is_file():
        raise ValueError('Local Whisper alignment is unavailable. Review caption cues manually or configure STUDIO_WHISPER_MODEL and whisper-cli.')
    payload, plan = read(work/'studio-payload.json'), read(work/'plan.json')
    outputs = work/'alignment';outputs.mkdir(exist_ok=True)
    scores = {}
    for source, scene in zip(payload['scenes'], plan['scenes']):
        name = outputs / str(source['index'])
        wav = name.with_suffix('.wav')
        ffmpeg('-i', Path(payload['project'])/source['audio'], '-ar', '16000', '-ac', '1', wav)
        run([cli, '-m', model, '-l', 'auto', '-f', wav, '-ojf', '-ml', '1', '-np', '-of', name], stdout=subprocess.DEVNULL)
        transcript = read(str(name)+'.json')
        chars = []
        for block in transcript['transcription']:
            text = normal(block['text']);a, b = block['offsets']['from']/1000, block['offsets']['to']/1000
            chars.extend((c, a+(b-a)*i/max(1,len(text))) for i,c in enumerate(text))
        canonical = normal(source['content'])
        matcher = difflib.SequenceMatcher(None, canonical, ''.join(c for c,_ in chars), autojunk=False)
        score = matcher.ratio();scores[str(source['index'])] = round(score, 3)
        if score < .8:
            raise ValueError(f'Segment {source["index"]} transcript match is {score:.0%}. Review the recording and transcript before aligning.')
        times = {}
        for block in matcher.get_matching_blocks():
            for i in range(block.size):times[block.a+i] = chars[block.b+i][1]
        known = sorted(times)
        for i in range(len(canonical)):
            if i not in times:
                left=max((k for k in known if k<i), default=-1);right=min((k for k in known if k>i), default=len(canonical))
                a=times[left] if left>=0 else 0;b=times[right] if right<len(canonical) else source['duration']
                times[i]=a+(b-a)*(i-left)/(right-left)
        cues=estimated_captions(source['content'], source['duration']);offset=0
        for cue in cues:
            length=len(normal(cue['text']));cue['start']=times.get(offset,0)
            cue['end']=times.get(offset+length,source['duration']);offset+=length
        # Adjacent equal ASR token times are coalesced; retain all canonical words.
        merged=[]
        for cue in cues:
            if merged and (cue['end']<=cue['start']+.001 or merged[-1]['end']<=merged[-1]['start']+.001):
                merged[-1]['text']+=cue['text'];merged[-1]['end']=cue['end']
            else:merged.append(cue)
        scene.update(captions=merged,caption_timing='aligned')
    write(work/'plan.json',plan);write(outputs/'report.json',scores)
    print(json.dumps(scores),flush=True)


def assets(work, check, visual_range=None):
    build=work/'build';build.mkdir(exist_ok=True)
    cli=Path(os.environ['AIGC_HYPERFRAMES_CLI']).resolve()
    gsap=cli.parents[2]/'gsap/dist/gsap.min.js'
    if not gsap.is_file():raise ValueError('The bundled GSAP renderer is unavailable.')
    shutil.copy2(gsap,build/'gsap.min.js')
    width,height=check['payload']['width'],check['payload']['height']
    if width < 16 or height < 16 or width%2 or height%2:raise ValueError('Video dimensions must be even and at least 16 pixels.')
    video_filter=f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={FPS}'
    required={i for i,s in enumerate(check['scenes']) if not visual_range or (s['start_frame'] < visual_range[1] and s['start_frame']+s['frames'] > visual_range[0])}
    required |= {i-1 for i in list(required) if i and check['scenes'][i]['transition'] != 'cut'}
    for position,scene in enumerate(check['scenes']):
        index=scene['index'];start=scene['source_start'];end=scene['source_end'];pause=scene['pause_before'];length=scene['length']
        source=Path(check['payload']['project'])
        audio=build/f'voice_{index}.wav';visual=build/f'visual_{index}.mp4'
        audio_filter=f'atrim=start={start}:end={end},asetpts=PTS-STARTPTS,atempo={check["speed"]},adelay={round(pause*1000)}:all=1,apad,atrim=duration={length},aresample=48000'
        ffmpeg('-i',source/scene['audio'],'-af',audio_filter,'-ac','2',audio)
        if position not in required:
            continue
        if scene.get('html'):
            from core.infra.hyperframes.runtime import hyperframes_command, subprocess_env
            custom=work/scene['html'];rendered=build/f'custom_{index}.mp4'
            if scene['length'] > 120: raise ValueError('Custom scene HTML must be no longer than two minutes.')
            run([*hyperframes_command(), 'render', '-c', scene['html'], '--output', rendered, '--fps', FPS, '--workers', '2'], cwd=work, env=subprocess_env())
            info=next(s for s in probe(rendered)['streams'] if s['codec_type']=='video')
            if abs(float(info['duration'])-length)>.1 or (info['width'],info['height'])!=(width,height): raise ValueError('Custom HTML duration or dimensions do not match the scene.')
            ffmpeg('-i',rendered,'-an','-frames:v',scene['frames'],'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',visual)
            ffmpeg('-i',visual,'-vf',f'select=eq(n\\,{scene["frames"]-1})','-frames:v','1',build/f'last_{index}.png')
            continue
        original=source/scene['visual'];still=original.suffix.lower() in {'.png','.jpg','.jpeg','.webp'}
        args=['-loop','1','-i',original] if still else ['-stream_loop','-1','-i',original]
        # A video's own audio never replaces the authoritative recording.
        vf=video_filter
        if not still:vf=f'trim=start={start}:end={end},setpts=(PTS-STARTPTS)/{check["speed"]},tpad=start_duration={pause}:start_mode=clone:stop_mode=clone:stop_duration={length},'+vf
        ffmpeg(*args,'-an','-vf',vf,'-frames:v',scene['frames'],'-c:v','libx264','-preset','ultrafast','-crf','18','-pix_fmt','yuv420p',visual)
        ffmpeg('-i',visual,'-vf',f'select=eq(n\\,{scene["frames"]-1})','-frames:v','1',build/f'last_{index}.png')
    write(build/'timeline.json',{'frames':check['frames'],'duration':check['duration'],'scenes':check['scenes'],'warnings':check['warnings']})
    return build


THEMES = {
 'editorial-explainer':('#10151b','#e9ae75','sans-serif',.24),
 'cinematic-essay':('#171713','#d6c5a2','serif',.55),
 'kinetic-social':('#10101b','#d2f85b','sans-serif',.16),
}


def composition(work, check, start_frame, frame_count):
    """Compile a bounded piece with one pre-roll frame; duplicate crossing events at seams."""
    build=work/'build';width,height=check['payload']['width'],check['payload']['height']
    base,accent,font,reveal=THEMES[check['plan']['style']]
    begin=start_frame/FPS;finish=(start_frame+frame_count)/FPS;pre=1/FPS
    elements=[];tweens=[]
    scale=height/720
    posters=[]
    for position,scene in enumerate(check['scenes']):
        global_start=scene['start_frame']/FPS;global_end=global_start+scene['length']
        if global_end<=begin or global_start>=finish:continue
        local=max(0,global_start-begin)+pre;visible_end=min(finish,global_end)-begin+pre
        offset=max(0,begin-global_start)
        ident=f'scene{scene["index"]}'
        custom=scene.get('html')
        # Source media is already fit to exact scene frames by FFmpeg.
        video=f'visual_{scene["index"]}.mp4'
        content=f'<video id="video{scene['index']}" muted playsinline src="{html.escape(video)}" data-start="{local:.6f}" data-duration="{visible_end-local:.6f}" data-media-start="{offset:.6f}" data-track-index="0" style="width:100%;height:100%;object-fit:cover"></video>'
        backdrop=''
        transition=scene['transition'] if position else 'cut';tau=scene['transition_duration']
        if transition!='cut' and offset<tau:
            previous=check['scenes'][position-1]['index'];poster=f'last_{previous}.png';posters.append(build/poster)
            backdrop=f'<img src="{poster}" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover" alt="">'
            if transition in {'fade_black','fade_white'}:
                color='#000' if transition=='fade_black' else '#fff';backdrop=f'<div style="position:absolute;inset:0;background:{color}"></div>'
            progress=offset/tau;duration=tau-offset;initial={};final={'duration':duration,'ease':'none','immediateRender':False}
            if transition in {'crossfade','fade_black','fade_white'}:initial={'opacity':progress};final['opacity']=1
            elif transition.startswith('slide_'):initial={'xPercent':(1-progress)*(-100 if transition=='slide_left' else 100)};final['xPercent']=0
            elif transition.startswith('wipe_'):initial={'clipPath':f'inset(0 {100*(1-progress) if transition=="wipe_left" else 0}% 0 {100*(1-progress) if transition=="wipe_right" else 0}%)'};final['clipPath']='inset(0 0% 0 0%)'
            else:initial={'opacity':progress,'scale':1+(1-progress)*(.2 if transition=='zoom_in' else -.2)};final.update(opacity=1,scale=1)
            tweens.append(f'tl.fromTo("#{ident} .surface",{json.dumps(initial)},{json.dumps(final)},{local:.6f});')
        elements.append(f'<div class="scene" id="{ident}" style="opacity:0">{backdrop}<div class="surface" style="position:absolute;inset:0"><div class="framing" style="position:absolute;inset:0">{content}</div></div></div>')
        # Explicit initial states make frame 0/pre-roll and mid-scene chunks deterministic.
        tweens.append(f'tl.set("#{ident}",{{opacity:0}},0);tl.set("#{ident}",{{opacity:1}},{local:.6f});tl.set("#{ident}",{{opacity:0}},{visible_end:.6f});')
        if scene['zoom']>1 and not custom:
            first=1+(scene['zoom']-1)*offset/scene['length'];last=1+(scene['zoom']-1)*(offset+visible_end-local)/scene['length']
            tweens.append(f'tl.fromTo("#{ident} .framing",{{scale:{first}}},{{scale:{last},duration:{visible_end-local},ease:"none",immediateRender:false}},{local:.6f});')
        for j,event in enumerate(scene['events']):
            a=global_start+event['start'];b=global_start+event['end']
            if b<=begin or a>=finish:continue
            text=html.escape(event['text']);kind=event['kind'];eid=f'event{scene["index"]}_{j}'
            extra=''
            if kind in {'bars','compare'}:
                items=[]
                for item in event['items']:
                    label=html.escape(item['label'])
                    bar=f'<i style="width:{item["value"]}%"></i><b>{item["value"]:g}</b>' if kind=='bars' else ''
                    items.append(f'<div class="item"><span>{label}</span>{bar}</div>')
                extra='<div class="items">'+''.join(items)+'</div>'
            elements.append(f'<div id="{eid}" class="overlay {kind} {event.get("position","center")}"><strong>{text}</strong>{extra}</div>')
            t=a-begin+pre;u=min(finish,b)-begin+pre
            tweens.append(f'tl.set("#{eid}",{{opacity:0,y:20}},0);')
            fade=min(reveal,(b-a)/3);elapsed=max(0,begin-a);remaining=max(0,fade-elapsed)
            if remaining:
                opacity=1-(1-min(1,elapsed/fade))**3
                tweens.append(f'tl.fromTo("#{eid}",{{opacity:{opacity},y:{20*(1-opacity)}}},{{opacity:1,y:0,duration:{remaining:.6f},ease:"power2.out",immediateRender:false}},{max(pre,t):.6f});')
            else:tweens.append(f'tl.set("#{eid}",{{opacity:1,y:0}},{pre:.6f});')
            tweens.append(f'tl.set("#{eid}",{{opacity:0}},{u:.6f});')
        if check['payload']['settings'].get('enable_subtitles',True):
            for j,cue in enumerate(scene['captions']):
                a=global_start+scene['pause_before']+(max(cue['start'],scene['source_start'])-scene['source_start'])/check['speed']
                b=global_start+scene['pause_before']+(min(cue['end'],scene['source_end'])-scene['source_start'])/check['speed']
                if b<=begin or a>=finish or b<=a:continue
                cid=f'caption{scene["index"]}_{j}'
                elements.append(f'<div id="{cid}" class="caption">{html.escape(cue["text"])}</div>')
                tweens.append(f'tl.set("#{cid}",{{opacity:0}},0);tl.set("#{cid}",{{opacity:1}},{max(pre,a-begin+pre):.6f});tl.set("#{cid}",{{opacity:0}},{min(finish,b)-begin+pre:.6f});')
    duration=(frame_count+1)/FPS
    doc=f'''<!doctype html><meta charset="utf-8"><script src="gsap.min.js"></script>
<style>*{{box-sizing:border-box}}html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;background:{base};font-family:{font};color:#fff}}#root{{position:relative;width:100%;height:100%;overflow:hidden}}.scene{{position:absolute;inset:0}}.overlay{{position:absolute;left:12%;right:12%;top:27%;padding:{32*scale}px;background:{base}ed;border-left:{5*scale}px solid {accent};font-size:{48*scale}px;line-height:1.25;opacity:0;border-radius:{10*scale}px;z-index:3}}.overlay strong{{font-weight:800;overflow-wrap:anywhere}}.overlay.top{{top:8%}}.overlay.left{{left:7%;right:48%}}.overlay.right{{left:48%;right:7%}}.chapter{{font-size:{60*scale}px;color:{accent}}}.quote{{font-family:serif;font-size:{42*scale}px}}.items{{display:flex;gap:{20*scale}px;margin-top:{20*scale}px;font-size:{25*scale}px}}.item{{flex:1;min-width:0;overflow-wrap:anywhere}}.overlay.bars{{top:8%;font-size:{36*scale}px}}.bars .items{{display:block}}.bars .item{{position:relative;margin:{10*scale}px 0}}.bars i{{display:block;height:{10*scale}px;background:{accent};margin-top:{8*scale}px}}.caption{{position:absolute;bottom:6%;left:8%;right:8%;text-align:center;font-family:sans-serif;font-size:{30*scale}px;line-height:1.4;text-shadow:0 2px 4px #000;background:#000a;padding:{8*scale}px;border-radius:{6*scale}px;z-index:5;opacity:0}}</style>
<div id="root" data-composition-id="main" data-start="0" data-width="{width}" data-height="{height}" data-duration="{duration:.6f}">{''.join(elements)}</div>
<script>const tl=gsap.timeline({{paused:true}});{''.join(tweens)}tl.to({{}},{{duration:0}},{duration:.6f});window.__timelines={{main:tl}};</script>'''
    path=build/'chunks'/f'{start_frame}_{frame_count}'/'index.html';path.parent.mkdir(parents=True,exist_ok=True)
    sources=[build/'gsap.min.js',*posters,*[build/f'visual_{s["index"]}.mp4' for s in check['scenes'] if s['start_frame'] < start_frame+frame_count and s['start_frame']+s['frames'] > start_frame]]
    for source in sources:
        target=path.parent/source.name;target.unlink(missing_ok=True)
        try:os.link(source,target)
        except OSError:shutil.copy2(source,target)
    path.write_text(doc,encoding='utf-8');return path


def sound(path, kind):
    import numpy as np
    import wave
    duration={'click':.12,'whoosh':.45,'impact':.4,'chime':.7}[kind];sr=48000
    time=np.arange(round(duration*sr))/sr
    if kind=='whoosh':signal=np.random.default_rng(7).normal(0,.12,len(time))*np.sin(np.pi*time/duration)**2
    else:
        freq={'click':1400,'impact':65,'chime':880}[kind];signal=.25*np.sin(2*np.pi*freq*time)*np.exp(-time*({'click':65,'impact':12,'chime':5}[kind]))
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(sr);wav.writeframes((signal*32767).astype('<i2').tobytes())


def mix(work, check):
    build=work/'build';duration=check['duration']
    listing=build/'audio-concat.txt';listing.write_text(''.join("file '"+str(build/f'voice_{s["index"]}.wav').replace("'","'\\''")+"'\n" for s in check['scenes']))
    narration=build/'narration.wav';ffmpeg('-f','concat','-safe','0','-i',listing,'-c','copy',narration)
    inputs=['-i',narration];filters=[];tracks=[];index=1
    for cue in check['plan'].get('music',[]):
        track=check['payload']['music'][cue['track']]['path'];end=cue.get('end',duration);length=end-cue.get('start',0)
        inputs+=['-stream_loop','-1','-i',track]
        fade=min(.6,length/3)
        normalizer='dynaudnorm=f=400:g=15,' if check['payload']['settings'].get('bgm_normalize_loudness', True) else ''
        filters.append(f'[{index}:a]atrim=start={cue.get("offset",0)}:duration={length},asetpts=PTS-STARTPTS,aresample=48000,{normalizer}volume={cue.get("volume",.1)},afade=t=in:d={fade},afade=t=out:st={length-fade}:d={fade},adelay={round(cue.get("start",0)*1000)}:all=1[m{index}]')
        tracks.append(f'[m{index}]');index+=1
    if tracks:
        filters.append(''.join(tracks)+f'amix=inputs={len(tracks)}:normalize=0[music]')
        if check['payload']['settings'].get('audio_ducking_enabled',True):
            strength=float(check['payload']['settings'].get('audio_ducking_strength',.5))
            filters+=['[0:a]asplit[voice][side]',f'[music][side]sidechaincompress=threshold=0.02:ratio={1+14*strength}:attack=120:release=450[ducked]']
            base='[voice][ducked]'
        else:base='[0:a][music]'
        filters.append(base+'amix=inputs=2:normalize=0[bed]');base='[bed]'
    else:base='[0:a]'
    cues=[]
    for cue in check['plan'].get('sfx',[]):
        path=build/f'sfx_{cue["sound"]}.wav';sound(path,cue['sound']);inputs+=['-i',path]
        filters.append(f'[{index}:a]volume={cue.get("volume",.15)},adelay={round(cue["at"]*1000)}:all=1[s{index}]');cues.append(f'[s{index}]');index+=1
    filters.append(base+''.join(cues)+f'amix=inputs={len(cues)+1}:normalize=0,apad,atrim=duration={duration},loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[out]')
    filterfile=build/'mix-filter.txt';filterfile.write_text(';'.join(filters));output=build/'mix.wav'
    ffmpeg(*inputs,'-filter_complex',';'.join(filters),'-map','[out]','-ac','2',output)
    return output


def render(work, preview=False, start=0, seconds=8):
    from core.infra.hyperframes.runtime import hyperframes_command, subprocess_env
    check=validate(work)
    verify_inputs(check["payload"], check["plan"])
    start_frame=round(start*FPS);frame_count=min(check['frames']-start_frame,round(seconds*FPS)) if preview else check['frames']
    if start_frame<0 or frame_count<=0:raise ValueError('Preview range is outside the edited video.')
    build=assets(work,check,(start_frame,start_frame+frame_count) if preview else None)
    chunks=[]
    for offset in range(start_frame,start_frame+frame_count,CHUNK_FRAMES):
        count=min(CHUNK_FRAMES,start_frame+frame_count-offset);composition_file=composition(work,check,offset,count)
        raw=build/f'raw_{offset}.mp4';chunk=build/f'chunk_{offset}.mp4'
        run([*hyperframes_command(),'render','-c',composition_file.name,'--output',raw,'--fps',FPS,'--quality','draft' if preview else 'looks','--workers','2','--strict'],cwd=composition_file.parent,env=subprocess_env())
        ffmpeg('-i',raw,'-an','-vf',f'trim=start_frame=1:end_frame={count+1},setpts=PTS-STARTPTS','-frames:v',count,'-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',chunk)
        chunks.append(chunk)
    listing=build/'video-concat.txt';listing.write_text(''.join("file '"+str(c).replace("'","'\\''")+"'\n" for c in chunks))
    audio=mix(work,check);output=work/'previews/preview.mp4' if preview else work/'build/candidate.mp4';output.parent.mkdir(exist_ok=True)
    codec='libx265' if not preview and check['payload']['settings'].get('video_codec')=='hevc' else 'libx264'
    ffmpeg('-f','concat','-safe','0','-i',listing,'-ss',start_frame/FPS,'-i',audio,'-map','0:v:0','-map','1:a:0','-frames:v',frame_count,'-t',frame_count/FPS,'-c:v',codec,'-preset','fast','-crf','18','-c:a','aac','-b:a','192k','-movflags','+faststart',output)
    verify_inputs(check["payload"], check["plan"])
    metadata=probe(output);video=next(s for s in metadata['streams'] if s['codec_type']=='video');audio_stream=next(s for s in metadata['streams'] if s['codec_type']=='audio')
    if int(video.get('nb_frames',0))!=frame_count or (video['width'],video['height'])!=(check['payload']['width'],check['payload']['height']):
        raise ValueError('Rendered frame count or dimensions do not match the edit.')
    expected=frame_count/FPS
    if abs(float(video['duration'])-expected)>.05 or abs(float(audio_stream['duration'])-expected)>.1:
        raise ValueError('Rendered audio/video duration does not match the edit.')
    ffmpeg('-i',output,'-f','null','-')
    report={'frames':frame_count,'duration':expected,'width':video['width'],'height':video['height'],'warnings':check['warnings'],'chunks':len(chunks),'notes':check['plan'].get('notes','')}
    write(output.with_suffix('.qa.json'),report)
    if preview:ffmpeg('-i',output,'-vf','fps=1,scale=320:-1,tile=4x2','-frames:v','1',work/'previews/contact-sheet.png')
    # A sidecar follows the same trim, pause and speed transform as burned-in captions.
    def timestamp(value):
        ms=round(max(0,value)*1000);return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'
    subtitles=[]
    for scene in check['scenes']:
        for cue in scene['captions']:
            a=scene['start_frame']/FPS+scene['pause_before']+(max(cue['start'],scene['source_start'])-scene['source_start'])/check['speed']
            b=scene['start_frame']/FPS+scene['pause_before']+(min(cue['end'],scene['source_end'])-scene['source_start'])/check['speed']
            if b>a:subtitles.append(f'{len(subtitles)+1}\n{timestamp(a)} --> {timestamp(b)}\n{cue["text"]}\n')
    (build/'captions.srt').write_text('\n'.join(subtitles),encoding='utf-8')
    return output,report


def main(work):
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['check','align','preview','render']);parser.add_argument('--start',type=float,default=0);parser.add_argument('--seconds',type=float,default=8)
    args=parser.parse_args()
    if args.action=='align':return align(work)
    if args.action=='check':
        check=validate(work);(work/'build').mkdir(exist_ok=True);write(work/'build/timeline.json',check)
        print(json.dumps({'duration':check['duration'],'frames':check['frames'],'warnings':check['warnings']},ensure_ascii=False));return
    output,report=render(work,preview=args.action=='preview',start=args.start,seconds=args.seconds)
    print(json.dumps({'output':str(output),**report},ensure_ascii=False))
