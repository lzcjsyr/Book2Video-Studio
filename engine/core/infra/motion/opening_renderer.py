import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import config
from core.domain.metadata import get_primary_golden_quote, parse_marked_focus_text
from core.infra.motion.runtime import parse_size, prepare
from core.infra.motion.renderer import render_clip
from core.shared import logger


_QUOTE_BREAK_PUNCTUATION = {"。", "！", "？", "!", "?", "；", ";", "，", ",", "：", ":"}


def _motion_app_dir() -> Path:
    return Path(__file__).resolve().parent / "app"


def _split_quote_fragments(quote: str) -> List[str]:
    fragments: List[str] = []
    normalized = (quote or "").replace("\r\n", "\n").replace("\r", "\n")

    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        current = ""
        for char in line:
            current += char
            if char in _QUOTE_BREAK_PUNCTUATION:
                cleaned = current.strip()
                if cleaned:
                    fragments.append(cleaned)
                current = ""

        cleaned = current.strip()
        if cleaned:
            fragments.append(cleaned)

    return fragments


def _split_quote_lines(quote: str, max_chars: int = 20, max_lines: int = 6) -> List[str]:
    from core.domain.subtitles import split_text_for_subtitle
    lines: List[str] = []

    for fragment in _split_quote_fragments(quote):
        if len(fragment) <= max_chars:
            lines.append(fragment)
            continue

        lines.extend(split_text_for_subtitle(fragment, max_chars))

    if max_lines > 0 and len(lines) > max_lines:
        return lines[: max_lines - 1] + ["".join(lines[max_lines - 1 :])]
    return lines


def _pick_focus_words(lines: List[str]) -> List[str]:
    for candidate in ("系统", "规则", "机制", "权力", "金钱", "认知"):
        if any(candidate in line for line in lines):
            return [candidate]
    return []


def _build_line_appear_times(
    line_count: int,
    first_line_seconds: float,
    last_line_seconds: float,
) -> List[float]:
    if line_count <= 0:
        return []
    if line_count == 1:
        return [round(first_line_seconds, 3)]

    step = (last_line_seconds - first_line_seconds) / (line_count - 1)
    return [round(first_line_seconds + step * index, 3) for index in range(line_count)]


def _resolve_timeline_config(motion: Optional[Dict[str, Any]] = None) -> tuple[float, int, int]:
    motion = motion if motion is not None else _configured_motion()
    duration_seconds = float(motion.get("duration_seconds") or 4.0)
    fps = int(motion.get("fps") or 30)

    duration_seconds = max(0.1, duration_seconds)
    fps = max(1, fps)
    duration_in_frames = max(1, round(duration_seconds * fps))
    return duration_seconds, fps, duration_in_frames


def _resolve_line_timing(duration_seconds: float, fps: int, motion: Optional[Dict[str, Any]] = None) -> tuple[float, float]:
    motion = motion if motion is not None else _configured_motion()
    first_line_seconds = float(motion.get("first_line_seconds") or 0.5)
    last_line_seconds = float(motion.get("last_line_seconds") or 2.0)

    max_visible_time = max(0.0, duration_seconds - (1.0 / fps))
    first_line_seconds = min(max(0.0, first_line_seconds), max_visible_time)
    last_line_seconds = min(max(first_line_seconds, last_line_seconds), max_visible_time)
    return first_line_seconds, last_line_seconds


def _configured_motion() -> Dict[str, Any]:
    """The opening's settings (the motion_opening section) as the engine is configured."""
    return {
        "ip_name": getattr(config, "OPENING_MOTION_IP_NAME", ""),
        "duration_seconds": getattr(config, "OPENING_MOTION_DURATION_SECONDS", 4.0),
        "fps": getattr(config, "OPENING_MOTION_FPS", 30),
        "first_line_seconds": getattr(config, "OPENING_MOTION_FIRST_LINE_SECONDS", 0.5),
        "last_line_seconds": getattr(config, "OPENING_MOTION_LAST_LINE_SECONDS", 2.0),
        "max_lines": getattr(config, "OPENING_MOTION_MAX_LINES", 6),
        "max_chars_per_line": getattr(config, "OPENING_MOTION_MAX_CHARS_PER_LINE", 20),
    }


def opening_props(script_data: Optional[Dict[str, Any]], motion: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """What the opening template shows: the quote's lines and timing, the book title and the masthead.

    The layout is authored on a 2560×1440 canvas; a page that sets outputWidth/outputHeight scales it.
    ``motion`` is the motion_opening settings; without it the configured engine's are used."""
    motion = motion if motion is not None else _configured_motion()
    quote = get_primary_golden_quote(script_data or {}, "").strip()
    clean_quote, marked_focus_words = parse_marked_focus_text(quote)
    if not clean_quote:
        return None
    quote_lines = _split_quote_lines(clean_quote, int(motion.get("max_chars_per_line") or 20), int(motion.get("max_lines") or 6))
    duration_seconds, fps, _ = _resolve_timeline_config(motion)
    first_line_seconds, last_line_seconds = _resolve_line_timing(duration_seconds, fps, motion)
    raw_source_name = (script_data or {}).get("source_name")
    book_title = raw_source_name.strip() if isinstance(raw_source_name, str) else ""
    return {
        "bookTitle": f"——{book_title or '未命名作品'}——",
        "quoteLines": quote_lines,
        "focusWords": marked_focus_words or _pick_focus_words(quote_lines),
        "ipName": str(motion.get("ip_name") or "").strip(),
        "lineAppearTimes": _build_line_appear_times(len(quote_lines), first_line_seconds=first_line_seconds,
                                                    last_line_seconds=last_line_seconds),
        "width": 2560,
        "height": 1440,
        "durationSeconds": duration_seconds,
    }


def render_opening_video(
    *,
    image_size: str,
    output_dir: str,
    script_data: Optional[Dict[str, Any]],
    opening_quote: bool = True,
    opening_bg_path: Optional[str] = None,
) -> Optional[str]:
    if not opening_quote:
        return None

    # The opening template is authored on a 2560×1440 canvas.  Rendering it
    # at a smaller image-generation size changes its fixed-pixel layout; the
    # final composer is responsible for scaling this video to the target size.
    parse_size(image_size)
    props = opening_props(script_data)
    if not props:
        return None
    app_dir = _motion_app_dir()
    output_path = Path(output_dir).resolve() / "opening.mp4"
    fps = _resolve_timeline_config()[1]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="aigc-opening-") as temporary:
        work_dir = Path(temporary) / "app"
        shutil.copytree(app_dir, work_dir)
        if opening_bg_path and os.path.exists(opening_bg_path):
            try:
                shutil.copy2(opening_bg_path, work_dir / "opening_bg.png")
                props["backgroundImage"] = "opening_bg.png"
            except OSError as e:
                logger.warning(f"Failed to copy opening background: {e}")

        prepare(work_dir, props)
        try:
            print("🎬 正在生成 HTML 开场视频…")
            render_clip(work_dir, props, output_path, fps=fps, caption_zone=False)
            logger.info("Opening HTML video rendered: %s", output_path)
            return str(output_path)
        except Exception as exc:
            logger.warning("Opening HTML render failed: %s", exc)
            return None
