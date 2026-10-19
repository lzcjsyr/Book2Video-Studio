"""The one tool Studio gives Assembly's Claude Code: GPU-accelerated rendering.

    python studio_render.py capabilities
    python studio_render.py render PAGE.html --seconds S --output OUT.mp4 [--fps 30]
    python studio_render.py snapshot PAGE.html --seconds S --at 0,1.5,3

A page provides window.clip = {width, height, durationSeconds, ready, renderAt(t)}. Rendering
captures every frame in a GPU-backed headless Chrome and encodes them with the machine's
hardware encoder, then checks the result; the output has no audio.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path


def main(work):
    work = Path(work).resolve()
    video = json.loads((work / 'materials.json').read_text(encoding='utf-8'))['video']
    parser = argparse.ArgumentParser(prog='studio_render.py', description='GPU-accelerated rendering for this video.')
    parser.add_argument('command', choices=['capabilities', 'render', 'snapshot'])
    parser.add_argument('page', nargs='?', help='An HTML page inside the working folder')
    parser.add_argument('--seconds', type=float, help='The page duration')
    parser.add_argument('--output', help='Where to write the video (render)')
    parser.add_argument('--at', help='Comma-separated seconds to capture (snapshot)')
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--width', type=int, default=video['width'])
    parser.add_argument('--height', type=int, default=video['height'])
    parser.add_argument('--browser-mode', choices=['auto', 'hardware', 'software'], default='auto')
    parser.add_argument('--encoder', choices=['auto', 'software'], default='auto')
    parser.add_argument('--force', action='store_true', help='Render again even if an identical render exists')
    args = parser.parse_args()
    os.environ['AIGC_BROWSER_MODE'], os.environ['AIGC_VIDEO_ENCODER'] = args.browser_mode, args.encoder
    from core.infra.motion.encoding import select_encoder
    select_encoder.cache_clear()
    size = {'width': args.width, 'height': args.height}
    if args.command == 'capabilities':
        from core.infra.motion.capabilities import inspect_machine
        print(json.dumps(inspect_machine(work, size, fps=args.fps), ensure_ascii=False, indent=2))
        return
    if not args.page or not args.seconds:
        parser.error('Name the page and its --seconds.')
    page = (work / args.page).resolve()
    if not page.is_relative_to(work) or page.suffix != '.html' or not page.is_file():
        parser.error('The page must be an existing .html file inside the working folder.')
    entry = page.relative_to(work).as_posix()
    payload = {**size, 'durationSeconds': args.seconds, 'fps': args.fps}
    if args.command == 'snapshot':
        from core.infra.motion.renderer import capture
        times = [float(t) for t in (args.at or '0').split(',')]
        report = capture(work, payload, mode='snapshot', fps=args.fps, times=times, caption_zone=False, entry=entry,
                         destination=work / 'snapshots' / page.stem)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if not args.output:
        parser.error('Name the --output video.')
    from core.infra.motion.renderer import render_clip
    # Several renders may run side by side in this folder, each writing its own video.
    output = render_clip(work, payload, (work / args.output).resolve(), fps=args.fps, caption_zone=False, entry=entry,
                         force=args.force, ignore_media=True)
    report = json.loads(Path(output).with_suffix('.render.json').read_text(encoding='utf-8'))
    print(json.dumps({'output': output, 'frames': report.get('frames'), 'encoder': report.get('encoder'),
                      'browser': report.get('gpu'), 'browserMode': report.get('browserMode')}, ensure_ascii=False))
