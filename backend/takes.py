"""Every still image and motion clip a part of the video has had, and the one it uses.

The engine assembles images/segment_N.(png|mp4) (the opening: images/opening.*) and prefers a
still. Each part keeps its takes in images/takes/<segment_N|opening>/: stills as
still-<id>.<ext>, motion clips as motion-<id>/ folders holding Claude Code's index.html, its
snapshots/ and the rendered video.mp4. Studio copies the selected take into the engine's place,
and new output the engine writes there becomes a take, so nothing a segment had is lost.

The opening is a clip the engine renders from a background picture (images/opening_bg.png) and
the opening quote. Every background is kept as background-<id>.<ext>; it is never placed itself,
since the quote is drawn over it: choosing one renders a new opening clip with it.
"""
from __future__ import annotations
import json

import filecmp
import shutil
import subprocess
import time
import uuid
from pathlib import Path

STILLS = [".png", ".jpg", ".jpeg"]
VIDEOS = [".mp4", ".mov", ".webm"]
ROOT = "images/takes"
BACKGROUND = "images/opening_bg.png"


def stem(index: int) -> str:
    return "opening" if index == 0 else f"segment_{index}"


def folder(p: Path, index: int) -> Path:
    return p / ROOT / stem(index)


def new_id(kind: str, when: float | None = None) -> str:
    """kind-YYYYMMDD-HHMMSS-mmm-xxxx: the time it was made (to the millisecond), then a tie-breaker."""
    when = time.time() if when is None else when
    return f"{kind}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(when))}-{int(when * 1000) % 1000:03d}-{uuid.uuid4().hex[:4]}"


def made(take: str) -> float:
    """When a take was made, from its id (ids from before milliseconds have none)."""
    parts = take.split("-")
    seconds = time.mktime(time.strptime(parts[1] + parts[2], "%Y%m%d%H%M%S"))
    return seconds + (int(parts[3]) / 1000 if len(parts) > 4 and parts[3].isdigit() else 0)


def same(a: Path, b: Path) -> bool:
    first, second = a.stat(), b.stat()
    if first.st_size != second.st_size:
        return False
    # Studio's copies keep the modification time, so most comparisons never read the files.
    return first.st_mtime_ns == second.st_mtime_ns or filecmp.cmp(a, b, shallow=False)


def placed(p: Path, index: int, extensions) -> list[Path]:
    """The files in the engine's place for this part."""
    return [f for ext in extensions if (f := p / "images" / (stem(index) + ext)).is_file()]


def video_in(work: Path) -> Path | None:
    return next((f for ext in VIDEOS if (f := work / ("video" + ext)).is_file()), None)


def listing(p: Path, index: int) -> list[dict]:
    takes = []
    d = folder(p, index)
    if d.is_dir():
        for entry in d.iterdir():
            if entry.is_file() and entry.name.startswith("still-") and entry.suffix.lower() in STILLS:
                takes.append({"id": entry.stem, "kind": "still", "file": entry, "dir": None})
            elif entry.is_file() and entry.name.startswith("background-") and entry.suffix.lower() in STILLS:
                takes.append({"id": entry.stem, "kind": "background", "file": entry, "dir": None})
            elif entry.is_dir() and entry.name.startswith("motion-"):
                takes.append({"id": entry.name, "kind": "motion", "file": video_in(entry), "dir": entry})
    return sorted(takes, key=lambda t: (made(t["id"]), t["id"]))


def motion(p: Path, work: Path) -> dict:
    """Where a motion clip stands: Claude Code's composition, its keyframes, and the rendered video."""
    html = work / "index.html"
    composed = html.stat().st_mtime if html.is_file() else None
    frames = sorted((work / "snapshots").glob("frame-*.png")) or sorted((work / "snapshots").glob("*.png"))
    video = video_in(work)
    verified = None
    if video and video.with_suffix('.render.json').is_file():
        try:
            from core.infra.motion.artifacts import current
            from core.infra.motion.renderer import verify_sources
            payload=json.loads((work/'studio-payload.json').read_text())
            verify_sources(payload)
            verified=current(work,payload,video,fps=int(payload.get('fps',30)))
        except (OSError,ValueError,KeyError,TypeError):verified=False
    try:progress=json.loads((work/'render-status.json').read_text())
    except (OSError,ValueError):progress=None
    # Keyframes and renders made before the latest composition edit are outdated (1 s for coarse clocks).
    return {"progress":progress,"composition": str(html.relative_to(p)) if composed else None, "composed": composed,
            "folder": str(work.relative_to(p)),
            "keyframes": [str(f.relative_to(p)) for f in frames],
            "keyframes_current": bool(frames) and composed is not None and min(f.stat().st_mtime for f in frames) >= composed - 1,
            "video": str(video.relative_to(p)) if video else None,
            "video_current": verified if verified is not None else bool(video) and (composed is None or video.stat().st_mtime >= composed - 1)}


