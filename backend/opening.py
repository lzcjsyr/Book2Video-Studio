"""The opening clip: the opening quote drawn over the chosen background, made at assembly.

Visuals only makes the background (images/opening_bg.png). Assembly renders the clip from it,
the quote and the source name into a working folder, and renders again only when one of them
(or the opening's motion settings) changed since the last render.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

CLIP = ".studio/opening/opening.mp4"
RECORD = ".studio/opening/opening.json"
BACKGROUND = "images/opening_bg.png"


def first_text(value) -> str:
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def script_of(project: Path) -> dict:
    path = project / "text/script.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def signature(project: Path, motion: dict) -> str:
    """What the clip shows: the quote, the source name under it, the background and the opening's timing."""
    script = script_of(project)
    background = project / BACKGROUND
    # Studio places backgrounds with their own modification time, so size and time name the picture.
    stamp = f"{background.stat().st_size}:{background.stat().st_mtime_ns}" if background.is_file() else None
    return hashlib.sha256(json.dumps({
        "quote": first_text(script.get("golden_quotes")), "source": str(script.get("source_name") or "").strip(),
        "background": stamp, "motion": {k: motion.get(k) for k in sorted(motion)},
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def current(project: Path, motion: dict) -> Path | None:
    """The rendered clip when it still matches what it shows, else None."""
    clip, record = project / CLIP, project / RECORD
    try:
        if clip.is_file() and json.loads(record.read_text(encoding="utf-8")).get("signature") == signature(project, motion):
            return clip
    except (OSError, ValueError):
        pass
    return None


def ensure(project: Path, motion: dict, image_size: str) -> Path:
    """Render the opening clip unless the last one is still current. Runs with the engine configured."""
    if found := current(project, motion):
        print("Using the rendered opening (unchanged since the last assembly).", flush=True)
        return found
    from core.infra.motion.opening_renderer import render_opening_video
    script = script_of(project)
    if not first_text(script.get("golden_quotes")):
        raise ValueError("Choose and save an opening quote in Writing, or turn the opening quote off.")
    background = project / BACKGROUND
    print("Rendering the opening quote over its background…", flush=True)
    output = project / CLIP
    output.parent.mkdir(parents=True, exist_ok=True)
    video = render_opening_video(image_size=image_size, output_dir=str(output.parent), script_data=script,
                                 opening_quote=True, opening_bg_path=str(background) if background.is_file() else None)
    if not video or not output.is_file():
        raise ValueError("The opening could not be rendered. Check the opening quote and background, then try again.")
    (project / RECORD).write_text(json.dumps({"signature": signature(project, motion)}), encoding="utf-8")
    return output
