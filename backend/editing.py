"""Assembly with Claude Code: the agent receives the materials and designs the whole video itself.

Studio gathers what earlier steps made (each segment's visual, narration text and recording, the
opening's quote, background and recording, the music it may use) and the person's choices on the
Assembly page, then gives Claude Code one tool: GPU-accelerated rendering. Structure, timing,
motion, captions, transitions and sound are Claude Code's decisions.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = '.studio/edits'
DELIVERY = 'final_video.mp4'
# What an Assembly session may do without asking: work in its own folder, render and encode.
ALLOWED_TOOLS = ['Edit', 'Write', 'Bash(cd:*)', 'Bash(python studio_render.py:*)', 'Bash(python3 studio_render.py:*)',
                 'Bash(ffmpeg:*)', 'Bash(ffprobe:*)']
# The first message, so Claude Code starts working as soon as it opens; what to do is in the template.
KICKOFF = {False: '开始本次视频合成。', True: '开始修改上一次的视频合成。'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def folder(project, edit):
    work = (project / edit['folder']).resolve()
    if not work.is_relative_to(project.resolve() / ROOT):
        raise ValueError('Invalid assembly edit path.')
    return work


def stamp(path):
    path = Path(path)
    return f'{path.stat().st_size}:{path.stat().st_mtime_ns}' if path.is_file() else None


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def materials(studio, project, settings):
    """Everything earlier steps made for this video, with absolute paths, and the Assembly page's choices."""
    from server import duration, first_text
    script = read(project / 'text/script.json')
    raw = read(project / 'text/raw.json') if (project / 'text/raw.json').exists() else script
    if not script.get('segments'):
        raise ValueError('Generate segments before editing the video.')
    path = lambda f: str(f.resolve())
    scenes = []
    for segment in script['segments']:
        index = segment['index']
        audio = studio.asset(project, 'voice', f'voice_{index}', ['.wav'])
        visual = studio.asset(project, 'images', f'segment_{index}', ['.mp4', '.mov', '.webm', '.png', '.jpg', '.jpeg', '.webp'])
        if not str(segment.get('content', '')).strip() or not audio or not visual or not duration(audio):
            raise ValueError(f'Segment {index} needs narration text, a recording and a visual before editing the video.')
        scenes.append({'index': index, 'text': segment['content'], 'audio': path(audio), 'audio_seconds': duration(audio),
                       'visual': path(visual), 'visual_kind': 'video' if visual.suffix.lower() in {'.mp4', '.mov', '.webm'} else 'image',
                       'stamps': [stamp(audio), stamp(visual)]})
    opening = None
    if settings.get('global', {}).get('opening_quote', True):
        audio = studio.asset(project, 'voice', 'opening', ['.wav'])
        background = studio.asset(project, 'images', 'opening_bg', ['.png', '.jpg', '.jpeg', '.webp'])
        quote = first_text(raw.get('golden_quotes'))
        if not quote or not audio or not background:
            raise ValueError('The opening needs its quote, recording and background before editing the video.')
        opening = {'quote': quote, 'source_name': str(script.get('source_name') or raw.get('source_name') or '').strip(),
                   'background': path(background), 'audio': path(audio), 'audio_seconds': duration(audio),
                   'stamps': [stamp(audio), stamp(background)]}
    step5 = settings.get('step5', {})
    available = studio.music()['music']
    suggested = step5.get('bgm_filename') if step5.get('bgm_filename') in available else None
    mix = bool(step5.get('agent_music_mix'))
    tracks = available if mix else [suggested] if suggested else []
    width, height = map(int, str(step5.get('video_size') or '1280x720').lower().replace('×', 'x').replace(' ', '').split('x'))
    return {'title': first_text(raw.get('video_titles')), 'source_name': str(raw.get('source_name') or '').strip(),
            'video': {'width': width, 'height': height}, 'narration_speed': float(step5.get('narration_speed_factor', 1) or 1),
            'subtitles': bool(step5.get('enable_subtitles')),
            'music': {'suggested': suggested, 'mix': mix, 'tracks': [{'name': n, 'path': str(studio.music_file(n))} for n in tracks]},
            'opening': opening, 'segments': scenes}


def template_values(found, skill, work, delivery, revise):
    """What the step 5 template's variables say for this session."""
    music = found['music']
    if music['suggested'] and music['mix']:
        sound = f"建议以「{music['suggested']}」为主，也可以从清单里另选曲目，按需要混合或衔接"
    elif music['suggested']:
        sound = f"只用「{music['suggested']}」"
    elif music['mix']:
        sound = "由你从清单里挑选，可以混合多首"
    else:
        sound = "不用背景音乐"
    opening = ("先放开场：金句、书名、背景图和录音都在素材清单的 opening 里，开场画面由你设计" if found['opening']
               else "不放开场，视频从正文开始")
    session = ("修改上一次的合成。先查看工作目录里已有的文件和成片，汇报现状和可改进之处，然后等待用户的修改要求" if revise
               else "新的合成，独立完成到交付")
    return {'style': skill['id'], 'skill_path': skill['path'], 'materials': str(work / 'materials.json'),
            'video_size': f"{found['video']['width']}×{found['video']['height']}",
            'narration_speed': f"{found['narration_speed']:.2f}×（相对原录音）",
            'subtitles': '需要，样式由你设计' if found['subtitles'] else '不需要', 'music': sound, 'opening': opening,
            'session': session, 'work_dir': str(work), 'delivery': str(delivery)}


def prepare(studio, project, settings, session, revise=False):
    found = materials(studio, project, settings)
    style = settings.get('step5', {}).get('agent_skill', 'editorial-explainer')
    skill = studio.library.item('assembly_skills', style)
    work = project / ROOT / session
    previous = studio.state(project).get('assembly_edit')
    if revise and previous and folder(project, previous).is_dir():
        # A revision continues in a copy of the last session's folder. Pages and notes are copied, so editing
        # them leaves the earlier session as it was; large renders are hard-linked to keep the copy cheap.
        def link(source, target):
            if os.path.getsize(source) < 1_000_000:
                return shutil.copy2(source, target)
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        shutil.copytree(folder(project, previous), work, copy_function=link)
    work.mkdir(parents=True, exist_ok=True)
    write(work / 'materials.json', found)
    from core.infra.motion.runtime import prepare_assets
    prepare_assets(work)
    tool = (f'import sys\nfrom pathlib import Path\nsys.path[:0] = {[str(Path(__file__).parent), str(studio.engine)]!r}\n'
            f'from gpu_render import main\nmain(Path({str(work)!r}))\n')
    (work / 'studio_render.py').write_text(tool, encoding='utf-8')
    delivery = project / DELIVERY
    edit = {'folder': str(work.relative_to(project)), 'source': signature(found), 'skill': style,
            'delivered_before': stamp(delivery)}
    prompt = studio.library.prompt('step5_editing_agent.md', **template_values(found, skill, work, delivery, revise))
    return edit, work, prompt


def detail(project, edit, current_materials=None):
    """Where the last Claude Code edit stands: whether its materials are still current and whether it delivered."""
    if not edit:
        return None
    delivered = stamp(project / DELIVERY)
    return {**edit, 'current': current_materials is None or edit['source'] == signature(current_materials),
            'delivered': bool(delivered) and delivered != edit.get('delivered_before')}