def poster(work: Path, video: Path | None) -> Path | None:
    """A frame from the middle of a clip's video, for clips without keyframes (such as the rendered opening).

    Saved beside the video as poster.jpg and made again only when the video changes."""
    if not video:
        return None
    frame = work / "poster.jpg"
    if frame.is_file() and frame.stat().st_mtime >= video.stat().st_mtime:
        return frame
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return None
    try:
        probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
                               capture_output=True, text=True, timeout=10)
        middle = float(probe.stdout.strip() or 0) / 2
        temporary = work / "poster.tmp.jpg"
        subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{middle:.2f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=960:-2", str(temporary)],
                       capture_output=True, timeout=20, check=True)
        temporary.replace(frame)
        return frame
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def describe(p: Path, take: dict) -> dict:
    created = made(take["id"])
    if take["kind"] in {"still", "background"}:
        path = str(take["file"].relative_to(p))
        return {"id": take["id"], "kind": take["kind"], "created": created, "path": path, "poster": path}
    info = motion(p, take["dir"])
    frames = info["keyframes"]
    frame = frames[len(frames) // 2] if frames else (str(f.relative_to(p)) if (f := poster(take["dir"], take["file"])) else None)
    return {"id": take["id"], "kind": "motion", "created": created, "path": info["video"], "poster": frame, "motion": info}


def add_still(p: Path, index: int, source: Path, move: bool = False) -> str:
    d = folder(p, index)
    d.mkdir(parents=True, exist_ok=True)
    take = new_id("still", source.stat().st_mtime)
    (shutil.move if move else shutil.copy2)(source, d / (take + source.suffix.lower()))
    return take


def add_motion(p: Path, index: int, work: Path | None = None, video: Path | None = None) -> str:
    """A new motion clip: an existing composition folder, a rendered video, or an empty folder for Claude Code."""
    d = folder(p, index)
    d.mkdir(parents=True, exist_ok=True)
    when = (work / "index.html").stat().st_mtime if work and (work / "index.html").is_file() else video.stat().st_mtime if video else None
    take = new_id("motion", when)
    if work:
        shutil.move(work, d / take)
    else:
        (d / take).mkdir()
    if video:
        for old in VIDEOS:
            (d / take / ("video" + old)).unlink(missing_ok=True)
        shutil.copy2(video, d / take / ("video" + video.suffix.lower()))
    return take


def keep_background(p: Path) -> None:
    """Keep the opening background the engine made last, if it is new."""
    current = p / BACKGROUND
    if current.is_file() and not any(t["kind"] == "background" and same(t["file"], current) for t in listing(p, 0)):
        d = folder(p, 0)
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, d / (new_id("background", current.stat().st_mtime) + current.suffix.lower()))


def recover_backgrounds(p: Path) -> None:
    """Backgrounds from before Studio kept them: every opening job saved the one it replaced in revision history."""
    kept = [t["file"] for t in listing(p, 0) if t["kind"] == "background"]
    earlier = sorted((f for f in (p / ".studio/history").glob("*/images/opening_bg.*") if f.suffix.lower() in STILLS), key=lambda f: f.stat().st_mtime)
    for old in earlier:
        if not any(same(f, old) for f in kept):
            d = folder(p, 0)
            d.mkdir(parents=True, exist_ok=True)
            target = d / (new_id("background", old.stat().st_mtime) + old.suffix.lower())
            shutil.copy2(old, target)
            kept.append(target)


def current_background(p: Path) -> str | None:
    """The background the engine renders the next opening with."""
    current = p / BACKGROUND
    if not current.is_file():
        return None
    return next((t["id"] for t in listing(p, 0) if t["kind"] == "background" and same(t["file"], current)), None)


