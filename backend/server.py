"""Local project service. The Tauri host owns the process and its random bearer token."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import wave
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from library import Library, LibraryConflict, install_bundled
import takes
import covers as cover_designs
import editing
import exports

STAGES = ["1", "2", "3", "4", "5", "6"]
# The CLI engine numbers Segments 1.5 and keeps the content summary (part of step 4 here) as its
# step 2. Studio uses its own step numbers and translates settings at the engine boundary.
ENGINE_SECTIONS = {"step2": "step1_5", "step4_summary": "step2"}
DESKTOP_SECTIONS = {engine: desktop for desktop, engine in ENGINE_SECTIONS.items()}
SETTINGS_SECTIONS = {"global", "step1", "step2", "step3", "step4", "step4_summary", "step5", "three_by_four", "step6", "subtitles", "motion_opening"}
STATE_LAYOUT = 2
# Which service or model makes the next output. Changing one leaves finished work current.
PROVIDER_SETTINGS = {"llm_server", "llm_model", "image_server", "image_model", "cover_image_server", "cover_image_model", "tts_model", "cover_method", "assembly_method", "agent_skill"}
LINKS_FILE = "projects.json"
HIDDEN_FILE = "hidden-projects.json"
GOOGLE_CHECK_FILE = "google-adc-check.json"
LIBRARY_ENV = "AIGC_VIDEO_LIBRARY_DIR"
VOICES_FILE = "voices.json"
DEFAULT_VOICE = {"id": "S_MKj17k802", "name": "默认音色"}
MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".mp4", ".mov", ".webm", ".srt", ".pdf", ".txt"}
# Background music formats; the engine reads them through FFmpeg.
MUSIC = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
# The still the Assembly page shows for the 3:4 copy's panels.
PANEL_PREVIEW = ".studio/preview_3x4.png"
RENDER_REASON = "Render the revised motion compositions to review the new visuals"
# Outdated-visual reasons that only concern a segment's previous visual type.
TYPE_REASONS = {"Visual type changed", RENDER_REASON}
TEXT_FILES = {"raw": "raw.json", "script": "script.json", "summary": "mini_summary.json"}
METADATA_FIELDS = ("video_titles", "cover_titles", "cover_subtitles", "golden_quotes")
# Settings the desktop keeps for itself; the engine's configuration rejects unknown keys.
DESKTOP_SETTINGS = {"step1": {"writing_mode"}, "step6": {"cover_method"}, "step5": {"assembly_method", "agent_skill"}}
# Covers are drawn by an image service from the text alone and write only cover files, so a cover job
# runs beside the jobs that leave the text as it is: recordings, visuals and assembly.
BESIDE_COVERS = {"3", "4", "5"}
PROVIDER_KEYS = {"OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "MIMO_API_KEY", "KIMI_API_KEY", "SILICONFLOW_KEY", "VOLCENGINE_API_KEY", "GOOGLE_CLOUD_API_KEY", "BYTEDANCE_TTS_API_KEY"}


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        if default is not None:
            return default
        raise


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def segment_of(relative: str) -> int | None:
    """The segment a media file belongs to (0 for the opening), or None (subtitles, logs)."""
    if match := re.search(r"(?:^|/)(?:voice|segment)_(\d+)(?=[./]|$)", relative):
        return int(match.group(1))
    return 0 if re.search(r"(?:^|/)(?:voice/opening|images/(?:segment_)?opening)", relative) else None


def first_text(value) -> str:
    """The candidate the engine uses: the first nonblank text of a metadata list."""
    items = value if isinstance(value, list) else [value]
    return next((v.strip() for v in items if isinstance(v, str) and v.strip()), "")


def cover_copy(data: dict) -> tuple:
    """The text a cover shows (engine step 6): the cover title and subtitle; the video title only stands in for a missing one."""
    title = first_text(data.get("video_titles")) or "未命名视频"
    return first_text(data.get("cover_titles")) or title, first_text(data.get("cover_subtitles")) or title


def panel_copy(data: dict, panel: dict) -> tuple:
    """The text the 3:4 copy's panels show (engine select_panel_copy), after the panel's own choices."""
    title = panel.get("title_text") or first_text(data.get("cover_titles")) or first_text(data.get("video_titles")) or first_text(data.get("source_name"))
    subtitle = panel.get("subtitle_text") or first_text(data.get("cover_subtitles"))
    top = panel.get("top_content", "title_subtitle")
    if top == "none":
        title = subtitle = ""
    elif top == "title":
        subtitle = ""
    bottom = first_text(data.get("golden_quotes")) if panel.get("bottom_content") == "quote" else ""
    return title, subtitle, bottom


def editable(value: dict) -> dict:
    """What an edit can change in raw.json or script.json; the counts the app recomputes are left out."""
    kept = {k: v for k, v in value.items() if k not in {"total_length", "actual_segments"}}
    if isinstance(value.get("segments"), list):
        kept["segments"] = [{k: v for k, v in s.items() if k != "length"} for s in value["segments"]]
    return kept


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def raw_problem(raw) -> str | None:
    """Why a Writing agent's raw.json cannot be used by the later steps, or None when it can."""
    if not isinstance(raw, dict):
        return "raw.json is not a JSON object."
    for key in ("source_name", "content"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            return f"{key} must be non-empty text."
    for key in METADATA_FIELDS:
        if not isinstance(raw.get(key), list) or any(not isinstance(x, str) for x in raw[key]):
            return f"{key} must be a list of separate text candidates."
    return None


def stray_raw(project: Path) -> Path | None:
    """A usable manuscript the agent saved under a legacy output/<name>/text folder instead of text/."""
    found = [f for f in project.glob("output/**/raw.json") if f.is_file()]
    for f in sorted(found, key=lambda f: f.stat().st_mtime, reverse=True):
        if raw_problem(read_json(f, {})) is None:
            return f
    return None


def duration(path: Path | None) -> float | None:
    if not path:
        return None
    try:
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as f:
                return round(f.getnframes() / f.getframerate(), 3)
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in DURATION_CACHE:
            ffprobe = shutil.which("ffprobe")
            if not ffprobe:
                return None
            result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, timeout=10)
            DURATION_CACHE[key] = float(result.stdout.strip()) if result.returncode == 0 else None
        return DURATION_CACHE[key]
    except (OSError, ValueError, wave.Error, subprocess.SubprocessError):
        return None


DURATION_CACHE = {}


def engine_settings(settings):
    """Project settings as the engine's configuration accepts them (its section names, no desktop-only keys)."""
    return {ENGINE_SECTIONS.get(section, section): {k: v for k, v in values.items() if k not in DESKTOP_SETTINGS.get(section, ())}
            for section, values in settings.items()}


def migrate_segmentation(settings):
    """The removed evenly split mode now uses punctuation boundaries."""
    if settings.get("step2", {}).get("split_mode") == "auto":
        settings["step2"]["split_mode"] = "punctuation"


def segmentation_output(values):
    """Target count affects only agent segmentation."""
    result = dict(values)
    if result.get("split_mode", "punctuation") != "agent":
        result.pop("num_segments", None)
    return result


def migrate_state(state):
    """Projects from earlier builds used the engine's numbering: Segments was stage "1.5" with settings
    step1_5, step2 held the content summary settings, and a stage "2" (Context) predates its merge into Visuals."""
    settings = state.setdefault("settings", {})
    if "hyperframes_opening" in settings:
        settings.setdefault("motion_opening", settings.pop("hyperframes_opening"))
    motion = settings.get("step4", {})
    for suffix in ("style_preset", "max_turns", "render_fps", "concurrency"):
        old = "hyperframes_" + suffix
        if old in motion:
            motion.setdefault("motion_" + suffix, motion.pop(old))
    if motion.get("visual_mode") == "hyperframes_agent":
        motion["visual_mode"] = "motion_agent"
    if state.get("layout", 1) >= STATE_LAYOUT:
        migrate_segmentation(settings)
        return state
    stale, reviewed = state.setdefault("stale", {}), state.setdefault("reviewed", {})
    if stale.pop("2", None):
        state["summary_stale"] = True
    reviewed.pop("2", None)
    for table in (stale, reviewed):
        if "1.5" in table:
            table["2"] = table.pop("1.5")
    settings = state.setdefault("settings", {})
    if "step2" in settings:
        settings["step4_summary"] = settings.pop("step2")
    if "step1_5" in settings:
        settings["step2"] = settings.pop("step1_5")
    state["layout"] = STATE_LAYOUT
    migrate_segmentation(settings)
    return state


class Conflict(Exception):
    pass


