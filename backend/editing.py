"""A saved, source-bound editing plan for the Assembly Claude Code session."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

ROOT = '.studio/edits'
STYLES = ('editorial-explainer', 'cinematic-essay', 'kinetic-social')


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


def signature(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source(studio, project, settings):
    """Use exactly the currently selected visuals and canonical narration."""
    from server import duration, first_text
    script = read(project / 'text/script.json')
    raw = read(project / 'text/raw.json') if (project / 'text/raw.json').exists() else script
    scenes = []
    include_opening = settings.get('global', {}).get('opening_quote', True)
    selected = ([{'index': 0, 'content': first_text(raw.get('golden_quotes'))}] if include_opening else []) + script.get('segments', [])
    if not script.get('segments'):
        raise ValueError('Generate segments before editing the video.')
    for segment in selected:
        index = segment['index']
        if not str(segment.get('content', '')).strip():
            raise ValueError(f'Segment {index} needs narration text before editing the video.')
        audio = studio.asset(project, 'voice', 'opening' if index == 0 else f'voice_{index}', ['.wav', '.mp3'])
        visual = studio.asset(project, 'images', 'opening' if index == 0 else f'segment_{index}', ['.mp4', '.mov', '.webm', '.png', '.jpg', '.jpeg', '.webp'])
        seconds = duration(audio)
        if not audio or not visual or not seconds:
            raise ValueError(f'Segment {index} needs a recording and visual before editing the video.')
        scenes.append({'index': index, 'content': segment['content'], 'duration': seconds,
                       'audio': str(audio.relative_to(project)), 'visual': str(visual.relative_to(project)),
                       'audio_stamp': stamp(audio), 'visual_stamp': stamp(visual)})
    step5 = {k: v for k, v in settings.get('step5', {}).items() if k not in {'assembly_method', 'agent_skill'}}
    music = {name: {'path': str(studio.music_file(name)), 'stamp': stamp(studio.music_file(name))} for name in studio.music()['music']}
    dimensions = str(step5.get('video_size') or '1280x720').lower().replace('×', 'x').replace(' ', '')
    width, height = map(int, dimensions.split('x'))
    return {'project': str(project), 'scenes': scenes, 'settings': step5,
            'subtitles': settings.get('subtitles', {}), 'three_by_four':settings.get('three_by_four',{}), 'music': music,
            'title': first_text(raw.get('video_titles')), 'fps': 30,
            'width': width, 'height': height}


def normal(text):
    return ''.join(c.lower() for c in str(text) if c.isalnum())


def estimated_captions(text, seconds):
    parts = [p.strip() for p in re.split(r'(?<=[。！？.!?，,；;])', text) if p.strip()]
    parts = [part[i:i+24] for part in parts for i in range(0, len(part), 24)]
    total = sum(max(1, len(normal(p))) for p in parts)
    result, cursor = [], 0.0
    for part in parts:
        end = cursor + seconds * max(1, len(normal(part))) / max(1, total)
        result.append({'text': part, 'start': cursor, 'end': min(seconds, end)})
        cursor = end
    return result


def prepare(studio, project, settings, session, revise=False):
    payload = source(studio, project, settings)
    style = settings.get('step5', {}).get('agent_skill', STYLES[0])
    skill = studio.library.item('assembly_skills', style)
    work = project / ROOT / session
    previous = studio.state(project).get('assembly_edit')
    if revise and previous:
        old = folder(project, previous)
        if old.is_dir():
            shutil.copytree(old, work, ignore=shutil.ignore_patterns('build', 'previews', '__pycache__'))
    work.mkdir(parents=True, exist_ok=True)
    invalid_copy = False
    old_plan = {}
    if (work / 'plan.json').is_file():
        try:
            old_plan = read(work / 'plan.json')
            validate(work)
        except (ValueError, KeyError, TypeError, OSError):
            old_plan, invalid_copy = {}, True
    old_scenes = {s['index']: s for s in old_plan.get('scenes', [])}
    scenes = []
    for scene in payload['scenes']:
        item = old_scenes.get(scene['index']) if revise else None
        if not item:
            item = {'index': scene['index'], 'trim': [0, scene['duration']], 'pause_before': 0,
                    'zoom': float(payload['settings'].get('image_zoom_in_ratio', 1) or 1) if Path(scene['visual']).suffix.lower() in {'.png','.jpg','.jpeg','.webp'} else 1,
                    'overlays': [], 'captions': estimated_captions(scene['content'], scene['duration']), 'caption_timing': 'estimated'}
        scenes.append(item)
    track = payload['settings'].get('bgm_filename')
    plan = {**old_plan, 'version': 1, 'style': style if style in STYLES else STYLES[0], 'scenes': scenes,
            'music': old_plan.get('music', [{'track': track, 'start': 0, 'volume': float(payload['settings'].get('bgm_default_volume', .1))}] if track else []),
            'sfx': old_plan.get('sfx', []), 'notes': old_plan.get('notes', '')}
    from core.infra.motion.runtime import prepare_assets
    prepare_assets(work)
    write(work / 'studio-payload.json', payload)
    if not invalid_copy:
        write(work / 'plan.json', plan)
    (work / 'EDITING.md').write_text(studio.library.prompt('step5_editing_contract.md'), encoding='utf-8')
    tool = (f'import sys\nfrom pathlib import Path\nsys.path[:0] = {[str(Path(__file__).parent), str(studio.engine)]!r}\n'
            f'from edit_render import main\nmain(Path({str(work)!r}))\n')
    (work / 'studio_edit.py').write_text(tool, encoding='utf-8')
    edit = {'folder': str(work.relative_to(project)), 'source': signature(payload), 'skill': style, 'initial_plan': None if invalid_copy else signature(plan),
            'initial_html': {str(p.relative_to(work)): stamp(p) for p in work.rglob('*.html') if 'build' not in p.relative_to(work).parts}}
    prompt = studio.library.prompt('step5_editing_agent.md', 'main', skill_path=skill['path'],
        work_dir=str(work), contract_path=str(work / 'EDITING.md'), motion_skills_dir=str(studio.library_root / 'skills/step4/motion-core'))
    if invalid_copy:
        prompt += studio.library.prompt('step5_editing_agent.md', 'invalid')
    return edit, work, prompt


def finite(value, label, low=0, high=86400):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'Invalid {label}.')
    return value


def validate(work, payload=None):
    payload = payload or read(work / 'studio-payload.json')
    plan = read(work / 'plan.json')
    if not isinstance(plan, dict):
        raise ValueError('The editing plan must be a JSON object.')
    if not isinstance(plan.get('scenes'), list) or any(not isinstance(s, dict) or type(s.get('index')) is not int for s in plan['scenes']):
        raise ValueError('Each editing scene needs an integer narration index.')
    for collection in ['music', 'sfx']:
        if not isinstance(plan.get(collection, []), list) or any(not isinstance(cue, dict) for cue in plan.get(collection, [])):
            raise ValueError(f'{collection} must be a list of cue objects.')
    if type(plan.get('version')) is not int or plan.get('version') != 1 or plan.get('style') not in STYLES:
        raise ValueError('Choose a supported editing style and plan version 1.')
    if [s.get('index') for s in plan.get('scenes', [])] != [s['index'] for s in payload['scenes']]:
        raise ValueError('The edit must include every narration segment once, in order.')
    speed = finite(float(payload['settings'].get('narration_speed_factor', 1)), 'narration speed', .5, 2)
    cursor, resolved, warnings = 0, [], []
    for original, scene in zip(payload['scenes'], plan['scenes']):
        start, end = scene.get('trim', [0, original['duration']])
        finite(start, 'trim start', 0, original['duration']); finite(end, 'trim end', 0, original['duration'])
        if end <= start:
            raise ValueError('A scene must keep a positive recording duration.')
        pause = finite(scene.get('pause_before', 0), 'scene pause', 0, 10)
        zoom = finite(scene.get('zoom', 1), 'scene zoom', 1, 1.4)
        frames = max(1, math.ceil(((end-start) / speed + pause) * payload['fps'] - 1e-7))
        length = frames / payload['fps']
        captions = scene.get('captions', [])
        if not isinstance(captions, list) or any(not isinstance(cue, dict) or not isinstance(cue.get('text'), str) for cue in captions):
            raise ValueError('Captions must be a list of text and timing cue objects.')
        overlays = scene.get('overlays', [])
        if not isinstance(overlays, list) or any(not isinstance(item, dict) for item in overlays):
            raise ValueError('Overlays must be a list of objects.')
        if scene.get('caption_timing') not in {'estimated', 'aligned', 'reviewed'}:
            raise ValueError('Declare caption timing as estimated, aligned or reviewed.')
        if normal(''.join(c.get('text', '') for c in captions)) != normal(original['content']):
            raise ValueError(f'Segment {scene["index"]} captions must preserve the narration text.')
        if scene['caption_timing'] == 'estimated':
            warnings.append(f'Segment {scene["index"]}: estimated caption timing')
        previous_end = 0
        for cue in captions:
            a = finite(cue['start'], 'caption start', 0, original['duration'])
            b = finite(cue['end'], 'caption end', 0, original['duration']+.001)
            if b <= a or a < previous_end-.001:
                raise ValueError('Caption cues must be ordered and non-overlapping.')
            previous_end = b
        events = []
        for overlay in scene.get('overlays', []):
            if overlay.get('kind') not in {'keyword', 'quote', 'chapter', 'compare', 'bars'}:
                raise ValueError('Unsupported overlay kind.')
            if overlay.get('position', 'center') not in {'center', 'top', 'left', 'right'}:
                raise ValueError('Unsupported overlay position.')
            at = overlay.get('start')
            if at is None:
                keyword = normal(overlay.get('at', ''))
                text = normal(original['content'])
                if not keyword or keyword not in text:
                    raise ValueError(f'Segment {scene["index"]}: overlay keyword is absent from the narration.')
                for cue in captions:
                    text = normal(cue['text']); found = text.find(keyword)
                    if found >= 0:
                        at = cue['start'] + (cue['end']-cue['start']) * found/max(1, len(text)); break
                if at is None:
                    raise ValueError('The anchor crosses caption cues. Use a reviewed numeric start time.')
            at = finite(at, 'overlay start', start, end)
            until = finite(overlay.get('end', min(end, at + 3)), 'overlay end', at, end)
            if until <= at:
                raise ValueError('Overlay duration must be positive.')
            limit = 36 if overlay['kind'] == 'chapter' else 40 if overlay.get('position') in {'left', 'right'} else 80
            if not isinstance(overlay.get('text'), str) or not overlay['text'].strip() or len(overlay['text']) > limit or len(overlay['text'].splitlines()) > 3:
                raise ValueError(f'Overlay text is too long for this layout (up to {limit} characters and three lines).')
            if overlay['kind'] in {'compare', 'bars'}:
                items = overlay.get('items', [])
                if overlay.get('position', 'center') not in {'center', 'top'}:
                    raise ValueError('Comparisons and charts need a full-width position.')
                if not isinstance(items, list) or not 2 <= len(items) <= 6 or any(not isinstance(i, dict) or not isinstance(i.get('label'), str) or len(i['label']) > 20 for i in items):
                    raise ValueError('Comparisons and charts need 2–6 labelled items.')
                if overlay['kind'] == 'bars':
                    for item in items: finite(item.get('value'), 'chart value', 0, 100)
            if overlay.get('during_pause') and (overlay['kind'] != 'chapter' or not pause):
                raise ValueError('Only a chapter card with a positive pause can start during that pause.')
            events.append({**overlay, 'start': 0 if overlay.get('during_pause') else pause+(at-start)/speed, 'end': pause+(until-start)/speed})
        custom = scene.get('html')
        if custom:
            path = (work / custom).resolve()
            if not path.is_relative_to(work.resolve()) or path.suffix != '.html' or not path.is_file():
                raise ValueError('Custom scene HTML must be saved inside this edit folder.')
        transition = scene.get('transition', payload['settings'].get('transition_style', 'crossfade') if payload['settings'].get('enable_transitions') else 'cut')
        if transition not in {'cut','crossfade','fade_black','fade_white','slide_left','slide_right','wipe_left','wipe_right','zoom_in','zoom_out'}:
            raise ValueError('Unsupported scene transition.')
        transition_duration = min(length/3, finite(scene.get('transition_duration', payload['settings'].get('transition_duration', .4)), 'transition duration', .01, 3))
        resolved.append({**original, **scene, 'source_start': start, 'source_end': end, 'start': cursor/payload['fps'], 'start_frame': cursor,
                         'frames': frames, 'length': length, 'pause_before': pause, 'zoom': zoom, 'transition': transition, 'transition_duration': transition_duration, 'events': events})
        cursor += frames
    seconds = cursor/payload['fps']
    for cue in plan.get('music', []):
        if cue.get('track') not in payload['music']:
            raise ValueError('Choose a music track listed in studio-payload.json.')
        finite(cue.get('start', 0), 'music start', 0, seconds)
        finite(cue.get('end', seconds), 'music end', cue.get('start', 0)+.001, seconds)
        finite(cue.get('volume', .1), 'music volume', 0, .6)
        finite(cue.get('offset', 0), 'music offset', 0)
    for cue in plan.get('sfx', []):
        if cue.get('sound') not in {'click', 'whoosh', 'impact', 'chime'}:
            raise ValueError('Choose click, whoosh, impact or chime for a sound effect.')
        finite(cue.get('at'), 'sound cue time', 0, seconds)
        finite(cue.get('volume', .15), 'sound cue volume', 0, .5)
    return {'plan': plan, 'payload': payload, 'scenes': resolved, 'frames': cursor, 'duration': seconds, 'speed': speed, 'warnings': warnings}



def verify_inputs(payload, plan):
    project = Path(payload['project'])
    for scene in payload['scenes']:
        if stamp(project/scene['audio']) != scene['audio_stamp'] or stamp(project/scene['visual']) != scene['visual_stamp']:
            raise ValueError('Assembly media changed during this edit. Revise the edit before rendering.')
    script = read(project/'text/script.json')
    expected = [(s['index'], s['content']) for s in payload['scenes'] if s['index'] != 0]
    if expected != [(s['index'], s['content']) for s in script.get('segments', [])]:
        raise ValueError('Narration changed during this edit. Revise the edit before rendering.')
    raw = read(project/'text/raw.json') if (project/'text/raw.json').is_file() else script
    opening = next((s for s in payload['scenes'] if s['index'] == 0), None)
    quotes = raw.get('golden_quotes', [])
    quotes = quotes if isinstance(quotes, list) else [quotes]
    quote = next((v.strip() for v in quotes if isinstance(v, str) and v.strip()), '')
    if opening and opening['content'] != quote:
        raise ValueError('Opening quote changed during this edit. Revise the edit before rendering.')
    state = read(project/'.studio/state.json') if (project/'.studio/state.json').is_file() else {}
    for section, old in [('step5', payload['settings']), ('subtitles', payload['subtitles']), ('three_by_four', payload.get('three_by_four',{}))]:
        for key, value in state.get('settings', {}).get(section, {}).items():
            if key not in {'assembly_method', 'agent_skill'} and old.get(key) != value:
                raise ValueError('Assembly settings changed during this edit. Revise the edit before rendering.')
    for cue in plan.get('music', []):
        source = payload['music'][cue['track']]
        if stamp(source['path']) != source['stamp']:
            raise ValueError('An editing music track changed. Revise the edit before rendering.')


def detail(project, edit, current_source=None):
    if not edit:
        return None
    work = folder(project, edit)
    try:
        check = validate(work)
        problem = None
    except (ValueError, KeyError, TypeError, OSError) as exc:
        check, problem = None, str(exc)
    previews = [str(p.relative_to(project)) for p in [work/'previews/preview.mp4', work/'previews/contact-sheet.png'] if p.is_file()]
    paths = [work/'plan.json', *work.glob('*.html'), *[project/p for p in previews]]
    current_html = {str(p.relative_to(work)): stamp(p) for p in work.rglob('*.html') if 'build' not in p.relative_to(work).parts}
    changed = bool(check and (signature(check['plan']) != edit.get('initial_plan') or current_html != edit.get('initial_html', {})))
    from edit_render import candidate_current
    verified=candidate_current(work,check) if check else False
    published=False
    if verified:
        try:
            receipt=read(work/'build/published.json')
            from edit_render import render_signature
            published=receipt['input_signature']==render_signature(work,check) and all(stamp(path)==value for path,value in receipt['outputs'].items())
        except (OSError,ValueError,KeyError,TypeError):pass
    return {**edit, 'published':published,'verified':verified,'candidate':str((work/'build/candidate.mp4').relative_to(project)) if verified else None, 'changed': changed, 'style': check['plan']['style'] if check else edit['skill'], 'valid': check is not None, 'problem': problem, 'current': current_source is None or edit['source'] == signature(current_source),
            'duration': check['duration'] if check else None, 'warnings': check['warnings'] if check else [],
            'preview': previews[0] if previews and previews[0].endswith('.mp4') else None,
            'contact_sheet': next((p for p in previews if p.endswith('.png')), None), 'revisions': {str(p.relative_to(project)): stamp(p) for p in paths}}