def keep(p: Path, index: int, file: Path, takes: list[dict]) -> None:
    """Make sure a file in the engine's place is kept as a take before Studio replaces it."""
    kind = "still" if file.suffix.lower() in STILLS else "motion"
    if not any(t["kind"] == kind and t["file"] and same(t["file"], file) for t in takes):
        add_still(p, index, file) if kind == "still" else add_motion(p, index, video=file)


def migrate(p: Path, index: int, visualizer: str) -> str | None:
    """Projects from before takes kept one still (in use, or set aside in images/stills/) and one composition."""
    still = motion_take = None
    for parked in [f for ext in STILLS if (f := p / "images/stills" / (stem(index) + ext)).is_file()]:
        still = add_still(p, index, parked, move=True)
    for current in placed(p, index, STILLS)[:1]:
        still = add_still(p, index, current)
    work = p / "images/motion" / stem(index)
    if not (work / "index.html").is_file():
        work = p / "images/hyperframes" / stem(index)
    video = next(iter(placed(p, index, VIDEOS)), None)
    if (work / "index.html").is_file() or video:
        motion_take = add_motion(p, index, work if (work / "index.html").is_file() else None, video)
    folder(p, index).mkdir(parents=True, exist_ok=True)
    return motion_take if visualizer == "hf" else still


def sync(p: Path, index: int, selection: str | None, visualizer: str) -> str | None:
    """Adopt new engine output as a take, then put the selected take in the engine's place."""
    if not folder(p, index).is_dir():
        # The segment's type decides which of its migrated files it uses.
        selection = migrate(p, index, visualizer)
        place(p, index, selection)
        return selection
    takes = listing(p, index)
    still = next(iter(placed(p, index, STILLS)), None)
    video = next(iter(placed(p, index, VIDEOS)), None)
    chosen = next((t for t in takes if t["id"] == selection), None)
    if still and not (chosen and chosen["kind"] == "still" and same(chosen["file"], still)):
        # A new image from the engine (or a restored revision) becomes the segment's visual.
        match = next((t for t in takes if t["kind"] == "still" and same(t["file"], still)), None)
        selection = match["id"] if match else add_still(p, index, still)
    elif not still and video and not (chosen and chosen["kind"] == "motion" and chosen["file"] and same(chosen["file"], video)):
        match = next((t for t in takes if t["kind"] == "motion" and t["file"] and same(t["file"], video)), None)
        selection = match["id"] if match else add_motion(p, index, video=video)
    elif not still and not video and selection and not chosen:
        selection = None
    place(p, index, selection)
    return selection


def place(p: Path, index: int, selection: str | None) -> None:
    """Put the selected take's file in the engine's place; nothing there when it has none yet."""
    takes = listing(p, index)
    chosen = next((t for t in takes if t["id"] == selection), None)
    source = chosen["file"] if chosen else None
    target = p / "images" / (stem(index) + source.suffix.lower()) if source else None
    for current in placed(p, index, STILLS + VIDEOS):
        if current != target:
            keep(p, index, current, takes)
            current.unlink()
    if source and not (target.is_file() and same(source, target)):
        shutil.copy2(source, target)


def kind_of(selection: str | None) -> str | None:
    """The engine's visualizer for the selected take: image for a still, hf for motion."""
    if not selection:
        return None
    return "image" if selection.startswith("still-") else "hf"


def newest(p: Path, index: int, kind: str) -> str | None:
    """The take a segment switching to this kind uses: its newest still, or its newest rendered clip."""
    takes = [t for t in listing(p, index) if t["kind"] == kind]
    if kind == "motion":
        rendered = [t for t in takes if t["file"] and motion(p, t["dir"])["video_current"]]
        takes = rendered or takes
    return takes[-1]["id"] if takes else None


def ready(p: Path, index: int, selection: str | None) -> bool:
    """The part has a current visual: a still, or a clip rendered from its latest composition."""
    chosen = find(p, index, selection) if selection else None
    if not chosen or not chosen["file"]:
        return False
    return chosen["kind"] == "still" or motion(p, chosen["dir"])["video_current"]


def find(p: Path, index: int, take: str) -> dict | None:
    return next((t for t in listing(p, index) if t["id"] == take), None)
