"""Shared Step 5 assembly runner: concurrent clip rendering, concat/ASS compose, verification, and atomic delivery."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .artifacts import stamp
from .capabilities import inspect_machine
from .encoding import encode, select_encoder
from .renderer import _tool, capture, render_clip
from .runtime import parse_size, prepare_assets, subprocess_env

ENGINE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKERS = 4
MAX_WORKERS = 6
MAX_ATTEMPTS = 3

# Non-retryable contract/input failures should surface immediately with clip index and log path.
_CONTRACT_MARKERS = (
    'valueerror:',
    'html entry must exist',
    'external resource blocked',
    'canvas overflow',
    'clip.ready must be a promise',
    'clip width/height must match',
    'http 4',
    'http 5',
    'syntaxerror',
    'referenceerror',
    'typeerror',
    'no such file',
)
_TRANSIENT_MARKERS = (
    'timed out',
    'timeout',
    'target closed',
    'session closed',
    'browser has disconnected',
    'protocol error',
    'econnreset',
    'epipe',
    'sigkill',
    'sigterm',
    'browser capture failed',
)


def _inside(work: Path, raw: str, *, label: str) -> Path:
    path = Path(raw)
    resolved = (path if path.is_absolute() else work / path).resolve()
    if not resolved.is_relative_to(work):
        raise ValueError(f'{label} must stay inside the working directory: {raw}')
    return resolved


def _read_size(work: Path, manifest: dict) -> tuple[int, int]:
    if manifest.get('size'):
        size = manifest['size']
        if isinstance(size, dict):
            return parse_size(f"{size['width']}x{size['height']}")
        return parse_size(str(size))
    materials = work / 'materials.json'
    if materials.is_file():
        video = json.loads(materials.read_text(encoding='utf-8')).get('video') or {}
        if video.get('width') and video.get('height'):
            return parse_size(f"{video['width']}x{video['height']}")
    raise ValueError('Video size is required in render.json ("size") or materials.json ("video").')


def _load_manifest(work: Path, manifest_path: str | Path = 'render.json') -> dict:
    path = _inside(work, str(manifest_path), label='manifest')
    if not path.is_file():
        raise FileNotFoundError(f'Manifest not found: {path}')
    data = json.loads(path.read_text(encoding='utf-8'))
    fps = data.get('fps')
    if not isinstance(fps, int) or isinstance(fps, bool) or not (1 <= fps <= 120):
        raise ValueError('render.json "fps" must be an integer between 1 and 120.')
    clips = data.get('clips')
    if not isinstance(clips, list) or not clips:
        raise ValueError('render.json "clips" must be a non-empty list.')
    for idx, clip in enumerate(clips):
        if not isinstance(clip, dict):
            raise ValueError(f'Clip {idx} must be an object.')
        frames = clip.get('frames')
        if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
            raise ValueError(f'Clip {idx} "frames" must be a positive integer.')
        if bool(clip.get('html')) == bool(clip.get('video')):
            raise ValueError(f'Clip {idx} must set exactly one of "html" or "video".')
        if clip.get('html'):
            entry = _inside(work, str(clip['html']), label=f'clip {idx} html')
            if not entry.is_file():
                raise FileNotFoundError(f'Clip {idx} HTML not found: {entry}')
        else:
            video = _inside(work, str(clip['video']), label=f'clip {idx} video')
            if not video.is_file():
                raise FileNotFoundError(f'Clip {idx} video not found: {video}')
    return data


def _probe(path: Path, *, expected_seconds: float = 0.0, count_packets: bool = True) -> dict:
    cmd = [_tool('ffprobe'), '-v', 'error']
    if count_packets:
        cmd.append('-count_packets')
    cmd += ['-show_streams', '-show_format', '-of', 'json', str(path)]
    result = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=max(30, math.ceil(expected_seconds)),
    )
    return json.loads(result.stdout)


def _audio_duration(audio: Path) -> float:
    meta = _probe(audio, count_packets=False)
    audios = [s for s in meta.get('streams', []) if s.get('codec_type') == 'audio']
    if not audios:
        raise RuntimeError(f'Audio file contains no audio stream: {audio}')
    raw = meta.get('format', {}).get('duration') or audios[0].get('duration')
    if raw is None:
        raise RuntimeError(f'Could not read duration of audio file: {audio}')
    return float(raw)


def verify_video(
    path: Path | str,
    width: int,
    height: int,
    fps: int,
    frames: int,
    *,
    expect_audio: bool = False,
) -> dict:
    """Fast packet-level validation using ffprobe -count_packets."""
    target = Path(path).resolve()
    expected_seconds = frames / fps
    meta = _probe(target, expected_seconds=expected_seconds, count_packets=True)
    streams = meta.get('streams', [])
    videos = [s for s in streams if s.get('codec_type') == 'video']
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    if len(videos) != 1:
        raise RuntimeError(f'{target.name}: expected 1 video stream, got {len(videos)}.')
    video = videos[0]
    if (int(video['width']), int(video['height'])) != (width, height):
        raise RuntimeError(f"{target.name}: expected {width}x{height}, got {video['width']}x{video['height']}.")
    if video.get('codec_name') != 'h264':
        raise RuntimeError(f"{target.name}: expected h264, got {video.get('codec_name')}.")
    num, den = map(float, str(video.get('avg_frame_rate', '0/1')).split('/'))
    actual_fps = num / den if den else 0.0
    if abs(actual_fps - fps) > 0.001:
        raise RuntimeError(f'{target.name}: expected fps {fps}, got {actual_fps:.4f}.')
    packets = int(video.get('nb_read_packets') or 0)
    if packets != frames:
        raise RuntimeError(f'{target.name}: expected {frames} video packets, got {packets}.')
    video_duration = float(video.get('duration') or meta['format']['duration'])
    if abs(video_duration - expected_seconds) > max(0.05, 1.0 / fps + 0.001):
        raise RuntimeError(f'{target.name}: expected {expected_seconds:.3f}s, got {video_duration:.3f}s.')
    if expect_audio:
        if len(audios) != 1:
            raise RuntimeError(f'{target.name}: expected 1 audio stream, got {len(audios)}.')
        audio_duration = float(audios[0].get('duration') or meta['format']['duration'])
        if abs(audio_duration - video_duration) > 0.1:
            raise RuntimeError(
                f'{target.name}: audio/video duration mismatch ({audio_duration:.3f}s vs {video_duration:.3f}s).'
            )
    elif audios:
        raise RuntimeError(f'{target.name}: intermediate clip must not contain audio.')
    return meta


def _check_decode(path: Path, expected_seconds: float) -> None:
    result = subprocess.run(
        [_tool('ffmpeg'), '-nostdin', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=max(60, math.ceil(expected_seconds * 4)),
    )
    if result.returncode != 0:
        raise RuntimeError(f'Full decode check failed for {path.name}: {result.stderr.strip()}')


def _atomic_copy(source: Path, destination: Path) -> None:
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f'.{destination.name}.{os.getpid()}.tmp')
    try:
        shutil.copy2(source, temp)
        os.replace(temp, destination)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _should_retry(output_text: str, timed_out: bool) -> bool:
    if timed_out:
        return True
    lower = output_text.lower()
    if any(marker in lower for marker in _CONTRACT_MARKERS):
        return False
    return any(marker in lower for marker in _TRANSIENT_MARKERS)


def _render_single_clip(work: Path, idx: int, clip: dict, width: int, height: int, fps: int, force: bool) -> Path:
    build = work / 'build'
    build.mkdir(parents=True, exist_ok=True)
    frames = int(clip['frames'])
    duration = frames / fps

    if clip.get('video'):
        video = _inside(work, str(clip['video']), label=f'clip {idx} video')
        verify_video(video, width, height, fps, frames, expect_audio=False)
        return video

    entry = _inside(work, str(clip['html']), label=f'clip {idx} html').relative_to(work).as_posix()
    output = build / f'clip_{idx:03d}.mp4'
    log_path = build / f'render_{idx:03d}.log'
    env = subprocess_env()
    existing_pp = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = os.pathsep.join([str(ENGINE_ROOT), existing_pp]) if existing_pp else str(ENGINE_ROOT)

    cmd = [
        sys.executable, '-m', 'core.infra.motion.assembly', '_render-one',
        '--work', str(work),
        '--entry', entry,
        '--output', str(output),
        '--width', str(width),
        '--height', str(height),
        '--fps', str(fps),
        '--frames', str(frames),
    ]
    if force:
        cmd.append('--force')

    timeout = max(180, math.ceil(duration * 120))
    for attempt in range(1, MAX_ATTEMPTS + 1):
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(work),
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            combined = (proc.stdout or '') + ('\n' + proc.stderr if proc.stderr else '')
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            combined = f'Timed out after {timeout}s.\n{exc.stdout or ""}\n{exc.stderr or ""}'

        with log_path.open('a', encoding='utf-8') as log:
            log.write(f'\n=== clip {idx:03d} attempt {attempt}/{MAX_ATTEMPTS} ({stamp}) rc={returncode} ===\n')
            log.write(combined.strip() + '\n')

        if returncode == 0 and output.is_file():
            return output

        if attempt < MAX_ATTEMPTS and _should_retry(combined, timed_out):
            continue

        tail = '\n'.join(combined.strip().splitlines()[-12:])
        raise RuntimeError(f'Clip {idx:03d} ({entry}) failed after {attempt} attempt(s); log: {log_path}\n{tail}')

    raise RuntimeError(f'Clip {idx:03d} failed; log: {log_path}')


def _check_inputs(work: Path, manifest: dict, *, require_subtitles: bool | None = None) -> tuple[Path, Path | None]:
    fps = manifest['fps']
    total_seconds = sum(int(c['frames']) for c in manifest['clips']) / fps
    audio_raw = manifest.get('audio')
    if not audio_raw:
        raise ValueError('render.json must specify "audio".')
    audio = _inside(work, str(audio_raw), label='audio')
    if not audio.is_file():
        raise FileNotFoundError(f'Audio file not found: {audio}')
    audio_sec = _audio_duration(audio)
    if abs(audio_sec - total_seconds) >= 0.1:
        raise ValueError(
            f'Audio duration ({audio_sec:.3f}s) differs from manifest total ({total_seconds:.3f}s) by >= 0.1s.'
        )

    if require_subtitles is None:
        materials = work / 'materials.json'
        if materials.is_file():
            require_subtitles = bool(json.loads(materials.read_text(encoding='utf-8')).get('subtitles'))
        else:
            require_subtitles = False

    sub_raw = manifest.get('subtitles')
    subtitles = None
    if sub_raw:
        subtitles = _inside(work, str(sub_raw), label='subtitles')
        if not subtitles.is_file():
            raise FileNotFoundError(f'Subtitle file not found: {subtitles}')
    elif require_subtitles:
        raise ValueError('Subtitles are enabled in materials.json, but render.json has no "subtitles" path.')
    return audio, subtitles


def render_clips(
    work: Path | str,
    manifest_path: Path | str = 'render.json',
    *,
    workers: int | None = None,
    force: bool = False,
    only: list[int] | None = None,
) -> list[Path]:
    """Render all (or selected) clips from render.json using isolated worker processes."""
    work = Path(work).resolve()
    manifest = _load_manifest(work, manifest_path)
    width, height = _read_size(work, manifest)
    fps = manifest['fps']
    clips = manifest['clips']

    chosen = list(range(len(clips))) if only is None else sorted(set(only))
    for idx in chosen:
        if not (0 <= idx < len(clips)):
            raise ValueError(f'Clip index out of range: {idx}')

    raw_workers = workers if workers is not None else manifest.get('workers', DEFAULT_WORKERS)
    pool_size = max(1, min(MAX_WORKERS, int(raw_workers or DEFAULT_WORKERS), len(chosen)))
    if any(clips[idx].get('html') for idx in chosen):
        prepare_assets(work)

    results: dict[int, Path] = {}
    if pool_size == 1:
        for idx in chosen:
            results[idx] = _render_single_clip(work, idx, clips[idx], width, height, fps, force)
    else:
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            futures = {
                pool.submit(_render_single_clip, work, idx, clips[idx], width, height, fps, force): idx
                for idx in chosen
            }
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
    return [results[idx] for idx in chosen]


def _file_digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _compose_cache_matches(candidate: Path, record: Path, inputs: dict) -> bool:
    try:
        cached = json.loads(record.read_text(encoding='utf-8'))
        return isinstance(cached, dict) and cached.get('inputs') == inputs and cached.get('output') == _file_digest(candidate)
    except (OSError, ValueError):
        return False


def compose(
    work: Path | str,
    manifest_path: Path | str = 'render.json',
    *,
    delivery: Path | str | None = None,
    srt: Path | str | None = None,
) -> dict:
    """Compose rendered clips or reuse the verified video stream when only audio changes."""
    work = Path(work).resolve()
    manifest = _load_manifest(work, manifest_path)
    width, height = _read_size(work, manifest)
    fps = manifest['fps']
    clips = manifest['clips']
    total_frames = sum(int(c['frames']) for c in clips)
    total_seconds = total_frames / fps

    audio, subtitles = _check_inputs(work, manifest)
    build = work / 'build'
    build.mkdir(parents=True, exist_ok=True)

    clip_paths = [
        _inside(work, str(clip['video']), label=f'clip {idx} video')
        if clip.get('video') else build / f'clip_{idx:03d}.mp4'
        for idx, clip in enumerate(clips)
    ]
    for clip_path in clip_paths:
        if not clip_path.is_file():
            raise FileNotFoundError(f'Rendered clip missing: {clip_path}. Run render first.')
    video_sources = [*clip_paths, *([subtitles] if subtitles is not None else [])]
    source_stamps = [stamp(path) for path in video_sources]
    video_inputs = {
        'width': width, 'height': height, 'fps': fps,
        'clips': [[clip['frames'], _file_digest(path)] for clip, path in zip(clips, clip_paths)],
        'subtitles': _file_digest(subtitles) if subtitles is not None else None,
    }

    ffmpeg = _tool('ffmpeg')
    candidate = build / 'candidate_final.mp4'
    record = build / 'candidate_final.compose.json'
    temp_record = build / f'.candidate_final.{os.getpid()}.tmp.json'
    temp_candidate = build / f'.candidate_final.{os.getpid()}.tmp.mp4'
    temp_candidate.unlink(missing_ok=True)
    encode_timeout = max(120, math.ceil(total_seconds * 15))
    used_encoder = 'copy'
    reuse_video = _compose_cache_matches(candidate, record, video_inputs)

    if reuse_video:
        video_input = ['-i', str(candidate)]
    else:
        # Validate clips and give concat filenames it can interpret safely.
        concat_entries = []
        for idx, (clip, clip_path) in enumerate(zip(clips, clip_paths)):
            verify_video(clip_path, width, height, fps, clip['frames'], expect_audio=False)
            if clip_path.parent == build and clip_path.name.isascii() and "'" not in clip_path.name:
                concat_entries.append(f"file '{clip_path.name}'")
            else:
                staged = build / f'_src_{idx:03d}.mp4'
                staged.unlink(missing_ok=True)
                try:
                    os.link(clip_path, staged)
                except OSError:
                    shutil.copy2(clip_path, staged)
                concat_entries.append(f"file '{staged.name}'")

        concat_list = build / '_concat.txt'
        concat_list.write_text('\n'.join(concat_entries) + '\n', encoding='utf-8')

        video_input = ['-f', 'concat', '-safe', '0', '-i', str(concat_list)]

    try:
        if reuse_video or subtitles is None:
            subprocess.run(
                [
                    ffmpeg, '-nostdin', '-y', '-v', 'error',
                    *video_input, '-i', str(audio),
                    '-map', '0:v:0', '-map', '1:a:0',
                    '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                    '-movflags', '+faststart',
                    str(temp_candidate),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=True,
                timeout=encode_timeout,
            )
        else:
            merged_silent = build / f'._merged_silent.{os.getpid()}.mp4'
            staged_ass = build / f'_captions_{os.getpid()}.ass'
            try:
                subprocess.run(
                    [
                        ffmpeg, '-nostdin', '-y', '-v', 'error',
                        *video_input, '-c:v', 'copy', '-an', str(merged_silent),
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=encode_timeout,
                )
                shutil.copy2(subtitles, staged_ass)
                rel_ass = staged_ass.relative_to(work).as_posix()

                def _run_ffmpeg(*args):
                    return subprocess.run(
                        [ffmpeg, '-nostdin', '-y', '-v', 'error', *args],
                        cwd=str(work),
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=encode_timeout,
                    )

                result = encode(
                    _run_ffmpeg,
                    '-i', str(merged_silent),
                    '-i', str(audio),
                    '-map', '0:v:0', '-map', '1:a:0',
                    '-vf', f'ass={rel_ass}',
                    output=str(temp_candidate),
                    width=width,
                    height=height,
                    codec='h264',
                    extra=('-r', str(fps), '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart'),
                )
                used_encoder = getattr(result, 'aigc_encoder', select_encoder(ffmpeg, 'h264', width, height, sys.platform))
            finally:
                merged_silent.unlink(missing_ok=True)
                staged_ass.unlink(missing_ok=True)

        verify_video(temp_candidate, width, height, fps, total_frames, expect_audio=True)
        _check_decode(temp_candidate, total_seconds)
        if source_stamps != [stamp(path) for path in video_sources]:
            raise RuntimeError('Video fragments or subtitles changed during assembly. Run compose again.')
        temp_record.write_text(json.dumps({'inputs': video_inputs, 'output': _file_digest(temp_candidate)}), encoding='utf-8')
        os.replace(temp_candidate, candidate)
        os.replace(temp_record, record)
    finally:
        temp_candidate.unlink(missing_ok=True)
        temp_record.unlink(missing_ok=True)

    delivered_path = None
    delivered_srt = None
    if delivery is not None:
        delivery_target = Path(delivery).resolve()
        _atomic_copy(candidate, delivery_target)
        delivered_path = str(delivery_target)

        srt_source = None
        if srt is not None:
            srt_source = _inside(work, str(srt), label='srt')
        elif manifest.get('srt'):
            srt_source = _inside(work, str(manifest['srt']), label='srt')
        elif subtitles is not None and (build / 'captions.srt').is_file():
            srt_source = build / 'captions.srt'

        if srt_source is not None and srt_source.is_file():
            srt_target = delivery_target.with_suffix('.srt')
            _atomic_copy(srt_source, srt_target)
            delivered_srt = str(srt_target)

    return {
        'candidate': str(candidate),
        'delivery': delivered_path,
        'srt': delivered_srt,
        'encoder': used_encoder,
        'reused_video': reuse_video,
        'width': width,
        'height': height,
        'fps': fps,
        'frames': total_frames,
        'seconds': round(total_seconds, 3),
        'verified': True,
    }


def run_manifest(
    work: Path | str,
    manifest_path: Path | str = 'render.json',
    *,
    delivery: Path | str | None = None,
    srt: Path | str | None = None,
    workers: int | None = None,
    force: bool = False,
) -> dict:
    """Validate inputs, render all clips, compose, verify, and deliver."""
    work = Path(work).resolve()
    manifest = _load_manifest(work, manifest_path)
    _check_inputs(work, manifest)
    render_clips(work, manifest_path, workers=workers, force=force)
    return compose(work, manifest_path, delivery=delivery, srt=srt)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Step 5 assembly helper: render clips, compose, verify, and deliver.')
    sub = parser.add_subparsers(dest='command', required=True)

    p_cap = sub.add_parser('capabilities', help='Inspect local GPU, browser, and video encoders.')
    p_cap.add_argument('--work', default='.')
    p_cap.add_argument('--fps', type=int, default=30)

    p_snap = sub.add_parser('snapshot', help='Capture inspection screenshots for one HTML scene.')
    p_snap.add_argument('html')
    p_snap.add_argument('--work', default='.')
    p_snap.add_argument('--seconds', type=float, required=True)
    p_snap.add_argument('--fps', type=int, default=30)
    p_snap.add_argument('--at', required=True, help='Comma-separated timestamps in seconds.')
    p_snap.add_argument('--destination')

    p_render = sub.add_parser('render', help='Render all (or selected) clips from render.json.')
    p_render.add_argument('--work', default='.')
    p_render.add_argument('--manifest', default='render.json')
    p_render.add_argument('--workers', type=int)
    p_render.add_argument('--only', help='Comma-separated 0-based clip indices.')
    p_render.add_argument('--force', action='store_true')

    p_compose = sub.add_parser('compose', help='Concat clips, burn ASS subtitles, mux audio, verify, and deliver.')
    p_compose.add_argument('--work', default='.')
    p_compose.add_argument('--manifest', default='render.json')
    p_compose.add_argument('--delivery')
    p_compose.add_argument('--srt')

    p_all = sub.add_parser('all', help='Check inputs, render clips, compose, verify, and deliver.')
    p_all.add_argument('--work', default='.')
    p_all.add_argument('--manifest', default='render.json')
    p_all.add_argument('--delivery')
    p_all.add_argument('--srt')
    p_all.add_argument('--workers', type=int)
    p_all.add_argument('--force', action='store_true')

    # Internal isolated worker subcommand for render_clips.
    p_one = sub.add_parser('_render-one')
    p_one.add_argument('--work', required=True)
    p_one.add_argument('--entry', required=True)
    p_one.add_argument('--output', required=True)
    p_one.add_argument('--width', type=int, required=True)
    p_one.add_argument('--height', type=int, required=True)
    p_one.add_argument('--fps', type=int, required=True)
    p_one.add_argument('--frames', type=int, required=True)
    p_one.add_argument('--force', action='store_true')

    args = parser.parse_args(argv)
    if args.command == '_render-one':
        payload = {
            'width': args.width,
            'height': args.height,
            'durationSeconds': args.frames / args.fps,
            'fps': args.fps,
        }
        render_clip(
            Path(args.work),
            payload,
            Path(args.output),
            fps=args.fps,
            entry=args.entry,
            caption_zone=False,
            force=args.force,
            ignore_media=True,
        )
        return

    work = Path(args.work).resolve()
    if args.command == 'capabilities':
        width, height = _read_size(work, {})
        print(json.dumps(inspect_machine(work, {'width': width, 'height': height}, fps=args.fps), ensure_ascii=False, indent=2))
    elif args.command == 'snapshot':
        width, height = _read_size(work, {})
        source = _inside(work, args.html, label='html')
        entry = source.relative_to(work).as_posix()
        payload = {'width': width, 'height': height, 'durationSeconds': args.seconds, 'fps': args.fps}
        dest = _inside(work, args.destination, label='destination') if args.destination else work / 'build/snapshots' / source.stem
        times = [float(t) for t in args.at.split(',') if t.strip()]
        report = capture(work, payload, mode='snapshot', fps=args.fps, times=times, entry=entry, caption_zone=False, destination=dest)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.command == 'render':
        only = [int(x) for x in args.only.split(',') if x.strip()] if args.only else None
        rendered = render_clips(work, args.manifest, workers=args.workers, force=args.force, only=only)
        print(json.dumps({'rendered': [str(p) for p in rendered]}, ensure_ascii=False, indent=2))
    elif args.command == 'compose':
        report = compose(work, args.manifest, delivery=args.delivery, srt=args.srt)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.command == 'all':
        report = run_manifest(work, args.manifest, delivery=args.delivery, srt=args.srt, workers=args.workers, force=args.force)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
