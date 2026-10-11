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

STAGES = ["1", "1.5", "3", "4", "5", "6"]
# Segments keeps the engine's id "1.5" but is shown as step 2.
DISPLAY = {"1.5": "2"}
LINKS_FILE = "projects.json"
HIDDEN_FILE = "hidden-projects.json"
GOOGLE_CHECK_FILE = "google-adc-check.json"
LIBRARY_ENV = "AIGC_VIDEO_LIBRARY_DIR"
MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".mp4", ".mov", ".webm", ".srt", ".pdf", ".txt"}
TEXT_FILES = {"raw": "raw.json", "script": "script.json", "summary": "mini_summary.json"}
METADATA_FIELDS = ("video_titles", "cover_titles", "cover_subtitles", "golden_quotes")
# Settings the desktop keeps for itself; the engine's configuration rejects unknown keys.
DESKTOP_SETTINGS = {"step1": {"writing_mode"}}
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
    """Project settings as the engine's configuration accepts them."""
    return {section: {k: v for k, v in values.items() if k not in DESKTOP_SETTINGS.get(section, ())}
            for section, values in settings.items()}


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
        self.lock = threading.RLock()
        self.jobs = {}
        # Received from the native host; never included in project data or API responses.
        self.credentials = {}
        # A terminal lease belongs to this service instance; recover after an app crash.
        for path in (root / ".studio/state.json" for root in self.roots()):
            try:
                state = read_json(path, {})
            except OSError:
                continue  # Unreadable (for example, folder access was not granted); listed as such.
            active = state.get("agent_active")
            if active:
                try:
                    os.kill(active.get("owner", -1), 0) if active.get("owner") else (_ for _ in ()).throw(ProcessLookupError())
                    continue
                except (ProcessLookupError, PermissionError):
                    pass
                state.pop("agent_active", None)
                state.setdefault("stale", {})[active["step"]] = {"reason": "Previous agent session was interrupted. Review its outputs.", "segments": active.get("indices", [])}
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
        return read_json(p / ".studio/state.json", {"stale": {}, "reviewed": {}, "settings": {}, "source": "", "events": []})

    def persist(self, p: Path, state):
        atomic_json(p / ".studio/state.json", state)

    def invalidate(self, state, stage, indices=None):
        if stage == "1":
            # The visual direction is written from the manuscript.
            state["summary_stale"] = True
        for s in STAGES[STAGES.index(stage) + 1:]:
            # Covers consume text metadata, not voice or visuals.
            if s == "6" and stage in {"3", "4", "5"}:
                continue
            previous = state["stale"].get(s)
            scope = sorted(set(indices) | set(previous["segments"])) if indices and previous and previous["segments"] else indices or []
            if previous and not previous["segments"]:
                scope = []
            state["stale"][s] = {"reason": f"Step {DISPLAY.get(stage, stage)} changed", "segments": scope}
            state["reviewed"].pop(s, None)
        state["reviewed"].pop(stage, None)

    def direction_changed(self, state):
        """Every visual follows the shared direction, so a new one supersedes them."""
        self.invalidate(state, "4")
        state["stale"]["4"] = {"reason": "Visual direction changed", "segments": []}

    @staticmethod
    def needs_summary(p: Path, state) -> bool:
        """Visuals create their shared direction first when it is missing or outdated."""
        return bool(state.get("summary_stale")) or not str(read_json(p / "text/mini_summary.json", {}).get("summary", "")).strip()

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
        return {k: v for k, v in data.items() if isinstance(v, dict) and k in {"global", "step1", "step1_5", "step2", "step3", "step4", "step5", "step6", "subtitles", "hyperframes_opening"}}

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
            "HyperFrames": os.environ.get("AIGC_HYPERFRAMES_CLI"),
            "Chrome Headless Shell": os.environ.get("HYPERFRAMES_BROWSER_PATH"),
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
        return {"default_location": str(self.workspace), "library_folder": str(self.library_root), "defaults": self.defaults(), "skills": skills,
                "library": self.library.catalog(), "music": [p.name for p in (self.engine / "music").glob("*") if p.suffix.lower() in {".mp3", ".wav"}],
                "components": self.components()}

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

    def detail(self, project_id):
        p = self.project(project_id)
        with self.lock:
            state = self.state(p)
            # Projects from before Context merged into Visuals carry its markers over.
            if state["stale"].pop("2", None):
                state["summary_stale"] = True
            state["reviewed"].pop("2", None)
            raw = read_json(p / "text/raw.json", {})
            script = read_json(p / "text/script.json", {})
            summary = read_json(p / "text/mini_summary.json", {})
            hashes = {k: fingerprint(p / "text" / v) for k, v in TEXT_FILES.items()}
            old = state.get("hashes", {})
            for key, stage in [("raw", "1"), ("script", "1.5")]:
                if old.get(key) is not None and old[key] != hashes[key]:
                    self.invalidate(state, stage)
            if old.get("summary") is not None and old["summary"] != hashes["summary"]:
                self.direction_changed(state)
            state["hashes"] = hashes
            self.persist(p, state)
            segments = []
            for s in script.get("segments", []):
                idx = int(s["index"])
                audio = self.asset(p, "voice", f"voice_{idx}", [".wav", ".mp3"])
                extensions = [".mp4", ".mov", ".webm", ".png", ".jpg"] if s.get("visualizer") == "hf" else [".png", ".jpg", ".jpeg", ".mp4", ".mov"]
                visual = self.asset(p, "images", f"segment_{idx}", extensions)
                html = p / "images/hyperframes" / f"segment_{idx}/index.html"
                segments.append({**s, "audio": str(audio.relative_to(p)) if audio else None, "visual": str(visual.relative_to(p)) if visual else None, "duration": duration(audio), "composition": str(html.relative_to(p)) if html.exists() else None})
            artifact_paths = [f for folder in ["images", "voice"] for f in (p / folder).rglob("*") if f.is_file() and f.suffix.lower() in MEDIA | {".html"}]
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
                        self.invalidate(state, stage, indices)
            state["assets"] = assets
            opening_visual = self.asset(p, "images", "opening", [".mp4", ".mov", ".png", ".jpg"])
            opening_audio = self.asset(p, "voice", "opening", [".wav", ".mp3"])
            covers = [str(f.relative_to(p)) for f in sorted(p.glob("cover_*")) if f.suffix.lower() in MEDIA]
            ready = {"1": bool(raw.get("content")), "1.5": bool(segments), "3": bool(segments) and all(s["audio"] for s in segments), "4": bool(segments) and all(s["visual"] for s in segments), "5": (p / "final_video.mp4").is_file(), "6": bool(covers)}
            opening_required = state.get("settings", {}).get("global", self.defaults().get("global", {})).get("opening_quote", True)
            if opening_required:
                ready["3"] = ready["3"] and bool(opening_audio)
                ready["4"] = ready["4"] and bool(opening_visual)
            self.persist(p, state)
            revision = hashlib.sha256(json.dumps([hashes, assets, state["stale"], state["reviewed"], state.get("settings"), state.get("source"), state.get("summary_stale")], sort_keys=True).encode()).hexdigest()
            return {"id": project_id, "revision": revision, "path": str(p), "raw": raw, "script": script, "summary": summary, "hashes": hashes, "segments": segments, "state": state, "ready": ready, "covers": covers, "opening": {"visual": str(opening_visual.relative_to(p)) if opening_visual else None, "audio": str(opening_audio.relative_to(p)) if opening_audio else None, "duration": duration(opening_audio)}, "exports": [f for f in ["final_video.mp4", "final_video_3x4.mp4"] if (p / f).exists()]}

    def busy(self, project_id):
        return any(j["project"] == project_id and j["status"] in {"running", "cancelling"} for j in self.jobs.values()) or bool(self.state(self.project(project_id)).get("agent_active"))

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
                raise ValueError("The visual direction cannot be empty.")
            if editable(value) == editable(previous):
                # Nothing changed (for example a switch flipped and flipped back): later steps stay current.
                return self.detail(project_id)
            self.snapshot(p, [path], f"Before editing {kind}")
            atomic_json(path, value)
            state = self.state(p)
            if kind == "summary":
                state.pop("summary_stale", None)
                self.direction_changed(state)
            else:
                stage = {"raw": "1", "script": "1.5"}[kind]
                self.invalidate(state, "4" if visual_only else stage, body.get("segments"))
                if visual_only:
                    changed = [b["index"] for a, b in zip(previous["segments"], value["segments"]) if a.get("visualizer", "image") != b.get("visualizer", "image")]
                    old = state["stale"].get("4")
                    state["stale"]["4"] = {"reason": "Visual type changed", "segments": [] if old and not old["segments"] else sorted(set(changed) | set(old["segments"] if old else []))}
                else:
                    state["stale"].pop(stage, None)
            state.setdefault("hashes", {})[kind] = fingerprint(path)
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
            changed = "1" if "text/raw.json" in files else "1.5" if "text/script.json" in files else "3" if any(f.startswith("voice/") for f in files) else "4" if any(f.startswith("images/") for f in files) else "5"
            if changed == "5" and "text/mini_summary.json" in files:
                # Only the visual direction came back; visuals made from another one are outdated.
                self.direction_changed(state)
            else:
                self.invalidate(state, changed)
                state["stale"].pop(changed, None)
            self.persist(p, state)
        return self.detail(project_id)

    def prepare_agent(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            if self.busy(project_id):
                raise Conflict("This project already has an active job or agent.")
            if body["step"] == "4" and self.needs_summary(p, self.state(p)):
                raise Conflict("Generate the visual direction before revising motion.")
            from context import prepare
            launch = prepare(self, p, body)
            media = self.scoped_media(list((p / "images").rglob("*")), p, body.get("indices"))
            self.snapshot(p, list((p / "text").glob("*")) if body["step"] == "1" else media + [p / "text/script.json"], f"Before Claude Code · step {body['step']}")
            if body["step"] == "4":
                script = read_json(p / "text/script.json", {})
                chosen = body.get("indices") or [s["index"] for s in script["segments"]]
                for segment in script["segments"]:
                    if segment["index"] in chosen:
                        segment["visualizer"] = "hf"
                atomic_json(p / "text/script.json", script)
            state = self.state(p)
            if body["step"] == "4":
                state.setdefault("hashes", {})["script"] = fingerprint(p / "text/script.json")
            state["agent_active"] = {"step": body["step"], "indices": body.get("indices", []), "launch": launch["id"], "owner": os.getpid(), "raw_before": fingerprint(p / "text/raw.json")}
            self.persist(p, state)
            return launch

    def finish_agent(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            state = self.state(p)
            active = state.get("agent_active")
            warning = None
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
                if active["step"] != "1" or active.get("raw_before") != fingerprint(p / "text/raw.json"):
                    self.invalidate(state, active["step"], active["indices"])
                if warning:
                    state["stale"][active["step"]] = {"reason": warning, "segments": []}
                else:
                    state["stale"].pop(active["step"], None)
                if active["step"] == "4":
                    state["stale"]["4"] = {"reason": "Render the revised motion compositions to review the new visuals", "segments": active["indices"]}
                    missing = [i for i in active["indices"] or [] if not (p / "images/hyperframes" / f"segment_{i}" / "index.html").is_file()]
                    if missing:
                        warning = f"Claude Code ended without a composition for segment {', '.join(map(str, missing))}. Activate Claude Code for it again."
            self.persist(p, state)
        result = self.detail(project_id)
        result["warning"] = warning
        return result

    def run_job(self, project_id, body):
        p = self.project(project_id)
        with self.lock:
            if self.busy(project_id):
                raise Conflict("Finish the active project job or agent first.")
            step = str(body["step"])
            if step not in STAGES or step == "1":
                raise ValueError("Step 1 runs interactively through Activate Claude Code.")
            if step == "4" and body.get("mode") not in {"static", "render", "opening", "summary"}:
                raise ValueError("Animated generation runs through the interactive Claude Code terminal.")
            if step == "5":
                detail = self.detail(project_id)
                if any(not detail["ready"][s] or s in detail["state"]["stale"] for s in ["1.5", "3", "4"]):
                    raise Conflict("Regenerate the outdated script, recordings, and visuals before assembling this production.")
            if step == "6" and (p / "text/script.json").exists() and "1.5" in self.state(p)["stale"]:
                raise Conflict("Regenerate segmentation to apply the current title and cover metadata first.")
            if step == "4" and body.get("batch"):
                if body["batch"] not in {"all", "missing"}:
                    raise ValueError("Choose all images or missing images.")
                detail = self.detail(project_id)
                if not detail["ready"]["1.5"] or "1.5" in detail["state"]["stale"]:
                    raise Conflict("Generate current segments before generating all images.")
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
                required = [("cover_styles", "step6", "cover_image_style")] if step == "6" else [
                    ("image_styles", "step4", "image_style_preset"),
                    ("image_templates", "step4", "image_prompt_template"),
                ] if body.get("mode") not in {"render", "summary"} else []
                for group, section, key in required:
                    # A minimal test engine may not install image-generation resources.
                    if self.library.group(group)[2].exists():
                        self.library.item(group, settings.get(section, {}).get(key, ""))
            job_id = uuid.uuid4().hex
            direction_only = body.get("mode") == "summary"
            summary = step == "4" and body.get("mode") != "render" and (direction_only or self.needs_summary(p, self.state(p)))
            dirs = {"1.5": ["text"], "3": ["voice"], "4": [] if direction_only else ["images"], "5": [], "6": []}[step]
            paths = [f for d in dirs for f in (p / d).rglob("*")]
            if step in {"3", "4"}:
                paths = self.scoped_media(paths, p, body.get("indices"), body.get("opening", False) or body.get("mode") == "opening")
            if summary:
                paths += [p / "text/mini_summary.json", p / "text/raw.docx"]
            if step == "5":
                paths = list(p.glob("final_video*.mp4"))
            if step == "6":
                paths = list(p.glob("cover_*"))
            self.snapshot(p, paths, "Before regenerating the visual direction" if direction_only else f"Before regenerating step {DISPLAY.get(step, step)}")
            request = {**body, "summary": summary, "project": str(p), "engine": str(self.engine), "settings": self.state(p).get("settings", {})}
            job = {"id": job_id, "project": project_id, "step": step, "status": "running", "log": [], "started": time.time(), "result": None, "scope": body.get("indices"), "mode": body.get("mode"), "opening": body.get("opening", False),
                   "summary": summary, "summary_before": fingerprint(p / "text/mini_summary.json")}
            self.jobs[job_id] = job
            environment = {**os.environ, **self.credentials, LIBRARY_ENV: str(self.library_root)}
            job["_secrets"] = [environment[k] for k in PROVIDER_KEYS if environment.get(k)]
            process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("runner.py")), json.dumps(request, ensure_ascii=False)], env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt", creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0)
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
            if job.get("mode") == "summary":
                if direction:
                    self.direction_changed(state)
                self.persist(p, state)
                return
            if job["status"] != "succeeded":
                # A failed or cancelled job leaves the step as it was; files it did rewrite are
                # picked up by the asset comparison in detail(), segment by segment.
                self.persist(p, state)
                return
            # Later steps are outdated for what this job made: its segments, plus the opening (0).
            scope = job.get("scope")
            if scope is not None:
                scope = sorted(set(scope) | ({0} if job.get("opening") else set()))
            if scope is None or scope:
                self.invalidate(state, job["step"], scope)
            stale = state["stale"].get(job["step"])
            if stale and job["step"] in {"3", "4"}:
                script = read_json(p / "text/script.json", {})
                segments = script.get("segments", [])
                all_indices = {s["index"] for s in segments}
                global_settings = {**self.defaults().get("global", {}), **state.get("settings", {}).get("global", {})}
                if global_settings.get("opening_quote", True):
                    all_indices.add(0)
                completed = set(job["scope"] if job["scope"] is not None else all_indices - {0})
                if job["step"] == "4" and job["mode"] in {"static", "render"}:
                    desired = "hf" if job["mode"] == "render" else "image"
                    completed &= {s["index"] for s in segments if s.get("visualizer", "image") == desired}
                if job["opening"]:
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
        if parsed.path.startswith("/api/media"):
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
                    if action == "agent":
                        return self.json(studio.prepare_agent(project_id, body))
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
                            if studio.busy(project_id):
                                raise Conflict("Stop production before changing its settings.")
                            state = studio.state(p)
                            old = state.get("settings", {})
                            effective = studio.defaults()
                            for section, fields in old.items():
                                effective.setdefault(section, {}).update(fields)
                            # The engine's step-2 settings write the visual direction used by Visuals.
                            mapping = {"global": "3", "step1": "1", "step1_5": "1.5", "step2": "4", "step3": "3", "step4": "4", "step5": "5", "subtitles": "5", "step6": "6", "hyperframes_opening": "4"}
                            # Fields that are sent replace saved ones; anything left out keeps its saved value.
                            settings = copy.deepcopy(old)
                            for section, fields in body["settings"].items():
                                settings[section] = {**old.get(section, {}), **fields} if isinstance(fields, dict) else fields
                            sections = [k for k in body["settings"] if k in mapping and {**effective.get(k, {}), **settings[k]} != effective.get(k, {})]
                            changed = [mapping[k] for k in sections]
                            if "step2" in sections:
                                state["summary_stale"] = True
                            if changed:
                                earliest = min(changed, key=STAGES.index)
                                studio.invalidate(state, earliest)
                                for s in changed:
                                    state["stale"][s] = {"reason": "Production settings changed", "segments": []}
                            state["settings"] = settings
                            if "source" in body:
                                state["source"] = body["source"]
                            studio.persist(p, state)
                        return self.json(studio.detail(project_id))
                    if action == "review":
                        with studio.lock:
                            state = studio.state(p)
                            if studio.busy(project_id) or not studio.detail(project_id)["ready"].get(body["step"]) or body["step"] in state["stale"]:
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
    studio = Studio(Path(args.engine), Path(args.data), Path(args.workspace))
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