class Studio:
    @property
    def library(self):
        return Library(self.library_root)

    def __init__(self, engine: Path, data: Path, workspace: Path):
        # The engine (code, bundled prompts and skills, music, defaults) is read-only.
        self.engine = engine.resolve()
        if not (self.engine / "core/pipeline/steps.py").exists():
            raise ValueError("The production engine is missing. Reinstall Book2Video Studio.")
        # User data: the editable library and the registry of projects kept
        # outside the workspace, the default folder for new projects.
        self.data = data.resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        # The native host also gives this path to the service and agents as AIGC_VIDEO_LIBRARY_DIR.
        self.library_root = self.data / "library"
        install_bundled(self.engine, self.library_root)
        # Music the user adds; the bundled tracks stay in the read-only engine.
        self.music_root = self.data / "music"
        self.lock = threading.RLock()
        self.preview_lock = threading.Lock()
        self.jobs = {}
        # Received from the native host; never included in project data or API responses.
        self.credentials = {}
        # A terminal lease belongs to this service instance; recover after an app crash.
        for path in (root / ".studio/state.json" for root in self.roots()):
            recovery_error = False
            try:
                exports.recover(path.parents[1])
            except (OSError, ValueError, KeyError, TypeError):
                recovery_error = True
            try:
                state = read_json(path, {})
            except OSError:
                continue  # Unreadable (for example, folder access was not granted); listed as such.
            if recovery_error:
                state.setdefault("stale", {})["5"] = {"reason": "Export recovery needs review. Previous backups are retained.", "segments": []}
                atomic_json(path, state)
            active = state.get("agent_active")
            if active:
                try:
                    os.kill(active.get("owner", -1), 0) if active.get("owner") else (_ for _ in ()).throw(ProcessLookupError())
                    continue
                except (ProcessLookupError, PermissionError):
                    pass
                state.pop("agent_active", None)
                for index in active.get("created", []):
                    # A new motion clip the interrupted session never composed goes; its segment's previous visual returns.
                    work = takes.folder(path.parents[1], index) / active["takes"][str(index)]
                    if not (work / "index.html").is_file():
                        shutil.rmtree(work, ignore_errors=True)
                        previous_take = active.get("previous", {}).get(str(index))
                        state.setdefault("takes", {})[str(index)] = previous_take
                        if not previous_take:
                            state["takes"].pop(str(index))
                stale = state.setdefault("stale", {})
                previous, indices = stale.get(active["step"]), active.get("indices") or []
                # Segments that were already outdated stay outdated (an empty list means all of them).
                if not (previous and not previous["segments"]):
                    stale[active["step"]] = {"reason": "Previous agent session was interrupted. Review its outputs.",
                                             "segments": sorted(set(previous["segments"]) | set(indices)) if previous and indices else indices}
                atomic_json(path, state)

    @property
    def links_path(self) -> Path:
        return self.data / LINKS_FILE

    def links(self) -> dict:
        """Projects stored outside the workspace: id -> absolute project folder."""
        data = read_json(self.links_path, {})
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def link(self, path: Path) -> str:
        """Remember a project folder outside the workspace so it appears in the list."""
        path = path.resolve()
        with self.lock:
            links = self.links()
            for known, location in links.items():
                if Path(location) == path:
                    return known
            safe = re.sub(r'[\\/:*?"<>|]', "_", path.name)
            project_id = f"ext-{hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:8]}-{safe}"
            links[project_id] = str(path)
            atomic_json(self.links_path, links)
            return project_id

    @property
    def hidden_path(self) -> Path:
        return self.data / HIDDEN_FILE

    def hidden(self) -> set:
        """Project folders taken off the list but kept on disk (absolute paths)."""
        data = read_json(self.hidden_path, [])
        return {x for x in data if isinstance(x, str)} if isinstance(data, list) else set()

    def show(self, path: Path):
        """A folder that is added or created again must not stay hidden."""
        with self.lock:
            hidden = self.hidden()
            if str(path.resolve()) in hidden:
                atomic_json(self.hidden_path, sorted(hidden - {str(path.resolve())}))

    def roots(self) -> list:
        """Every existing project folder on the list: those in the workspace, then linked ones."""
        roots = []
        try:
            roots = [p for p in self.workspace.iterdir() if (p / "text").is_dir()]
        except OSError:
            pass
        known = {p.resolve() for p in roots}
        for location in self.links().values():
            p = Path(location)
            if (p / "text").is_dir() and p.resolve() not in known:
                roots.append(p)
        hidden = self.hidden()
        return [p for p in roots if str(p.resolve()) not in hidden]

    def project_id(self, p: Path) -> str:
        workspace = getattr(self, "workspace", None)
        if workspace is None or p.resolve().parent == workspace:
            return p.name
        for known, location in self.links().items():
            if Path(location) == p.resolve():
                return known
        return p.name

    def project(self, project_id: str) -> Path:
        linked = self.links().get(project_id)
        if linked:
            result = Path(linked).resolve()
            if not (result / "text").is_dir():
                raise ValueError("The project folder was moved or deleted.")
            return result
        result = (self.workspace / project_id).resolve()
        if result.parent != self.workspace or not (result / "text").is_dir():
            raise ValueError("Project is outside the workspace or does not exist.")
        return result

    def resolve_location(self, location) -> Path:
        """Validate the folder a new project is created in; blank means the workspace."""
        if location is None or not str(location).strip():
            return self.workspace
        raw = Path(str(location).strip()).expanduser()
        if not raw.is_absolute():
            raise ValueError("Choose a full folder path for the project location.")
        if not raw.is_dir():
            raise ValueError("The project location does not exist. Choose an existing folder.")
        if not os.access(raw, os.W_OK | os.X_OK):
            raise ValueError("The project location is not writable. Choose another folder.")
        return raw.resolve()

    def set_credentials(self, keys):
        if not isinstance(keys, dict) or any(k not in PROVIDER_KEYS or not isinstance(v, str) or not v.strip() or len(v) > 2400 or any(ord(c) < 32 or ord(c) == 127 for c in v) for k, v in keys.items()):
            raise ValueError("Invalid provider credentials.")
        with self.lock:
            self.credentials = dict(keys)

    def state(self, p: Path):
        return migrate_state(read_json(p / ".studio/state.json", {"stale": {}, "reviewed": {}, "settings": {}, "source": "", "events": [], "layout": STATE_LAYOUT}))

    def persist(self, p: Path, state):
        atomic_json(p / ".studio/state.json", state)

    def invalidate(self, state, stage, indices=None, project=None):
        if stage == "1":
            # The content summary is written from the manuscript. Recordings and visuals follow it only
            # through new segments, which then outdate just the segments whose text changed.
            state["summary_stale"] = True
            self.mark_stale(state, "2", "Step 1 changed")
            state["reviewed"].pop(stage, None)
            return
        for s in STAGES[STAGES.index(stage) + 1:]:
            # Covers consume text metadata, not voice or visuals. Recordings and visuals are independent:
            # both are made from the segment text, and assembly fits each visual to its recording.
            if (s == "6" and stage in {"3", "4", "5"}) or (stage == "3" and s == "4"):
                continue
            scoped = indices
            previous = state["stale"].get(s)
            scope = sorted(set(scoped) | set(previous["segments"])) if scoped and previous and previous["segments"] else scoped or []
            if previous and not previous["segments"]:
                scope = []
            state["stale"][s] = {"reason": f"Step {stage} changed", "segments": scope}
            state["reviewed"].pop(s, None)
        state["reviewed"].pop(stage, None)

    @staticmethod
    def mark_stale(state, stage, reason, indices=None):
        """Flag one step outdated: for these segments, or the whole step when none are named."""
        previous = state["stale"].get(stage)
        whole = not indices or (previous is not None and not previous["segments"])
        scope = [] if whole else sorted(set(indices) | set(previous["segments"] if previous else []))
        state["stale"][stage] = {"reason": reason, "segments": scope}
        state["reviewed"].pop(stage, None)

    def resegmented(self, state, before, after):
        """Later steps are outdated only where what they show or say changed: a segment whose text is
        unchanged keeps its recording and visual, and covers and the 3:4 panels follow only the text they print."""
        before, after = before or {}, after or {}
        old = {s.get("index"): s for s in before.get("segments", [])}
        new = after.get("segments", [])
        spoken = {s["index"] for s in new if (old.get(s["index"]) or {}).get("content") != s.get("content")}
        shown = spoken | {s["index"] for s in new if s["index"] in old and old[s["index"]].get("visualizer", "image") != s.get("visualizer", "image")}
        opening = {**self.defaults().get("global", {}), **state.get("settings", {}).get("global", {})}.get("opening_quote", True)
        quote = opening and first_text(before.get("golden_quotes")) != first_text(after.get("golden_quotes"))
        if quote:
            # The opening (0) speaks and shows the chosen golden quote.
            self.mark_stale(state, "3", "Opening quote changed", [0])
            self.mark_stale(state, "4", "Opening quote changed", [0])
        if spoken:
            self.mark_stale(state, "3", "Segment text changed", sorted(spoken))
        if shown:
            self.mark_stale(state, "4", "Segment text changed", sorted(shown))
        if spoken or shown or quote or len(new) != len(old):
            self.mark_stale(state, "5", "Segments changed")
        if cover_copy(before) != cover_copy(after):
            self.mark_stale(state, "6", "Cover text changed")
        panel = {**self.defaults().get("three_by_four", {}), **state.get("settings", {}).get("three_by_four", {})}
        if panel_copy(before, panel) != panel_copy(after, panel):
            state["three_by_four_stale"] = True
        state["reviewed"].pop("2", None)

    def direction_changed(self, state):
        """Every visual follows the shared direction, so a new one supersedes them."""
        self.invalidate(state, "4")
        state["stale"]["4"] = {"reason": "Content summary changed", "segments": []}

    @staticmethod
    def needs_summary(p: Path, state) -> bool:
        """Visuals create their shared direction first when it is missing. An outdated one stays
        until it is regenerated on purpose, since a new direction outdates every visual."""
        return not str(read_json(p / "text/mini_summary.json", {}).get("summary", "")).strip()

    def snapshot(self, p: Path, paths, label: str):
        revision = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        copied = []
        for path in paths:
            if not path.is_file() or not path.resolve().is_relative_to(p.resolve()) or ".studio" in path.relative_to(p).parts:
                continue
            relative = path.relative_to(p)
            dest = p / ".studio/history" / revision / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            copied.append(str(relative))
        state = self.state(p)
        state["events"].insert(0, {"id": revision, "label": label, "time": datetime.now().isoformat(), "files": copied})
        self.persist(p, state)
        return revision

    @staticmethod
    def scoped_media(paths, project, indices, opening=False):
        if indices is None:
            return paths
        selected = set(indices)
        result = []
        for path in paths:
            relative = str(path.relative_to(project)).replace("\\", "/")
            match = re.search(r"(?:voice_|segment_)(\d+)(?:\.|/|$)", relative)
            if match and int(match.group(1)) in selected or opening and Path(relative).name.startswith("opening"):
                result.append(path)
        return result

    def defaults(self):
        import yaml
        data = yaml.safe_load((self.engine / "config.yaml" if (self.engine / "config.yaml").exists() else self.engine / "config.example.yaml").read_text())
        sections = {DESKTOP_SECTIONS.get(k, k): v for k, v in data.items() if isinstance(v, dict)}
        migrate_segmentation(sections)
        if "step5" in sections:
            sections["step5"].setdefault("assembly_method", "standard")
            sections["step5"].setdefault("agent_skill", editing.STYLES[0])
        if "step6" in sections:
            sections["step6"].setdefault("cover_method", "api")
        return {k: v for k, v in sections.items() if k in SETTINGS_SECTIONS}

    def google_adc_status(self):
        """The last Google Cloud login check, kept across restarts."""
        return read_json(self.data / GOOGLE_CHECK_FILE, {})

    def check_google_adc(self, body):
        models = sorted({str(m).strip() for m in body.get("models") or [] if str(m).strip()}) or ["gemini-3.1-flash-image"]
        request = json.dumps({"engine": str(self.engine), "models": models}, ensure_ascii=False)
        try:
            # Its own process: Google's libraries stay out of the service, and a stalled network call is bounded.
            completed = subprocess.run([sys.executable, str(Path(__file__).with_name("google_check.py")), request],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            lines = completed.stdout.strip().splitlines()
            result = json.loads(lines[-1]) if lines else {"ok": False, "message": "The check stopped unexpectedly: " + completed.stderr.strip()[-300:]}
        except subprocess.TimeoutExpired:
            result = {"ok": False, "message": "Google did not answer within 90 seconds. Check the network and try again."}
        result["checked"] = datetime.now().astimezone().isoformat(timespec="seconds")
        atomic_json(self.data / GOOGLE_CHECK_FILE, result)
        return result

    def components(self):
        """The programs production relies on, and whether each ships with the app."""
        bundle = self.engine.parent
        found = {
            "Python": sys.executable,
            "Claude Code": os.environ.get("STUDIO_CLAUDE") or shutil.which("claude"),
            "Node.js": shutil.which("node"),
            "Browser libraries": os.environ.get("AIGC_MOTION_MODULES"),
            "Chrome Headless Shell": os.environ.get("AIGC_BROWSER_PATH"),
            "FFmpeg": shutil.which("ffmpeg"),
            "FFprobe": shutil.which("ffprobe"),
        }
        result = []
        for name, path in found.items():
            available = bool(path) and Path(path).is_file()
            result.append({"name": name, "path": path if available else None,
                           "bundled": available and Path(path).resolve().is_relative_to(bundle)})
        return result

    def bootstrap(self):
        skills = [p.parent.name for p in sorted((self.library_root / "skills/step1").glob("*/SKILL.md"))]
        return {"default_location": str(self.workspace), "library_folder": str(self.library_root), "defaults": self.defaults(), "skills": skills, "voices": self.voices(),
                "library": self.library.catalog(), **self.music(), "components": self.components()}

    def voices(self):
        saved = read_json(self.data / VOICES_FILE, {"voices": [DEFAULT_VOICE.copy()]})
        if not isinstance(saved, dict) or not isinstance(saved.get("voices"), list) or any(
            not isinstance(voice, dict) or not isinstance(voice.get("id"), str) or not isinstance(voice.get("name"), str)
            for voice in saved.get("voices", [])
        ):
            raise ValueError("The saved voice library is invalid.")
        return saved["voices"]

    def save_voice(self, body):
        if not isinstance(body, dict):
            raise ValueError("Enter a voice ID and a name.")
        voice_id, name = body.get("id"), body.get("name")
        if not isinstance(voice_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", voice_id.strip()):
            raise ValueError("Enter a valid voice ID (letters, numbers, underscores or hyphens).")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("Enter a voice name between 1 and 80 characters.")
        voice = {"id": voice_id.strip(), "name": name.strip()}
        with self.lock:
            voices = self.voices()
            index = next((i for i, item in enumerate(voices) if item["id"] == voice["id"]), None)
            if index is None:
                if len(voices) >= 500:
                    raise ValueError("The voice library can contain up to 500 voices.")
                voices.append(voice)
            else:
                voices[index] = voice
            atomic_json(self.data / VOICES_FILE, {"voices": voices})
        return {"voices": voices}

    def music(self):
        """Background music: the bundled tracks, then the ones the user added."""
        listing = lambda folder: sorted(f.name for f in folder.glob("*") if f.is_file() and f.suffix.lower() in MUSIC) if folder.is_dir() else []
        bundled, added = listing(self.engine / "music"), listing(self.music_root)
        return {"music": bundled + [name for name in added if name not in bundled], "user_music": added}

    def music_file(self, name):
        name = str(name or "")
        if not name or Path(name).name != name:
            raise ValueError("Choose a music track from the list.")
        for folder in (self.engine / "music", self.music_root):
            if (folder / name).is_file() and (folder / name).suffix.lower() in MUSIC:
                return folder / name
        raise ValueError("This music track is no longer available.")

    def import_music(self, body):
        """Copy a music file into the app's music folder, where every project can use it."""
        source = Path(str(body.get("path") or "")).expanduser()
        if not source.is_absolute() or not source.is_file():
            raise ValueError("Choose a music file.")
        if source.suffix.lower() not in MUSIC:
            raise ValueError("Choose an MP3, WAV, M4A, AAC or FLAC file.")
        self.music_root.mkdir(parents=True, exist_ok=True)
        taken = set(self.music()["music"])
        name, number = source.name, 2
        while name in taken:
            name = f"{source.stem} ({number}){source.suffix}"
            number += 1
        shutil.copy2(source, self.music_root / name)
        return {"name": name, **self.music()}

    def panel_preview(self, project_id):
        """Draw the 3:4 copy's panels around a frame of the video (or of a segment), with the saved settings."""
        p = self.project(project_id)
        request = {"engine": str(self.engine), "project": str(p), "step": "5", "mode": "preview",
                   "settings": self.state(p).get("settings", {}), "output": PANEL_PREVIEW}
        environment = {**os.environ, LIBRARY_ENV: str(self.library_root)}
        with self.preview_lock:
            result = subprocess.run([sys.executable, "-u", str(Path(__file__).with_name("runner.py")), json.dumps(request, ensure_ascii=False)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=environment)
        outcome = next((json.loads(line[len("STUDIO_RESULT:"):]) for line in reversed(result.stdout.splitlines()) if line.startswith("STUDIO_RESULT:")), None)
        if not outcome or not outcome.get("success"):
            raise ValueError((outcome or {}).get("message") or "The 3:4 preview could not be drawn.")
        return {"path": PANEL_PREVIEW, "source": outcome.get("source")}

    def open_existing(self, body):
        """Add a project folder from elsewhere (another computer, a backup, the CLI) to the list."""
        location = Path(str(body.get("path") or "")).expanduser()
        if not location.is_absolute() or not (location / "text").is_dir():
            raise ValueError("Choose a production folder. It contains a text folder.")
        location = location.resolve()
        self.show(location)  # Opening a project that was removed from the list brings it back.
        return {"id": location.name if location.parent == self.workspace else self.link(location), "path": str(location)}

    def remove_projects(self, body):
        """Take projects off the list; optionally delete their folders from disk.

        Removing keeps every file. Deleting is permanent and only applies to folders
        the list showed (a workspace folder or a project the user created or opened).
        """
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError("Choose the projects to remove.")
        delete_files = body.get("delete_files") is True
        with self.lock:
            targets = []
            for project_id in dict.fromkeys(ids):
                p = self.project(project_id)
                if self.busy(project_id):
                    raise Conflict("Stop the running job or agent before removing a project.")
                if delete_files and (p == self.workspace or any(keep.is_relative_to(p) for keep in (self.workspace, self.engine, self.data)) or p.parent == p):
                    raise ValueError("This folder is not safe to delete.")
                targets.append((project_id, p))
            links, hidden, removed, failed = self.links(), self.hidden(), [], []
            for project_id, p in targets:
                if delete_files:
                    try:
                        shutil.rmtree(p)
                    except OSError:
                        failed.append(p.name)
                        continue
                    hidden.discard(str(p))
                elif project_id not in links:
                    hidden.add(str(p))  # Listed because it sits in the workspace: hide, keep the files.
                links.pop(project_id, None)
                removed.append(project_id)
            atomic_json(self.links_path, links)
            atomic_json(self.hidden_path, sorted(hidden))
        if failed:
            raise ValueError("Some project folders could not be deleted. Close anything using them and try again.")
        return {"removed": removed, "projects": self.projects()}

    def adopt_legacy(self, config: Path):
        """List the projects of a build that kept them in the CLI engine's output folder."""
        try:
            engine = Path(json.loads(config.read_text(encoding="utf-8"))["engine"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        output = engine / "output"
        if output.is_dir():
            for p in sorted(output.iterdir()):
                if (p / "text").is_dir():
                    self.link(p)
            links = read_json(output / ".studio-projects.json", {})
            for location in links.values() if isinstance(links, dict) else []:
                if isinstance(location, str) and (Path(location) / "text").is_dir():
                    self.link(Path(location))
        config.replace(config.with_name(config.name + ".migrated"))

    def projects(self):
        result = []
        for p in self.roots():
            project_id = self.project_id(p)
            outside = p.resolve().parent != self.workspace
            try:
                raw = read_json(p / "text/raw.json", {})
                script = read_json(p / "text/script.json", {})
                name = self.state(p).get("name") or p.name
                result.append({"id": project_id, "name": (raw.get("video_titles") or [name])[0], "source_name": raw.get("source_name") or name, "segments": len(script.get("segments", [])), "modified": p.stat().st_mtime, "path": str(p), "linked": outside})
            except OSError:
                result.append({"id": project_id, "name": p.name, "source_name": p.name, "problem": "Cannot open this folder", "segments": 0, "modified": 0, "path": str(p), "linked": outside})
            except (ValueError, TypeError):
                result.append({"id": project_id, "name": p.name, "source_name": p.name, "problem": "Needs repair", "segments": 0, "modified": p.stat().st_mtime, "path": str(p), "linked": outside})
        return sorted(result, key=lambda p: p["id"])

    def asset(self, p, folder, stem, extensions):
        for ext in extensions:
            candidate = p / folder / (stem + ext)
            if candidate.is_file():
                return candidate
        return None

    def sync_takes(self, p, state, script=None):
        """Bring every part's takes up to date and keep each segment's visualizer in line with its take.

        Returns the parts' selections (0 is the opening) and whether script.json changed."""
        script = script if script is not None else read_json(p / "text/script.json", {})
        selections = state.setdefault("takes", {})
        changed = False
        parts = [(0, "hf" if takes.placed(p, 0, takes.VIDEOS) and not takes.placed(p, 0, takes.STILLS) else "image")]
        parts += [(int(s["index"]), s.get("visualizer", "image")) for s in script.get("segments", [])]
        takes.keep_background(p)
        if not state.get("backgrounds_recovered"):
            takes.recover_backgrounds(p)
            state["backgrounds_recovered"] = True
        for index, visualizer in parts:
            selection = takes.sync(p, index, selections.get(str(index)), visualizer)
            if selection:
                selections[str(index)] = selection
            else:
                selections.pop(str(index), None)
            kind = takes.kind_of(selection)
            segment = next((s for s in script.get("segments", []) if s.get("index") == index), None) if index else None
            if segment is not None and kind and segment.get("visualizer", "image") != kind:
                segment["visualizer"] = kind
                changed = True
        # Projects from before takes kept these; their contents now live in images/takes/.
        for legacy in [p / "images/stills", p / "images/motion", p / "images/hyperframes"]:
            if legacy.is_dir() and not any(f.is_file() and f.name not in {".DS_Store", "Thumbs.db"} for f in legacy.rglob("*")):
                shutil.rmtree(legacy, ignore_errors=True)
        if changed:
            atomic_json(p / "text/script.json", script)
            state.setdefault("hashes", {})["script"] = fingerprint(p / "text/script.json")
        return selections, changed

    def select(self, p, state, index, selection):
        """Use one take for a part; None leaves the part without a visual until one is made."""
        selections = state.setdefault("takes", {})
        if selection:
            selections[str(index)] = selection
        else:
            selections.pop(str(index), None)
        takes.place(p, index, selection)

    def use_visual_files(self, p, state, idx, visualizer) -> bool:
        """A segment that changes type uses its newest take of that type; True when it has a visual."""
        take = takes.newest(p, idx, "motion" if visualizer == "hf" else "still")
        self.select(p, state, idx, take)
        return takes.ready(p, idx, take)

    def motion_folder(self, p, state, index, take=None):
        """The motion clip a render or a Claude Code session works on: the given take, else the selected one."""
        take = take or state.get("takes", {}).get(str(index))
        found = takes.find(p, index, take) if take else None
        return found["dir"] if found and found["kind"] == "motion" else None

    def detail(self, project_id):
        p = self.project(project_id)
        with self.lock:
            state = self.state(p)
            raw = read_json(p / "text/raw.json", {})
            script = read_json(p / "text/script.json", {})
            summary = read_json(p / "text/mini_summary.json", {})
            hashes = {k: fingerprint(p / "text" / v) for k, v in TEXT_FILES.items()}
            old = state.get("hashes", {})
            for key, stage in [("raw", "1"), ("script", "2")]:
                if old.get(key) is not None and old[key] != hashes[key]:
                    self.invalidate(state, stage)
            if old.get("summary") is not None and old["summary"] != hashes["summary"]:
                self.direction_changed(state)
            state["hashes"] = hashes
            self.persist(p, state)
            selections, _ = self.sync_takes(p, state, script)
            hashes["script"] = state["hashes"]["script"] = fingerprint(p / "text/script.json")
            segments = []
            relative = lambda f: str(f.relative_to(p)) if f else None
            parts = {}
            for index in [0] + [int(s["index"]) for s in script.get("segments", [])]:
                listed = [takes.describe(p, t) for t in takes.listing(p, index)]
                if index == 0 and (background := takes.current_background(p)):
                    # The background the next opening render uses.
                    for take in listed:
                        take["current"] = take["id"] == background
                chosen = next((t for t in listed if t["id"] == selections.get(str(index))), None)
                placed = takes.placed(p, index, takes.STILLS + takes.VIDEOS)
                parts[index] = {"takes": listed, "take": chosen["id"] if chosen else None, "visual": relative(placed[0]) if placed else None,
                                # The sequence shows the selected take: a still, or a motion clip's keyframe.
                                "poster": chosen["poster"] if chosen else None, "motion": chosen.get("motion") if chosen else None}
            for s in script.get("segments", []):
                idx = int(s["index"])
                audio = self.asset(p, "voice", f"voice_{idx}", [".wav", ".mp3"])
                part = parts[idx]
                segments.append({**s, **part, "audio": relative(audio), "duration": duration(audio),
                                 "composition": part["motion"]["composition"] if part["motion"] else None})
            # Later steps use what is in the engine's place; takes that are not selected do not outdate them.
            artifact_paths = [f for folder in ["images", "voice"] for f in (p / folder).rglob("*") if f.is_file() and f.suffix.lower() in MEDIA
                              and not f.relative_to(p).as_posix().startswith(takes.ROOT + "/")]
            artifact_paths += [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in MEDIA]
            assets = {str(f.relative_to(p)): f"{f.stat().st_mtime_ns}:{f.stat().st_size}" for f in artifact_paths}
            previous = state.get("assets")
            if previous is not None:
                changes = {k for k in previous.keys() | assets.keys() if previous.get(k) != assets.get(k)}
                for prefix, stage in [("voice/", "3"), ("images/", "4")]:
                    # Only a segment's own media (or the opening's, index 0) makes later steps outdated;
                    # files such as voice/字幕.srt belong to no segment.
                    indices = sorted({i for k in changes if k.startswith(prefix) and (i := segment_of(k)) is not None})
                    if indices:
                        self.invalidate(state, stage, indices, project=p)
            state["assets"] = assets
            opening_audio = self.asset(p, "voice", "opening", [".wav", ".mp3"])
            covers = [str(f.relative_to(p)) for f in sorted(p.glob("cover_*")) if f.suffix.lower() in MEDIA]
            ready = {"1": bool(raw.get("content")), "2": bool(segments), "3": bool(segments) and all(s["audio"] for s in segments), "4": bool(segments) and all(s["visual"] for s in segments), "5": (p / "final_video.mp4").is_file(), "6": bool(covers)}
            opening_required = state.get("settings", {}).get("global", self.defaults().get("global", {})).get("opening_quote", True)
            if opening_required:
                ready["3"] = ready["3"] and bool(opening_audio)
                ready["4"] = ready["4"] and bool(parts[0]["visual"])
            self.persist(p, state)
            # New takes, keyframes and compositions refresh the app without outdating later steps.
            settings = self.defaults()
            for section, fields in state.get("settings", {}).items():
                settings.setdefault(section, {}).update(fields)
            cover_design = cover_designs.detail(p, state.get("cover_design"), settings, cover_copy(raw if raw.get("content") else script))
            assembly_edit = None
            if state.get("assembly_edit"):
                try:
                    edit_source = editing.source(self, p, settings)
                except (ValueError, OSError, KeyError):
                    edit_source = {}
                assembly_edit = editing.detail(p, state["assembly_edit"], edit_source)
                if assembly_edit and assembly_edit.get('published'):
                    state['stale'].pop('5',None)
                    self.persist(p,state)
            revision = hashlib.sha256(json.dumps([hashes, assets, parts, state["stale"], state["reviewed"], state.get("settings"), state.get("source"), state.get("summary_stale"), state.get("three_by_four_stale"), cover_design, assembly_edit], sort_keys=True).encode()).hexdigest()
            return {"id": project_id, "revision": revision, "path": str(p), "raw": raw, "script": script, "summary": summary, "hashes": hashes, "segments": segments, "state": state, "ready": ready, "covers": covers, "cover_design": cover_design, "assembly_edit": assembly_edit, "opening": {"visual": parts[0]["visual"], "takes": parts[0]["takes"], "take": parts[0]["take"], "poster": parts[0]["poster"], "audio": str(opening_audio.relative_to(p)) if opening_audio else None, "duration": duration(opening_audio)}, "exports": [f for f in ["final_video.mp4", "final_video_3x4.mp4"] if (p / f).exists()]}

    @staticmethod
    def beside(a, b):
        """Work on these two steps does not wait for each other: covers, and recordings, visuals or assembly."""
        return (a == "6" and b in BESIDE_COVERS) or (b == "6" and a in BESIDE_COVERS)

    def running(self, project_id, step=None):
        """A job is running that work on this step (or, without one, on anything in the project) waits for."""
        return any(j["project"] == project_id and j["status"] in {"running", "cancelling"} and not (step and self.beside(step, j["step"]))
                   for j in self.jobs.values())

    def busy(self, project_id, step=None):
        return self.running(project_id, step) or bool(self.state(self.project(project_id)).get("agent_active"))

    def save(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            if self.busy(project_id):
                raise Conflict("Stop the running job or agent before editing its outputs.")
            kind = body["kind"]
            path = p / "text" / TEXT_FILES[kind]
            if body.get("expected_hash") != fingerprint(path):
                raise Conflict("This file changed outside the editor. Refresh before saving; your draft is kept.")
            value = dict(body["value"])
            previous = read_json(path, {})
            visual_only = False
            if kind == "raw":
                if not isinstance(value.get("content"), str) or not value["content"].strip():
                    raise ValueError("The manuscript cannot be empty.")
                selections = body.get("selections", {})
                if not isinstance(selections, dict) or any(k not in METADATA_FIELDS for k in selections):
                    raise ValueError("Invalid generation choices. Refresh and select your candidates again.")
                for k in METADATA_FIELDS:
                    if not isinstance(value.get(k), list) or any(not isinstance(x, str) for x in value[k]):
                        raise ValueError(f"{k} must contain text candidates.")
                    choice = selections.get(k, 0 if value[k] else None)
                    if not value[k] and choice is None:
                        continue
                    if type(choice) is not int or not 0 <= choice < len(value[k]):
                        raise ValueError("Invalid generation choices. Refresh and select your candidates again.")
                    if not value[k][choice].strip():
                        raise ValueError("A selected candidate cannot be empty. Enter text or remove its box.")
                    # Every existing stage and the DOCX bridge use the first candidate.
                    # Keep alternatives, but persist the GUI choice as the primary input.
                    value[k] = [value[k][choice], *value[k][:choice], *value[k][choice + 1:]]
                value["total_length"] = len(value["content"])
            elif kind == "script":
                if not value.get("segments"):
                    raise ValueError("The script needs at least one segment.")
                for i, s in enumerate(value["segments"], 1):
                    if s.get("index") != i or not str(s.get("content", "")).strip() or s.get("visualizer", "image") not in {"image", "hf"}:
                        raise ValueError("Segments must have consecutive indices, text, and image/hf visualizer.")
                    s["length"] = len(s["content"])
                value["actual_segments"] = len(value["segments"])
                value["total_length"] = sum(s["length"] for s in value["segments"])
                before = previous.get("segments", [])
                visual_only = len(before) == len(value["segments"]) and all(a.get("index") == b.get("index") and a.get("content") == b.get("content") for a, b in zip(before, value["segments"])) and any(a.get("visualizer", "image") != b.get("visualizer", "image") for a, b in zip(before, value["segments"]))
            elif not str(value.get("summary", "")).strip():
                raise ValueError("The content summary cannot be empty.")
            if editable(value) == editable(previous):
                # Nothing changed (for example a switch flipped and flipped back): later steps stay current.
                return self.detail(project_id)
            script_path = p / "text/script.json"
            script = read_json(script_path) if kind == "raw" else None
            # Titles, cover text and quotes reach the segments right away; only new manuscript text needs segmenting again.
            synced = {**script, **{k: value[k] for k in METADATA_FIELDS}} if isinstance(script, dict) else None
            if synced == script:
                synced = None
            self.snapshot(p, [path, script_path] if synced else [path], f"Before editing {kind}")
            atomic_json(path, value)
            if synced:
                atomic_json(script_path, synced)
            state = self.state(p)
            if kind == "summary":
                state.pop("summary_stale", None)
                self.direction_changed(state)
            else:
                stage = {"raw": "1", "script": "2"}[kind]
                switched = {}
                if kind == "script":
                    kinds = {s.get("index"): s.get("visualizer", "image") for s in before}
                    switched = {s["index"]: self.use_visual_files(p, state, s["index"], s.get("visualizer", "image"))
                                for s in value["segments"] if s["index"] in kinds and kinds[s["index"]] != s.get("visualizer", "image")}
                if visual_only:
                    self.invalidate(state, "4", body.get("segments"))
                    # A segment that already has the other kind of visual (a set-aside still, a current render) is ready.
                    self.mark_visual_switch(state, switched)
                elif kind == "script":
                    self.resegmented(state, previous, value)
                    state["stale"].pop(stage, None)
                else:
                    if synced:
                        self.resegmented(state, script, synced)
                        state.setdefault("hashes", {})["script"] = fingerprint(script_path)
                    if value.get("content") != previous.get("content"):
                        self.invalidate(state, stage)
                    state["stale"].pop(stage, None)
            state.setdefault("hashes", {})[kind] = fingerprint(path)
            self.persist(p, state)
        return self.detail(project_id)

    @staticmethod
    def mark_visual_switch(state, switched):
        """Visuals still to make are outdated; ready ones drop reasons that only concerned their old type."""
        stale = state["stale"].get("4")
        pending = sorted(i for i, ready in switched.items() if not ready)
        if pending:
            if not stale:
                state["stale"]["4"] = {"reason": "Visual type changed", "segments": pending}
            elif stale["segments"]:
                stale["segments"] = sorted(set(stale["segments"]) | set(pending))
        elif stale and stale["segments"] and stale["reason"] in TYPE_REASONS:
            stale["segments"] = [i for i in stale["segments"] if i not in switched]
            if not stale["segments"]:
                state["stale"].pop("4")

    def select_take(self, project_id, body):
        """Use one of a part's stills or motion clips; the others are kept."""
        p = self.project(project_id)
        index, take = body.get("index"), body.get("take")
        if type(index) is not int or not isinstance(take, str):
            raise ValueError("Choose a segment and one of its images or motion clips.")
        with self.lock:
            if self.running(project_id):
                raise Conflict("Wait for the running job to finish before changing a segment's visual.")
            state = self.state(p)
            active = state.get("agent_active")
            if active and (active["step"] != "4" or index in (active.get("indices") or [])):
                raise Conflict("Finish the Claude Code session before changing this segment's visual.")
            script = read_json(p / "text/script.json", {})
            segment = next((s for s in script.get("segments", []) if s.get("index") == index), None)
            if index != 0 and segment is None:
                raise ValueError(f"Segment {index} does not exist.")
            self.sync_takes(p, state, script)
            if not takes.find(p, index, take):
                raise ValueError("This image or motion clip is no longer in the project. Refresh and choose again.")
            if state["takes"].get(str(index)) == take:
                return self.detail(project_id)
            before = takes.kind_of(state["takes"].get(str(index)))
            self.select(p, state, index, take)
            self.sync_takes(p, state, script)
            ready = takes.ready(p, index, take)
            self.invalidate(state, "4", [index])
            if before != takes.kind_of(take):
                self.mark_visual_switch(state, {index: ready})
            elif not ready:
                self.mark_visual_switch(state, {index: False})
            self.persist(p, state)
        return self.detail(project_id)

    def delete_take(self, project_id, body):
        """Remove one still, background, or motion clip a part no longer needs; Revision history keeps a copy."""
        p = self.project(project_id)
        index, take = body.get("index"), body.get("take")
        if type(index) is not int or not isinstance(take, str):
            raise ValueError("Choose a segment and one of its images or motion clips.")
        with self.lock:
            if self.running(project_id):
                raise Conflict("Wait for the running job to finish before deleting a version.")
            state = self.state(p)
            active = state.get("agent_active")
            if active and (active["step"] != "4" or index in (active.get("indices") or [])):
                raise Conflict("Finish the Claude Code session before deleting this segment's versions.")
            self.sync_takes(p, state)
            self.persist(p, state)
            found = takes.find(p, index, take)
            if not found:
                raise ValueError("This image or motion clip is no longer in the project. Refresh and choose again.")
            if state["takes"].get(str(index)) == take:
                raise Conflict("This version is in use. Choose another one before deleting it.")
            if found["kind"] == "background" and takes.current_background(p) == take:
                raise Conflict("The next opening renders with this background. Render the opening with another background before deleting it.")
            files = [f for f in found["dir"].rglob("*") if f.is_file()] if found["dir"] else [found["file"]]
            self.snapshot(p, files, "Before deleting a version of the opening" if index == 0 else f"Before deleting a version of segment {index}")
            if found["dir"]:
                shutil.rmtree(found["dir"])
            else:
                found["file"].unlink()
        return self.detail(project_id)

    def delete_cover(self, project_id, body):
        """Remove any cover, choosing a remaining candidate; Revision history keeps a copy."""
        p = self.project(project_id)
        with self.lock:
            if self.running(project_id, "6"):
                raise Conflict("Wait for the running job to finish before deleting a cover.")
            covers = self.detail(project_id)["covers"]
            path = body.get("path")
            if path not in covers:
                raise ValueError("This cover is no longer in the project. Refresh and choose again.")
            state = self.state(p)
            if state.get("agent_active"):
                raise Conflict("Finish the Claude Code session first.")
            # Without a saved choice, the first candidate is the cover in use.
            chosen = state.get("selected_cover") if state.get("selected_cover") in covers else covers[0]
            self.snapshot(p, [p / path], "Before deleting a cover")
            (p / path).unlink()
            remaining = [cover for cover in covers if cover != path]
            state = self.state(p)
            if state.get("selected_cover") not in remaining:
                if remaining:
                    state["selected_cover"] = remaining[0]
                else:
                    state.pop("selected_cover", None)
            if path == chosen:
                state["reviewed"].pop("6", None)
            self.persist(p, state)
        return self.detail(project_id)

    def writing_options(self, state):
        settings = {**self.defaults().get("step1", {}), **state.get("settings", {}).get("step1", {})}
        source = state.get("source", "")
        return {"mode": settings.get("writing_mode", "source" if source else "direct"), "source": source,
                "agent_skill": str(settings.get("agent_skill", "sample_writer")),
                "llm_server": str(settings.get("llm_server", "")), "llm_model": str(settings.get("llm_model", ""))}

    def set_opening(self, project_id, body):
        p = self.project(project_id)
        enabled = body.get("enabled")
        if type(enabled) is not bool:
            raise ValueError("Invalid opening quote setting.")
        with self.lock:
            if self.busy(project_id):
                raise Conflict("Stop production before changing its settings.")
            state = self.state(p)
            previous = state.get("settings", {}).get("global", {}).get("opening_quote", self.defaults().get("global", {}).get("opening_quote", True))
            if "expected" in body and body["expected"] != previous:
                raise Conflict("Opening quote setting changed elsewhere. Refresh before changing it.")
            if previous != enabled:
                state.setdefault("settings", {}).setdefault("global", {})["opening_quote"] = enabled
                # Switching inclusion changes the assembly, not the narration,
                # context, covers, or existing segment recordings/visuals.
                state["stale"]["5"] = {"reason": "Opening quote usage changed", "segments": []}
                state["reviewed"].pop("5", None)
                for stage in ["3", "4"]:
                    stale = state["stale"].get(stage)
                    if not enabled and stale and stale["segments"] and 0 in stale["segments"]:
                        state.setdefault("disabled_opening_stale", {})[stage] = {"reason": stale["reason"], "segments": [0]}
                        stale["segments"] = [i for i in stale["segments"] if i != 0]
                        if not stale["segments"]:
                            state["stale"].pop(stage)
                    elif enabled:
                        suspended = state.get("disabled_opening_stale", {}).pop(stage, None)
                        if suspended:
                            state["reviewed"].pop(stage, None)
                            if stale is None:
                                state["stale"][stage] = suspended
                            elif stale["segments"]:
                                stale["segments"] = sorted(set(stale["segments"]) | {0})
                self.persist(p, state)
        return self.detail(project_id)

    def save_writing(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            if self.busy(project_id):
                raise Conflict("Stop production before changing its settings.")
            state = self.state(p)
            previous = self.writing_options(state)
            if "expected" in body and body["expected"] != previous:
                raise Conflict("Writing setup changed elsewhere. Reload the project before saving.")
            options = body.get("options", {})
            if set(options) != set(previous) or options.get("mode") not in {"source", "direct"} or any(not isinstance(v, str) for v in options.values()):
                raise ValueError("Invalid writing setup.")
            if options != previous:
                state.setdefault("settings", {}).setdefault("step1", {}).update({
                    "writing_mode": options["mode"], "agent_skill": options["agent_skill"],
                    "llm_server": options["llm_server"], "llm_model": options["llm_model"]})
                state["source"] = options["source"]
                # Setup applies to the next writing session. Preserve the current
                # manuscript and its review state until it is actually rewritten.
                self.persist(p, state)
        return self.detail(project_id)

    def create(self, body):
        settings = body.get("settings", {})
        source_value = body.get("source") or ""
        mode = settings.get("step1", {}).get("writing_mode", "source" if source_value else "direct")
        if mode not in {"source", "direct"}:
            raise ValueError("Invalid writing setup.")
        if mode == "source" and (not source_value or not Path(source_value).exists()):
            raise ValueError("Source document or folder does not exist.")
        source = str(Path(source_value).resolve()) if source_value else ""
        title = re.sub(r'[\\/:*?"<>|]', "_", str(body.get("name") or (Path(source).stem if source else "Untitled"))).strip().strip(".").strip()[:80] or "Untitled"
        parent = self.resolve_location(body.get("location"))
        # The folder is named after the project; a repeat name gets a numeric suffix.
        for attempt in range(1, 1000):
            p = parent / (title if attempt == 1 else f"{title}_{attempt}")
            try:
                p.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise ValueError("Could not create a project folder here. Choose another location.")
        for folder in ["text", "images", "voice"]:
            (p / folder).mkdir()
        self.show(p)
        state = self.state(p)
        settings = {**settings, "step1": {**settings.get("step1", {}), "writing_mode": mode}}
        state.update(name=title, source=source, settings=settings)
        self.persist(p, state)
        # A project outside the workspace is remembered so it stays in the project list.
        project_id = p.name if parent == self.workspace else self.link(p)
        return {"id": project_id, "path": str(p)}

    def restart(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            if self.busy(project_id):
                raise Conflict("Stop production before restarting.")
            state = self.state(p)
            # The new production is stored beside the one it restarts.
            return self.create({"source": body.get("source", state.get("source", "")),
                                "name": body.get("name") or state.get("name") or p.name.split("_")[0], "settings": state.get("settings", {}),
                                "location": str(p.parent)})

    def restore(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            if self.busy(project_id):
                raise Conflict("Stop production before restoring a revision.")
            event = next(e for e in self.state(p)["events"] if e["id"] == body["revision"])
            files = event["files"]
            self.snapshot(p, [p / f for f in files], "Before restoring revision")
            script_before = read_json(p / "text/script.json", {})
            for f in files:
                source = p / ".studio/history" / event["id"] / f
                if not source.resolve().is_relative_to((p / ".studio/history").resolve()):
                    raise ValueError("Invalid revision source.")
                target = (p / f).resolve()
                if not target.is_relative_to(p):
                    raise ValueError("Invalid revision path.")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            state = self.state(p)
            changed = "1" if "text/raw.json" in files else "2" if "text/script.json" in files else "3" if any(f.startswith("voice/") for f in files) else "4" if any(f.startswith("images/") for f in files) else "5"
            if changed == "5" and "text/mini_summary.json" in files:
                # Only the content summary came back; visuals made from another one are outdated.
                self.direction_changed(state)
            elif changed == "2":
                self.resegmented(state, script_before, read_json(p / "text/script.json", {}))
                state["stale"].pop(changed, None)
            else:
                self.invalidate(state, changed, project=p)
                state["stale"].pop(changed, None)
                if changed == "1" and "text/script.json" in files:
                    # The segments came back with the manuscript.
                    self.resegmented(state, script_before, read_json(p / "text/script.json", {}))
            self.persist(p, state)
        return self.detail(project_id)

    def prepare_agent(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            if self.busy(project_id):
                raise Conflict("This project already has an active job or agent.")
            if body["step"] == "4" and self.needs_summary(p, self.state(p)):
                raise Conflict("Generate the content summary before revising motion.")
            from context import prepare
            state = self.state(p)
            # Visuals: each segment's session works on one motion clip — the one asked for, the
            # selected clip, or a new clip; the segment's other stills and clips stay as they are.
            folders, created, previous = {}, [], {}
            if body["step"] == "4":
                script = read_json(p / "text/script.json", {})
                self.sync_takes(p, state, script)
                chosen = body.get("indices") or [s["index"] for s in script["segments"]]
                requested = body.get("take")
                if requested and len(chosen) != 1:
                    raise ValueError("Choose one segment to revise one of its motion clips.")
                for index in chosen:
                    work = self.motion_folder(p, state, index, requested) if requested != "new" else None
                    if requested and requested != "new" and work is None:
                        raise ValueError("This motion clip is no longer in the project. Refresh and choose again.")
                    if work is None:
                        work = takes.folder(p, index) / takes.add_motion(p, index)
                        created.append(index)
                    folders[index] = work
                self.persist(p, state)
            try:
                launch = prepare(self, p, {**body, "folders": folders})
            except Exception:
                for index in created:
                    shutil.rmtree(folders[index], ignore_errors=True)
                raise
            revised = [f for index, work in folders.items() if index not in created for f in work.rglob("*")]
            self.snapshot(p, list((p / "text").glob("*")) if body["step"] == "1" else revised + [p / "text/script.json"], f"Before Claude Code · step {body['step']}")
            state = self.state(p)
            if body["step"] == "5":
                state["assembly_edit"] = launch["assembly_edit"]
            if body["step"] == "6":
                state["cover_design"] = launch["cover_design"]
            for index, work in folders.items():
                previous[str(index)] = state.get("takes", {}).get(str(index))
                self.select(p, state, index, work.name)
            if folders:
                self.sync_takes(p, state)
            state["agent_active"] = {"step": body["step"], "indices": body.get("indices", []), "launch": launch["id"], "owner": os.getpid(), "raw_before": fingerprint(p / "text/raw.json"),
                                     "takes": {str(i): w.name for i, w in folders.items()}, "created": created, "previous": previous}
            self.persist(p, state)
            return launch

    def finish_agent(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            state = self.state(p)
            active = state.get("agent_active")
            warning = None
            before = state["stale"].get("4")
            if active:
                if active["step"] == "1":
                    target = p / "text/raw.json"
                    raw = read_json(target, {})
                    problem = raw_problem(raw)
                    if problem and (stray := stray_raw(p)):
                        # Skills written for the CLI mention output/<project>/text; keep the manuscript where the app reads it.
                        raw = read_json(stray, {})
                        atomic_json(target, raw)
                        problem = None
                    if problem:
                        warning = "Writing agent ended without a valid raw.json. Reopen Writing to complete the manuscript."
                    elif raw.get("total_length") != len(raw["content"]) and active.get("raw_before") != fingerprint(target):
                        # Models miscount characters; the app defines the length of what the agent wrote.
                        raw["total_length"] = len(raw["content"])
                        atomic_json(target, raw)
                state.pop("agent_active", None)
                if active["step"] == "5":
                    info = editing.detail(p, state.get("assembly_edit"))
                    warning = info.get("problem") if info else "Save an editing plan before rendering."
                    if info and info.get("published"):
                        state["stale"].pop("5",None)
                    elif info and info["valid"] and info["changed"]:
                        warning = "Ask the editing agent to render, review and publish the final video."
                if active["step"] == "6":
                    info = cover_designs.detail(p, state.get("cover_design"))
                    warning = None if info and info["complete"] else "Save every cover HTML design with Claude Code before rendering."
                if active["step"] == "5" and info and info["changed"] and not info.get("published"):
                    state["stale"]["5"] = {"reason": warning or "Render the saved edit to review the polished video.", "segments": []}
                    state["reviewed"].pop("5", None)
                elif active["step"] == "6":
                    state["stale"]["6"] = {"reason": warning or "Render the saved HTML designs to review the new covers.", "segments": []}
                    state["reviewed"].pop("6", None)
                elif active["step"] != "5" and (active["step"] != "1" or active.get("raw_before") != fingerprint(p / "text/raw.json")):
                    self.invalidate(state, active["step"], active["indices"])
                if warning:
                    state["stale"][active["step"]] = {"reason": warning, "segments": []}
                elif active["step"] not in {"5", "6"}:
                    state["stale"].pop(active["step"], None)
                if active["step"] == "4":
                    indices = active["indices"] or list(map(int, active.get("takes", {})))
                    work = {i: (takes.folder(p, i) / active["takes"][str(i)]) if str(i) in active.get("takes", {}) else self.motion_folder(p, state, i) for i in indices}
                    missing = [i for i in indices if not (work[i] and (work[i] / "index.html").is_file())]
                    for i in missing:
                        if i in active.get("created", []):
                            # A new clip Claude Code never composed is not kept; the segment goes back to its previous visual.
                            shutil.rmtree(work[i], ignore_errors=True)
                            self.select(p, state, i, active.get("previous", {}).get(str(i)))
                    self.sync_takes(p, state)
                    # The session's segments wait for a render (unless their video already matches the
                    # composition); other segments keep whatever was outdated before the session.
                    pending = [i for i in indices if i not in missing and not takes.motion(p, work[i])["video_current"]]
                    if before and not before["segments"]:
                        state["stale"]["4"] = before
                    elif before and set(before["segments"]) - set(indices):
                        state["stale"]["4"] = {**before, "segments": sorted(set(before["segments"]) | set(pending))}
                    elif pending:
                        state["stale"]["4"] = {"reason": RENDER_REASON, "segments": pending}
                    if missing:
                        warning = f"Claude Code ended without a composition for segment {', '.join(map(str, missing))}. Activate Claude Code for it again."
            self.persist(p, state)
        result = self.detail(project_id)
        result["warning"] = warning
        # The agent already renders; ending a session only refreshes project state.
        result["finished"] = {"step": active["step"], "indices": active["indices"] or []} if active else None
        return result

    def run_job(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            step = str(body["step"])
            if self.busy(project_id, step):
                raise Conflict("Finish the active project job or agent first.")
            if step not in STAGES or step == "1":
                raise ValueError("Step 1 runs interactively through Activate Claude Code.")
            if step == "4" and body.get("mode") not in {"segments", "static", "render", "opening", "opening_render", "summary"}:
                raise ValueError("Animated generation runs through the interactive Claude Code terminal.")
            if step == "5" and body.get("mode") not in {None, "three_by_four", "edit_render", "edit_preview"}:
                raise ValueError("Unknown assembly action.")
            if step == "5" and body.get("mode") in {"edit_render", "edit_preview"}:
                info = self.detail(project_id).get("assembly_edit")
                if not info or not info["valid"]:
                    raise ValueError(info.get("problem") if info else "Save an editing plan before rendering.")
                if not info["current"]:
                    raise ValueError("Assembly sources or settings changed. Revise the edit before rendering.")
            if step == "5" and body.get("mode") is None and self.state(p).get("settings", {}).get("step5", {}).get("assembly_method") == "agent":
                raise ValueError("Edit the video through the interactive Claude Code terminal.")
            if step == "6":
                if body.get("mode") not in {None, "cover_render"}:
                    raise ValueError("Unknown cover action.")
                if body.get("mode") == "cover_render":
                    info = self.detail(project_id).get("cover_design")
                    if not info or not info["complete"]:
                        raise ValueError("Save every cover HTML design with Claude Code before rendering.")
                    if not info["current"]:
                        raise ValueError("Cover copy or setup changed. Revise the HTML design before rendering.")
                elif self.state(p).get("settings", {}).get("step6", {}).get("cover_method", "api") == "agent":
                    raise ValueError("Design covers through the interactive Claude Code terminal.")
            # Earlier steps need their outputs; an outdated output still lets later steps run.
            if step == "5" and body.get("mode") == "three_by_four":
                # The 3:4 copy is drawn around the landscape master.
                detail = self.detail(project_id)
                if not detail["ready"]["5"]:
                    raise Conflict("Assemble the video first. The 3:4 copy is made from the current landscape video.")
            elif step == "5":
                detail = self.detail(project_id)
                if any(not detail["ready"][s] for s in ["2", "3", "4"]):
                    raise Conflict("Generate the segments, recordings and visuals before assembling this production.")
            if step == "4" and body.get("mode") == "opening_render":
                # Render a new opening clip over one of the kept backgrounds; nothing is generated.
                background = takes.find(p, 0, str(body.get("take", "")))
                if not background or background["kind"] != "background":
                    raise ValueError("This background is no longer in the project. Refresh and choose again.")
                body = {**body, "indices": [], "opening": True, "background": str(background["file"])}
            if step == "4" and body.get("mode") == "segments":
                detail = self.detail(project_id)
                if not detail["ready"]["2"]:
                    raise Conflict("Generate segments before generating visuals.")
                batch = body.get("batch")
                if batch not in {"all", "missing", "selected"}:
                    raise ValueError("Choose missing, all or selected segments.")
                candidates = detail["segments"]
                enabled = {**self.defaults().get("global", {}), **detail["state"].get("settings", {}).get("global", {})}.get("opening_quote", True)
                if batch == "selected":
                    indices = body.get("indices")
                    available = {s["index"] for s in candidates}
                    if not isinstance(indices, list) or any(type(i) is not int or i not in available for i in indices):
                        raise ValueError("Choose segment numbers that exist in this project.")
                    indices = sorted(set(indices))
                    opening = bool(enabled and body.get("opening"))
                else:
                    indices = [s["index"] for s in candidates if batch == "all" or not s["visual"]]
                    opening = bool(enabled and (batch == "all" or not detail["opening"]["visual"]))
                if not indices and not opening:
                    raise ValueError("No segments need generation. Choose all or enter segment numbers.")
                missing_audio = [s["index"] for s in candidates if s["index"] in indices and s.get("visualizer") == "hf" and not s["audio"]]
                if missing_audio:
                    raise ValueError("Generate recordings for motion segments first: " + ", ".join(map(str, missing_audio)))
                body = {**body, "indices": indices, "opening": opening}
            elif step == "4" and body.get("batch"):
                if body["batch"] not in {"all", "missing"}:
                    raise ValueError("Choose all images or missing images.")
                detail = self.detail(project_id)
                if not detail["ready"]["2"]:
                    raise Conflict("Generate segments before generating all images.")
                candidates = [s for s in detail["segments"] if s.get("visualizer", "image") == "image"]
                indices = [s["index"] for s in candidates if body["batch"] == "all" or not self.asset(p, "images", f"segment_{s['index']}", [".png", ".jpg", ".jpeg", ".webp"])]
                global_settings = {**self.defaults().get("global", {}), **detail["state"].get("settings", {}).get("global", {})}
                opening_enabled = global_settings.get("opening_quote", True)
                opening = bool(opening_enabled and (body["batch"] == "all" or not detail["opening"]["visual"]))
                if not indices and not opening:
                    raise ValueError("All static images are already available.")
                body = {**body, "indices": indices, "opening": opening, "mode": "static"}
            if step in {"4", "6"}:
                settings = self.defaults()
                for section, fields in self.state(p).get("settings", {}).items():
                    settings.setdefault(section, {}).update(fields)
                image_work = body.get("mode") not in {"render", "opening_render", "summary"}
                if body.get("mode") == "segments":
                    image_work = body["opening"] or any(s["index"] in body["indices"] and s.get("visualizer") != "hf" for s in detail["segments"])
                required = ([] if body.get("mode") == "cover_render" else [("cover_styles", "step6", "cover_image_style")]) if step == "6" else [
                    ("image_styles", "step4", "image_style_preset"),
                    ("image_templates", "step4", "image_prompt_template"),
                ] if image_work else []
                for group, section, key in required:
                    # A minimal test engine may not install image-generation resources.
                    if self.library.group(group)[2].exists():
                        self.library.item(group, settings.get(section, {}).get(key, ""))
            job_id = uuid.uuid4().hex
            direction_only = body.get("mode") == "summary"
            summary = step == "4" and body.get("mode") not in {"render", "opening_render"} and (direction_only or self.needs_summary(p, self.state(p)))
            dirs = {"2": ["text"], "3": ["voice"], "4": [] if direction_only else ["images"], "5": [], "6": []}[step]
            # Takes are only ever added (a revised clip is snapshotted by its session), so jobs leave them out.
            paths = [f for d in dirs for f in (p / d).rglob("*") if not f.relative_to(p).as_posix().startswith(takes.ROOT + "/")]
            if step in {"3", "4"}:
                paths = self.scoped_media(paths, p, body.get("indices"), body.get("opening", False) or body.get("mode") == "opening")
            if summary:
                paths += [p / "text/mini_summary.json", p / "text/raw.docx"]
            if step == "5":
                paths = [p / "final_video_3x4.mp4"] if body.get("mode") == "three_by_four" else list(p.glob("final_video*.mp4"))
                if body.get("mode") == "edit_render":
                    paths += [p / "final_video.srt", p / "final_video.qa.json"]
                paths = [f for f in paths if f.is_file()]
            if step == "6":
                paths = list(p.glob("cover_*"))
            if step == "5" and body.get("mode") == "edit_preview":
                paths = []
            self.snapshot(p, paths, "Before generating the content summary" if direction_only else "Before rebuilding the 3:4 copy" if body.get("mode") == "three_by_four" else f"Before generating step {step}")
            request = {**body, "summary": summary, "project": str(p), "engine": str(self.engine), "settings": self.state(p).get("settings", {}),
                       "music_dir": str(self.music_root)}
            motion_takes = {}
            if step == "4" and body.get("mode") == "segments":
                script = read_json(p / "text/script.json", {})
                for segment in script.get("segments", []):
                    if segment["index"] in body["indices"] and segment.get("visualizer") == "hf":
                        motion_takes[str(segment["index"])] = takes.add_motion(p, segment["index"])
                request.update(motion_folders={i: str(takes.folder(p, int(i)) / take) for i, take in motion_takes.items()},
                               studio_data=str(self.data), workspace=str(self.workspace),
                               claude=read_json(self.data / "claude-code.json", {"mode": "api_key"}))
            if step == "5" and body.get("mode") in {"edit_render", "edit_preview"}:
                request["assembly_edit"] = self.state(p)["assembly_edit"]
            if step == "6" and body.get("mode") == "cover_render":
                request["cover_design"] = self.state(p)["cover_design"]
            if step == "4" and body.get("mode") == "render":
                # Each segment renders its selected motion clip, or the one clip asked for.
                state = self.state(p)
                self.sync_takes(p, state)
                self.persist(p, state)
                script = read_json(p / "text/script.json", {})
                chosen = body.get("indices") or [s["index"] for s in script.get("segments", []) if s.get("visualizer") == "hf"]
                one = body.get("take") if len(chosen) == 1 else None
                request["folders"] = {str(i): str(w) for i in chosen if (w := self.motion_folder(p, state, i, one))}
            job = {"id": job_id, "project": project_id, "step": step, "status": "running", "log": [], "started": time.time(), "result": None, "scope": body.get("indices"), "mode": body.get("mode"), "opening": body.get("opening", False),
                   "summary": summary, "summary_before": fingerprint(p / "text/mini_summary.json")}
            if step == "2":
                # New segments outdate recordings and visuals only where their text changed.
                job["_script_before"] = read_json(p / "text/script.json", {})
            if motion_takes:
                job["_motion_takes"] = motion_takes
            self.jobs[job_id] = job
            environment = {**os.environ, **self.credentials, LIBRARY_ENV: str(self.library_root)}
            job["_secrets"] = [environment[k] for k in PROVIDER_KEYS if environment.get(k)]
            try:
                process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("runner.py")), json.dumps(request, ensure_ascii=False)], env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt", creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0)
            except OSError:
                self.jobs.pop(job_id, None)
                for index, take in motion_takes.items():
                    shutil.rmtree(takes.folder(p, int(index)) / take, ignore_errors=True)
                raise
            job["process"] = process
            threading.Thread(target=self.observe_job, args=(job, process), daemon=True).start()
            return self.public_job(job)

    @staticmethod
    def public_job(job):
        return {k: v for k, v in job.items() if k != "process" and not k.startswith("_")}

    def observe_job(self, job, process):
        for line in process.stdout:
            with self.lock:
                for secret in job.get("_secrets", []):
                    line = line.replace(secret, "[hidden API key]")
                if line.startswith("STUDIO_RESULT:"):
                    job["result"] = json.loads(line[len("STUDIO_RESULT:"):])
                else:
                    job["log"].append(line.rstrip())
                    job["log"] = job["log"][-300:]
        code = process.wait()
        process.stdout.close()
        with self.lock:
            job.pop("_secrets", None)
            job["status"] = "cancelled" if job["status"] == "cancelling" else "succeeded" if code == 0 and (job.get("result") or {}).get("success") else "failed"
            job["ended"] = time.time()
            p = self.project(job["project"])
            state = self.state(p)
            # Pipeline-owned rewrites (including the DOCX bridge) have already
            # been accounted for by this job, rather than being external edits.
            state["hashes"] = {k: fingerprint(p / "text" / filename) for k, filename in TEXT_FILES.items()}
            direction = job.get("summary") and state["hashes"]["summary"] != job["summary_before"]
            if direction:
                state.pop("summary_stale", None)
                if job.get("mode") == "segments":
                    self.direction_changed(state)
            if job.get("mode") == "summary":
                if direction:
                    self.direction_changed(state)
                self.persist(p, state)
                return
            if job.get("mode") == "edit_preview":
                self.persist(p, state)
                return
            generated = None
            if job.get("mode") == "segments":
                result = job.get("result") or {}
                generated = set(result.get("processed_segments", [])) & set(job.get("scope") or [])
                for index, take in job.get("_motion_takes", {}).items():
                    if int(index) in generated:
                        self.select(p, state, int(index), take)
                    elif not (takes.folder(p, int(index)) / take / "index.html").is_file():
                        shutil.rmtree(takes.folder(p, int(index)) / take, ignore_errors=True)
                if result.get("opening_generated"):
                    generated.add(0)
            if job["status"] != "succeeded" and not generated:
                # A failed or cancelled job leaves the step as it was; files it did rewrite are
                # picked up by the asset comparison in detail(), segment by segment.
                self.persist(p, state)
                return
            if job["step"] == "5":
                # Either kind of assembly leaves the 3:4 copy matching its panel settings.
                state.pop("three_by_four_stale", None)
                if job.get("mode") == "three_by_four":
                    # Only the 3:4 copy changed: the landscape master and later steps stay as they were.
                    self.persist(p, state)
                    return
            # Later steps are outdated for what this job made: its segments, plus the opening (0).
            scope = job.get("scope")
            if generated is not None:
                scope = sorted(generated)
            elif scope is not None:
                scope = sorted(set(scope) | ({0} if job.get("opening") else set()))
            before = job.pop("_script_before", None)
            if job["step"] == "2" and before is not None:
                self.resegmented(state, before, read_json(p / "text/script.json", {}))
            elif scope is None or scope:
                self.invalidate(state, job["step"], scope, project=p)
            if job["step"] == "4":
                # New images become their segments' visuals before the step's progress is counted.
                self.sync_takes(p, state)
            stale = state["stale"].get(job["step"])
            if stale and job["step"] in {"3", "4"}:
                script = read_json(p / "text/script.json", {})
                segments = script.get("segments", [])
                all_indices = {s["index"] for s in segments}
                global_settings = {**self.defaults().get("global", {}), **state.get("settings", {}).get("global", {})}
                if global_settings.get("opening_quote", True):
                    all_indices.add(0)
                completed = generated if generated is not None else set(job["scope"] if job["scope"] is not None else all_indices - {0})
                if job["step"] == "4" and job["mode"] in {"static", "render"}:
                    desired = "hf" if job["mode"] == "render" else "image"
                    completed &= {s["index"] for s in segments if s.get("visualizer", "image") == desired}
                if job["opening"] and generated is None:
                    completed.add(0)
                remaining = set(stale["segments"] or all_indices) - completed
                if remaining:
                    stale["segments"] = sorted(remaining)
                else:
                    state["stale"].pop(job["step"], None)
            else:
                state["stale"].pop(job["step"], None)
            self.persist(p, state)

    def cancel_job(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] != "running":
                return self.public_job(job)
            job["status"] = "cancelling"
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(job["process"].pid), "/T", "/F"], capture_output=True)
            else:
                try:
                    os.killpg(job["process"].pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            return self.public_job(job)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", self.allowed_origin())
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def allowed_origin(self):
        origin = self.headers.get("Origin", "")
        return origin if origin in {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost", "http://localhost:1420", "http://127.0.0.1:1420"} else "http://127.0.0.1:1420"

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.allowed_origin())
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        self.handle_api("GET")

    def do_POST(self):
        self.handle_api("POST")

    def handle_api(self, method):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        # Media elements cannot send headers, so their requests carry the token in the address.
        if parsed.path.startswith("/api/media") or parsed.path == "/api/music":
            supplied = query.get("token", [supplied])[0]
        if not secrets.compare_digest(supplied, self.server.token):
            return self.json({"error": "Unauthorized"}, 401)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
        studio = self.server.studio
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 8_000_000:
                raise ValueError("Request too large.")
            body = json.loads(self.rfile.read(length)) if length else {}
            if parts == ["api", "credentials"] and method == "POST":
                if os.environ.get("STUDIO_NATIVE_HOST") != "1":
                    raise ValueError("API keys are managed by the desktop app.")
                studio.set_credentials(body)
                return self.json({"applied": True})
            if parts == ["api", "google-adc"]:
                return self.json(studio.check_google_adc(body) if method == "POST" else studio.google_adc_status())
            if parts == ["api", "bootstrap"]:
                return self.json(studio.bootstrap())
            if parts == ["api", "voices"]:
                return self.json(studio.save_voice(body) if method == "POST" else {"voices": studio.voices()})
            if parts == ["api", "library"] and method == "GET":
                return self.json(studio.library.catalog())
            if parts == ["api", "library", "item"] and method == "GET":
                return self.json(studio.library.item(query["group"][0], query["id"][0], query.get("file", [None])[0]))
            if parts == ["api", "library", "history"] and method == "GET":
                return self.json(studio.library.history())
            if len(parts) == 3 and parts[:2] == ["api", "library"] and method == "POST":
                with studio.lock:
                    operation = parts[2]
                    if operation in {"save", "create"}:
                        return self.json(studio.library.save(body, create=operation == "create"))
                    if operation == "remove":
                        return self.json(studio.library.remove(body))
                    if operation == "import":
                        return self.json(studio.library.import_skill(body))
                    if operation == "restore":
                        return self.json(studio.library.restore(body))
            if parts == ["api", "music"] and method == "GET":
                return self.media(studio.music_file(query.get("name", [""])[0]))
            if parts == ["api", "music", "import"] and method == "POST":
                return self.json(studio.import_music(body))
            if parts == ["api", "projects"]:
                return self.json(studio.projects() if method == "GET" else studio.create(body))
            if parts == ["api", "projects", "open"] and method == "POST":
                return self.json(studio.open_existing(body))
            if parts == ["api", "projects", "remove"] and method == "POST":
                return self.json(studio.remove_projects(body))
            if parts == ["api", "project-path"] and method == "GET":
                # The native host asks where a project lives before it trusts a path.
                return self.json({"path": str(studio.project(query["project"][0]))})
            if parts == ["api", "jobs"]:
                return self.json([studio.public_job(j) for j in studio.jobs.values()])
            if len(parts) == 4 and parts[1] == "jobs" and parts[3] == "cancel" and method == "POST":
                return self.json(studio.cancel_job(parts[2]))
            if len(parts) >= 3 and parts[1] == "projects":
                project_id = parts[2]
                if len(parts) == 3 and method == "GET":
                    return self.json(studio.detail(project_id))
                if len(parts) == 4 and method == "POST":
                    action = parts[3]
                    if action == "save":
                        return self.json(studio.save(project_id, body))
                    if action == "job":
                        return self.json(studio.run_job(project_id, body))
                    if action == "panel-preview":
                        return self.json(studio.panel_preview(project_id))
                    if action == "agent":
                        return self.json(studio.prepare_agent(project_id, body))
                    if action == "take":
                        return self.json(studio.select_take(project_id, body))
                    if action == "delete-take":
                        return self.json(studio.delete_take(project_id, body))
                    if action == "delete-cover":
                        return self.json(studio.delete_cover(project_id, body))
                    if action == "finish-agent":
                        return self.json(studio.finish_agent(project_id, body))
                    if action == "restore":
                        return self.json(studio.restore(project_id, body))
                    p = studio.project(project_id)
                    if action == "writing":
                        return self.json(studio.save_writing(project_id, body))
                    if action == "opening":
                        return self.json(studio.set_opening(project_id, body))
                    if action == "settings":
                        with studio.lock:
                            # Cover settings change beside a running recording, visual or assembly job.
                            if studio.busy(project_id, "6" if set(body["settings"]) == {"step6"} and "source" not in body else None):
                                raise Conflict("Stop production before changing its settings.")
                            state = studio.state(p)
                            old = state.get("settings", {})
                            effective = studio.defaults()
                            for section, fields in old.items():
                                effective.setdefault(section, {}).update(fields)
                            # The engine's step-2 settings write the content summary used by Visuals.
                            # Writing settings apply to the next writing session; the manuscript stays current (as in Writing setup).
                            mapping = {"global": "3", "step2": "2", "step3": "3", "step4": "4", "step4_summary": "4", "step5": "5", "subtitles": "5", "step6": "6", "motion_opening": "4"}
                            # Fields that are sent replace saved ones; anything left out keeps its saved value.
                            settings = copy.deepcopy(old)
                            for section, fields in body["settings"].items():
                                settings[section] = {**old.get(section, {}), **fields} if isinstance(fields, dict) else fields
                            migrate_segmentation(settings)
                            output = lambda values: {k: v for k, v in values.items() if k not in PROVIDER_SETTINGS}
                            def differs(k):
                                before, after = effective.get(k, {}), {**effective.get(k, {}), **settings[k]}
                                if k == "step2":
                                    before, after = segmentation_output(before), segmentation_output(after)
                                return output(after) != output(before)
                            sections = [k for k in body["settings"] if k in mapping and differs(k)]
                            # The 3:4 copy is redrawn around the landscape master, so its panels outdate only that copy.
                            if "three_by_four" in body["settings"] and differs("three_by_four"):
                                state["three_by_four_stale"] = True
                            if "step4_summary" in sections:
                                # Visuals follow a new summary once it is regenerated.
                                state["summary_stale"] = True
                            # Settings outdate only their own step; later steps follow once it is regenerated
                            # and only where its outputs actually changed.
                            for s in sorted({mapping[k] for k in sections if k != "step4_summary"}, key=STAGES.index):
                                studio.mark_stale(state, s, "Production settings changed")
                            state["settings"] = settings
                            if "source" in body:
                                state["source"] = body["source"]
                            studio.persist(p, state)
                        return self.json(studio.detail(project_id))
                    if action == "keep-current":
                        # The user keeps a step's outputs as they are: it no longer needs a rebuild.
                        with studio.lock:
                            step = str(body.get("step"))
                            if studio.busy(project_id, step):
                                raise Conflict("Wait for the running job to finish first.")
                            state = studio.state(p)
                            if step not in STAGES:
                                raise ValueError("Unknown step.")
                            state["stale"].pop(step, None)
                            if step == "4" and body.get("summary"):
                                state.pop("summary_stale", None)
                            if step == "5" and body.get("three_by_four"):
                                state.pop("three_by_four_stale", None)
                            studio.persist(p, state)
                        return self.json(studio.detail(project_id))
                    if action == "review":
                        with studio.lock:
                            state = studio.state(p)
                            if studio.busy(project_id, str(body["step"])) or not studio.detail(project_id)["ready"].get(body["step"]) or body["step"] in state["stale"]:
                                raise Conflict("Generate current outputs and finish production before marking them reviewed.")
                            state["reviewed"][body["step"]] = datetime.now().isoformat()
                            studio.persist(p, state)
                        return self.json(studio.detail(project_id))
                    if action == "select-cover":
                        with studio.lock:
                            path = body["path"]
                            if path not in studio.detail(project_id)["covers"]:
                                raise ValueError("Choose an existing cover candidate.")
                            state = studio.state(p)
                            state["selected_cover"] = path
                            studio.persist(p, state)
                        return self.json(studio.detail(project_id))
                    if action == "restart":
                        return self.json(studio.restart(project_id, body))
            if parts == ["api", "media"] and method == "GET":
                p = studio.project(query["project"][0])
                path = (p / query["path"][0]).resolve()
                if not path.is_relative_to(p) or path.suffix.lower() not in MEDIA or not path.is_file():
                    raise ValueError("Media is not a project artifact.")
                return self.media(path)
            return self.json({"error": "Unknown operation"}, 404)
        except (Conflict, LibraryConflict) as e:
            self.json({"error": str(e)}, 409)
        except (ValueError, KeyError, StopIteration, OSError, TypeError) as e:
            self.json({"error": str(e)}, 400)
        except Exception as e:
            self.json({"error": f"{type(e).__name__}: {e}"}, 500)

    def media(self, path):
        size = path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match:
                self.send_error(416)
                return
            a, b = match.groups()
            if not a:
                start = max(0, size - int(b or "0"))
            else:
                start = int(a)
                end = min(int(b), end) if b else end
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", self.allowed_origin())
        self.send_header("Cache-Control", "no-cache")
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining:
                    data = f.read(min(65536, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, help="Bundled engine: code, prompts, skills, music, defaults")
    parser.add_argument("--data", required=True, help="User data: editable library and project registry")
    parser.add_argument("--workspace", required=True, help="Default folder for new projects")
    parser.add_argument("--legacy-config", help="Runtime settings of an earlier build, imported once")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    # The native host clears inherited Python paths. Shared rendering modules
    # must resolve from this installation, regardless of the process cwd.
    engine = Path(args.engine).resolve()
    sys.path.insert(0, str(engine))
    studio = Studio(engine, Path(args.data), Path(args.workspace))
    if args.legacy_config and Path(args.legacy_config).is_file():
        studio.adopt_legacy(Path(args.legacy_config))
    token = os.environ.get("STUDIO_TOKEN") or secrets.token_hex(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.studio, server.token = studio, token
    print(json.dumps({"port": server.server_port}), flush=True)
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # The native host closes stdin when it exits, allowing child jobs to stop cleanly.
    if not sys.stdin.isatty():
        def watch_host():
            line = sys.stdin.readline()
            if line.strip() == "shutdown" or not line and os.environ.get("STUDIO_NATIVE_HOST"):
                stop()
        threading.Thread(target=watch_host, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        for job in studio.jobs.values():
            if job["status"] in {"running", "cancelling"}:
                studio.cancel_job(job["id"])
                try:
                    job["process"].wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        job["process"].kill()
                    else:
                        os.killpg(job["process"].pid, signal.SIGKILL)
        server.server_close()


if __name__ == "__main__":
    main()
