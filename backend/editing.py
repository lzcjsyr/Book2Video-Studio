"""Assembly with Claude Code: the agent receives the materials and designs the whole video itself.

Studio gathers what earlier steps made (each segment's visual, narration text and recording, the
opening's quote, background and recording, the music it may use) and the person's choices on the
Assembly page, then supplies the template's execution contract. The agent writes its own run.py. Structure, timing,
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
TIMING = 'timing.jsonl'
# What an Assembly session may do without asking: work in its own folder, render and encode.
ALLOWED_TOOLS = ['Edit', 'Write', 'Bash(cd:*)', 'Bash(python run.py:*)', 'Bash(python3 run.py:*)', 'Bash(ffmpeg:*)', 'Bash(ffprobe:*)']


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
    # The bundled sound effects belong to the app, not the project, and a recording's timing changes only with the
    # recording, whose stamp is already here: neither may make an edit look stale on its own.
    value = {key: item for key, item in value.items() if key != 'sound_effects'}
    untimed = lambda part: {key: item for key, item in part.items() if key != 'timing'} if isinstance(part, dict) else part
    value.update(segments=[untimed(s) for s in value.get('segments', [])], opening=untimed(value.get('opening')))
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
                       'timing': timing(audio), 'visual': path(visual), 'visual_kind': 'video' if visual.suffix.lower() in {'.mp4', '.mov', '.webm'} else 'image',
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
                   'timing': timing(audio), 'stamps': [stamp(audio), stamp(background)]}
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
            'sound_effects': sound_effects(studio.engine / 'sfx'), 'opening': opening, 'segments': scenes}


def timing(audio):
    """Sentence and character times Voice saved beside a recording (voice_N.json), or None for older recordings.
    Only a file written after the recording describes it: an older one belongs to an earlier take."""
    found = Path(audio).with_suffix('.json')
    if not found.is_file() or found.stat().st_mtime_ns < Path(audio).stat().st_mtime_ns:
        return None
    try:
        value = read(found)
    except (OSError, ValueError):
        return None
    return {'sentences': value['sentences'], 'seconds': value.get('seconds')} if isinstance(value.get('sentences'), list) else None


def split_timing(found, work):
    """What the agent reads: materials.json keeps each recording's sentences, small enough to read whole; the
    character times, too many to read, go to one timing.jsonl in the edit folder (a line per sentence) for its scripts."""
    lines = []

    def part(item, key):
        if not item:
            return item
        sentences = (item.get('timing') or {}).get('sentences')
        for number, sentence in enumerate(sentences or []):
            words = [[w['text'], w['start'], w['end'], w.get('confidence')] for w in sentence['words']]
            lines.append(json.dumps({'part': key, 'sentence': number, 'text': sentence['text'], 'start': sentence['start'],
                                     'end': sentence['end'], 'words': words}, ensure_ascii=False))
        shown = {k: v for k, v in item.items() if k != 'timing'}
        shown['sentences'] = [{k: s[k] for k in ('text', 'start', 'end')} for s in sentences] if sentences is not None else None
        return shown

    shown = {**found, 'opening': part(found['opening'], 'opening'), 'segments': [part(s, s['index']) for s in found['segments']]}
    target = work / TIMING
    if lines:
        # A revision's copy may share files with the earlier session: replace, never rewrite in place.
        temp = target.with_name(TIMING + '.tmp')
        temp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        temp.replace(target)
    else:
        target.unlink(missing_ok=True)
    shown['word_timing'] = TIMING if lines else None
    return shown


def sound_effects(folder):
    """The bundled sound effects (engine/sfx/sfx.json), each with its absolute path."""
    manifest = Path(folder) / 'sfx.json'
    if not manifest.is_file():
        return {'sounds': []}
    found = read(manifest)
    sounds = [{key: value for key, value in sound.items() if key not in {'file', 'source'}} | {'path': str((Path(folder) / sound['file']).resolve())}
              for sound in found['sounds'] if (Path(folder) / sound['file']).is_file()]
    return {'about': found.get('about', ''), 'usage': found.get('usage', ''), 'sounds': sounds}


def template_values(found, skill, work, delivery, revise, resume=False):
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
    if resume:
        session = "继续上一次未完成的自动合成。先检查已保存的片段、字幕、音频和日志，复用已验证的产物，只补缺失或失败部分，独立完成到交付，不等待用户确认"
    return {'style': skill['id'], 'skill_path': skill['path'], 'materials': str(work / 'materials.json'),
            'video_size': f"{found['video']['width']}×{found['video']['height']}",
            'narration_speed': f"{found['narration_speed']:.2f}×（相对原录音）",
            'subtitles': '需要，样式由你设计' if found['subtitles'] else '不需要', 'music': sound, 'opening': opening,
            'session': session, 'work_dir': str(work), 'delivery': str(delivery)}


def prepare(studio, project, settings, session, revise=False, resume=False):
    found = materials(studio, project, settings)
    style = settings.get('step5', {}).get('agent_skill', 'content-led')
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
    write(work / 'materials.json', split_timing(found, work))
    from core.infra.motion.runtime import prepare_assets
    prepare_assets(work)
    # Remove the app-owned legacy wrapper only in this new session's copy.
    (work / 'studio_render.py').unlink(missing_ok=True)
    delivery = project / DELIVERY
    edit = {'folder': str(work.relative_to(project)), 'source': signature(found), 'skill': style,
            'delivered_before': stamp(delivery)}
    prompt = studio.library.prompt('step5_editing_agent.md', engine_path=repr(str(studio.engine)),
                                  **template_values(found, skill, work, delivery, revise, resume))
    return edit, work, prompt


def detail(project, edit, current_materials=None):
    """Where the last Claude Code edit stands: whether its materials are still current and whether it delivered."""
    if not edit:
        return None
    delivered = stamp(project / DELIVERY)
    return {**edit, 'current': current_materials is None or edit['source'] == signature(current_materials),
            'delivered': bool(delivered) and delivered != edit.get('delivered_before')}
