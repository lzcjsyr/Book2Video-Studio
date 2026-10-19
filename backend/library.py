"""Edit the skill packages and generation templates the engine uses.

The library is a user-writable copy of the engine's ``prompts/`` and
``skills/``; the engine reads it through AIGC_VIDEO_LIBRARY_DIR.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import string
import tempfile
import uuid
import zipfile
from datetime import datetime

import yaml


class LibraryConflict(Exception):
    pass


GROUPS = {
    "writing_skills": ("Writing skills", "skill", "skills/step1"),
    "summary": ("Content summary prompt", "prompt", "prompts/step2_summary.md"),
    "image_styles": ("Image styles", "style", "prompts/step4_styles.yaml"),
    "image_templates": ("Image templates", "image_template", "prompts/step4_image_templates.yaml"),
    "motion_styles": ("Motion styles", "motion_style", "prompts/step4_motion_styles.yaml"),
    "motion_skills": ("Motion skills", "skill", "skills/step4"),
    "assembly_skills": ("Assembly skills", "skill", "skills/step5"),
    "cover_styles": ("Cover styles", "style", "prompts/step6_styles.yaml"),
    "templates": ("Production templates", "prompt", "prompts"),
}
PROMPTS = {
    "step1_no_skill.md": ("Writing without a skill", None, {"input_file", "output_json", "text_dir", "extra_requirements"}),
    "step1_agent.md": ("Writing agent template", None, {"input_file", "output_json", "text_dir", "skill_path", "extra_requirements"}),
    "step1_5_segment.md": ("Segmentation template", None, {"raw_json_path", "output_json_path", "target_segments"}),
    "step4_motion_agent.md": ("Motion agent template", True, {"skill_path_context", "style_preset", "style_context", "payload_json", "target_index_html_path"}),
    "step4_opening_bg.md": ("Opening image template", True, {"summary", "style_block"}),
    "step4_safety.md": ("Image prompt refinement", True, {"prompt"}),
    "step6_cover.md": ("Cover image template", True, {"cover_title", "cover_subtitle", "style_block"}),
    "step6_cover_agent.md": ("Claude Code cover template", True, {"design_dir", "entries_json"}),
    "step1_studio.md": ("Writing Studio instructions", True, {"project_dir", "text_dir", "output_json", "opening_quote_state"}),
    "step4_studio.md": ("Visuals Studio instructions", True, {"agent_prompt", "segment_index", "work_dir", "snapshots_dir", "segment_count", "task_number", "target_path", "tasks", "segments"}),
    "step4_skill_context.md": ("Motion reference instructions", True, {"skills_root", "reference_limit", "recipe_paths", "reference_paths", "skill_entries"}),
    "step4_style_context.md": ("Motion style instructions", True, {"style_id", "label", "official_style", "palette", "best_for", "style_definition", "palette_definition", "references"}),
    "step5_editing_agent.md": ("Claude Code assembly template", None, {"style", "skill_path", "materials", "video_size", "narration_speed", "subtitles", "music", "opening", "session", "work_dir", "delivery"}),
}
TEXT_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".json", ".py", ".js", ".mjs", ".ts", ".tsx", ".html", ".css", ".toml"}
TEMPLATE_SECTIONS = {
    "step1_studio.md": {"direct", "shared"},
    "step4_studio.md": {"segment", "new", "single", "multi", "target", "task", "finish"},
    "step6_cover_agent.md": {"main", "revise"},
}
MAX_TEXT = 2_000_000
MAX_PACKAGE = 128_000_000


def digest(path: Path) -> str:
    if not path.exists():
        return ""
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    checksum = hashlib.sha256()
    for file in sorted(path.rglob("*")):
        if file.is_symlink():
            raise ValueError("Skill packages cannot contain symbolic links.")
        if file.is_file():
            checksum.update(file.relative_to(path).as_posix().encode())
            checksum.update(b"\0" + file.read_bytes())
    return checksum.hexdigest()


def write_bytes(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install_bundled(bundled: Path, root: Path):
    """Bring the bundled prompts and skills into the editable library.

    New files are added and files the person never touched follow the bundled
    version. Their edits and deletions stay as they left them.
    """
    record_path = root / ".bundled.json"
    try:
        installed = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        installed = {}
    for top in ("prompts", "skills"):
        for source in sorted((bundled / top).rglob("*")):
            relative = source.relative_to(bundled)
            if not source.is_file() or any(p.startswith(".") or p == "__pycache__" for p in relative.parts):
                continue
            key = relative.as_posix()
            target = root / relative
            # A removed skill package stays removed even if an update adds files to it.
            package = PurePosixPath(*relative.parts[:3]).as_posix() + "/" if top == "skills" and len(relative.parts) > 3 else None
            if package and not (root / package).exists() and any(k.startswith(package) for k in installed):
                continue
            current = digest(target)
            if not current and key in installed:
                continue
            new = digest(source)
            if current and current != new and current != installed.get(key):
                continue
            if current != new:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
                shutil.copy2(source, temporary)
                temporary.replace(target)
            installed[key] = new
    # A file the bundle no longer ships goes too, unless the person edited it; then it is theirs.
    for key in [k for k in installed if not (bundled / k).is_file()]:
        target = root / key
        if target.is_file() and digest(target) == installed[key]:
            target.unlink()
            parent = target.parent
            while parent != root and parent.parent != root and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        del installed[key]
    write_bytes(record_path, json.dumps(installed, ensure_ascii=False, indent=1).encode())


class LiteralDumper(yaml.SafeDumper):
    pass


LiteralDumper.add_representer(str, lambda dumper, value: dumper.represent_scalar(
    "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None))


class Library:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.history_root = self.confined(self.root / ".studio-library")

    def group(self, group):
        if group not in GROUPS:
            raise ValueError("Unknown skill or template group.")
        label, kind, relative = GROUPS[group]
        path = self.confined(self.root / relative)
        return label, kind, path

    def confined(self, path):
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("The source file is outside the skill and template library.")
        return resolved

    def identifier(self, value, package=False):
        if not isinstance(value, str) or not value or len(value) > 80:
            raise ValueError("Enter a valid library item ID.")
        if package:
            if value.startswith(".") or value.endswith((" ", ".")) or re.search(r'[\\/:*?"<>|\x00-\x1f]', value) or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
                raise ValueError("Enter a folder name compatible with Mac and Windows.")
        elif not re.fullmatch(r"[a-z][a-z0-9_-]*", value):
            raise ValueError("Use a lowercase letter first, followed by letters, numbers, underscores or hyphens.")
        return value

    def mapping(self, path):
        try:
            data = yaml.safe_load(self.read_text(path)) if path.exists() else {}
        except yaml.YAMLError:
            raise ValueError("The source YAML is invalid. Open the source editor to repair it.")
        if not isinstance(data, dict):
            raise ValueError("The style or template source must contain a list of named items.")
        return data or {}

    def catalog(self):
        groups = []
        for group, (label, kind, _) in GROUPS.items():
            _, _, path = self.group(group)
            items = []
            error = None
            if kind == "skill":
                for skill in sorted(path.glob("*/SKILL.md")):
                    package = self.confined(skill.parent)
                    if package.parent != path:
                        continue
                    items.append({"id": package.name, "label": package.name})
            elif group == "summary":
                if path.is_file():
                    items.append({"id": "summary", "label": "Content summary instructions"})
            elif group == "templates":
                items = [{"id": name, "label": info[0]} for name, info in PROMPTS.items() if (path / name).is_file()]
            else:
                try:
                    data = self.mapping(path)
                    for name, value in data.items():
                        self.identifier(name)
                        self.validate(group, name, value)
                except ValueError as exception:
                    data = {}
                    error = str(exception)
                for name, value in data.items():
                    items.append({"id": name, "label": value.get("label", name) if isinstance(value, dict) else name,
                                  "description": (value.get("description") or value.get("best_for") or "") if isinstance(value, dict) else str(value) if group in {"cover_styles", "image_styles"} else str(value)[:100]})
            groups.append({"id": group, "label": label, "kind": kind, "items": items,
                           "hash": digest(path) if kind not in {"skill", "prompt"} else "", "path": str(path), "error": error})
        return {"groups": groups}

    def item(self, group, item_id, file=None):
        label, kind, root = self.group(group)
        files = []
        variables = []
        if kind == "skill":
            self.identifier(item_id, package=True)
            package = self.confined(root / item_id)
            if package.parent != root or not (package / "SKILL.md").is_file():
                raise ValueError("The selected skill does not exist. Refresh the library.")
            for candidate in sorted(package.rglob("*")):
                if candidate.is_file() and candidate.suffix.lower() in TEXT_EXTENSIONS and not any(part.startswith(".") for part in candidate.relative_to(package).parts):
                    if not self.confined(candidate).is_relative_to(package):
                        raise ValueError("Skill references must stay inside their package.")
                    files.append(candidate.relative_to(package).as_posix())
            file = file or "SKILL.md"
            if file not in files:
                raise ValueError("Choose an editable file from this skill package.")
            path = self.confined(package / file)
            value = self.read_text(path)
            label = item_id
        elif group == "summary":
            if item_id != "summary":
                raise ValueError("Choose the summary skill.")
            path = root
            value = self.read_text(path)
        elif group == "templates":
            if item_id not in PROMPTS:
                raise ValueError("Choose an existing production template.")
            label, _, fields = PROMPTS[item_id]
            variables = sorted(fields)
            path = self.confined(root / item_id)
            value = self.read_text(path)
        elif item_id == "__source__":
            path = root
            value = self.read_text(path)
            kind = "source"
            label = "Source file"
        else:
            self.identifier(item_id)
            path = root
            data = self.mapping(path)
            if item_id not in data:
                raise ValueError("The selected style or template no longer exists. Choose another one.")
            value = data[item_id]
            label = value.get("label", item_id) if isinstance(value, dict) else item_id
            if kind == "image_template":
                variables = ["summary", "segment", "style_block"]
        return {"group": group, "id": item_id, "kind": kind, "label": label, "value": value,
                "hash": digest(path), "path": str(path), "folder": str(path.parent),
                "package_hash": digest(package) if kind == "skill" else None,
                "files": files, "file": file, "variables": variables, "removable": kind not in {"prompt", "source"}}

    def prompt(self, name, section=None, **values):
        """Read current user instructions; sections select mode-specific text."""
        text = self.item("templates", name)["value"]
        self.validate("templates", name, text)
        if section:
            pieces = re.split(r"(?m)^<!-- section: ([a-z_]+) -->\s*\n", text)
            sections = dict(zip(pieces[1::2], pieces[2::2]))
            if section not in sections:
                raise ValueError(f"Template {name} is missing its {section} section.")
            text = sections[section]
        if PROMPTS[name][1]:
            return text.format(**values)
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        return text

    def read_text(self, path):
        if not path.is_file() or path.stat().st_size > MAX_TEXT:
            raise ValueError("Choose an existing text file smaller than 2 MB.")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ValueError("The source file must use UTF-8 text encoding.")

    def format_fields(self, content, required):
        try:
            fields = {field for _, field, _, _ in string.Formatter().parse(content) if field}
            if fields != required:
                raise ValueError()
            content.format(**{field: "example" for field in required})
        except (ValueError, KeyError, IndexError, AttributeError):
            raise ValueError("Keep all required template variables and escape literal braces as {{ and }}.")

    def validate(self, group, item_id, value):
        _, kind, _ = self.group(group)
        try:
            size = len(json.dumps(value, ensure_ascii=False).encode())
        except (TypeError, ValueError):
            raise ValueError("Enter a valid style or template definition.")
        if size > MAX_TEXT:
            raise ValueError("Library text cannot be empty and must be smaller than 2 MB.")
        if kind in {"skill", "prompt", "style"}:
            if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > MAX_TEXT:
                raise ValueError("Library text cannot be empty and must be smaller than 2 MB.")
            if group == "templates":
                if item_id in TEMPLATE_SECTIONS:
                    sections = re.findall(r"(?m)^<!-- section: ([a-z_]+) -->\s*$", value)
                    if set(sections) != TEMPLATE_SECTIONS[item_id] or len(sections) != len(set(sections)):
                        raise ValueError("Keep all named template sections.")
                _, formatted, fields = PROMPTS[item_id]
                if formatted:
                    self.format_fields(value, fields)
                elif any("{" + field + "}" not in value for field in fields):
                    raise ValueError("Keep all required template variables.")
        elif kind == "image_template":
            if not isinstance(value, dict) or set(value) != {"label", "description", "template"} or any(not isinstance(v, str) or not v.strip() for v in value.values()):
                raise ValueError("Enter a name, description, and template.")
            self.format_fields(value["template"], {"summary", "segment", "style_block"})
        elif kind == "motion_style":
            if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k].strip() for k in ["label", "official_style", "palette"]):
                raise ValueError("Enter a name, motion style, and palette.")
            if not isinstance(value.get("best_for", ""), str) or not isinstance(value.get("references", []), list):
                raise ValueError("Enter valid motion guidance and reference paths.")
            for reference in value.get("references", []):
                if not isinstance(reference, str) or not reference.strip():
                    raise ValueError("Enter valid motion guidance and reference paths.")
                base = self.root / "skills/step4"
                target = (base / reference.split("#", 1)[0]).resolve()
                if not target.is_relative_to(base.resolve()) or not target.is_file():
                    raise ValueError("Motion references must name existing files inside the motion skills folder.")

    def snapshot(self, group, item_id, path, kind="file"):
        event_id = uuid.uuid4().hex
        directory = self.confined(self.history_root / event_id)
        directory.mkdir(parents=True)
        backup = directory / "before"
        if path.exists() and kind == "file":
            shutil.copy2(path, backup)
        event = {"id": event_id, "group": group, "item": item_id, "time": datetime.now().isoformat(),
                 "target": path.relative_to(self.root).as_posix(), "kind": kind, "before_exists": path.exists(), "after_hash": None}
        return event, backup

    def retire_invalid(self, item_id, bundled):
        """Replace an edited template the app can no longer use (it fails the current variable and section
        rules) with the bundled one. The edited text stays in revision history, to restore or copy from."""
        path = self.confined(self.root / "prompts" / item_id)
        if not path.is_file():
            return False
        try:
            self.validate("templates", item_id, path.read_text(encoding="utf-8", errors="replace"))
            return False
        except ValueError:
            pass
        event, _ = self.snapshot("templates", item_id, path)
        write_bytes(path, bundled.read_bytes())
        self.record(event, path)
        return True

    def record(self, event, path):
        event["after_hash"] = digest(path)
        write_bytes(self.history_root / event["id"] / "event.json", json.dumps(event, ensure_ascii=False).encode())
        return {"revision": event["id"]}

    def save(self, body, create=False):
        group, item_id = body["group"], body["id"]
        _, kind, root = self.group(group)
        if create and kind in {"skill", "prompt"}:
            raise ValueError("Import a skill package or edit an existing template.")
        if create:
            self.identifier(item_id)
            path = root
            if body.get("expected_hash") != digest(path):
                raise LibraryConflict("The library changed outside the editor. Refresh before saving; your draft is kept.")
            data = self.mapping(path)
            if item_id in data:
                raise ValueError("This ID already exists. Choose another ID.")
        else:
            item = self.item(group, item_id, body.get("file"))
            path = Path(item["path"])
            kind = item["kind"]
            if body.get("expected_hash") != item["hash"]:
                raise LibraryConflict("The library changed outside the editor. Refresh before saving; your draft is kept.")
        value = body["value"]
        if kind == "source":
            if not isinstance(value, str) or not value.strip() or len(value.encode()) > MAX_TEXT:
                raise ValueError("Library text cannot be empty and must be smaller than 2 MB.")
            try:
                data = yaml.safe_load(value)
            except yaml.YAMLError:
                raise ValueError("The source YAML is invalid. Open the source editor to repair it.")
            if not isinstance(data, dict) or not data:
                raise ValueError("Keep at least one style or image template in this group.")
            for name, definition in data.items():
                self.identifier(name)
                self.validate(group, name, definition)
        else:
            self.validate(group, item_id, value)
        if kind in {"style", "image_template", "motion_style"}:
            data = self.mapping(path)
            data[item_id] = value
            content = yaml.dump(data, Dumper=LiteralDumper, allow_unicode=True, sort_keys=False).encode()
        else:
            content = value.encode("utf-8")
        event, _ = self.snapshot(group, item_id, path)
        write_bytes(path, content)
        return {**self.record(event, path), "item": self.item(group, item_id, body.get("file"))}

    def remove(self, body):
        item = self.item(body["group"], body["id"])
        _, kind, root = self.group(body["group"])
        if not item["removable"]:
            raise ValueError("This production template can be edited but not deleted.")
        path = root / body["id"] if kind == "skill" else root
        expected = digest(path) if kind == "skill" else item["hash"]
        # Package edits may include references, so deletion uses a whole-package hash.
        if body.get("expected_hash") != expected:
            raise LibraryConflict("The library changed outside the editor. Refresh before saving; your draft is kept.")
        if kind != "skill" and len(self.mapping(path)) <= 1:
            raise ValueError("Keep at least one style or image template in this group.")
        event, backup = self.snapshot(body["group"], body["id"], path, "package_delete" if kind == "skill" else "file")
        if kind == "skill":
            os.replace(path, backup)
        else:
            data = self.mapping(path)
            del data[body["id"]]
            write_bytes(path, yaml.dump(data, Dumper=LiteralDumper, allow_unicode=True, sort_keys=False).encode())
        return self.record(event, path)

    def import_skill(self, body):
        group = body["group"]
        _, kind, root = self.group(group)
        if kind != "skill":
            raise ValueError("Choose a writing or motion skill group.")
        item_id = self.identifier(body["id"], package=True)
        destination = self.confined(root / item_id)
        if destination.exists():
            raise ValueError("This ID already exists. Choose another ID.")
        source = Path(body["source"]).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".studio-import-", dir=root) as temporary:
            staged = Path(temporary) / "package"
            staged.mkdir()
            if source.is_file() and source.suffix.lower() == ".md":
                shutil.copy2(source, staged / "SKILL.md")
            elif source.is_file() and source.suffix.lower() == ".zip":
                with zipfile.ZipFile(source) as archive:
                    for member in archive.infolist():
                        name = member.filename.replace("\\", "/")
                        relative = PurePosixPath(name)
                        if relative.is_absolute() or ".." in relative.parts or re.match(r"^[a-zA-Z]:", name) or stat.S_ISLNK(member.external_attr >> 16):
                            raise ValueError("The skill archive contains an unsafe path or symbolic link.")
                        for part in relative.parts:
                            self.identifier(part, package=True) if not part.startswith(".") else None
                    members = [m for m in archive.infolist() if not any(p.startswith(".") or p in {"__MACOSX", "node_modules"} for p in PurePosixPath(m.filename.replace("\\", "/")).parts)]
                    if len(members) > 4000 or sum(m.file_size for m in members) > MAX_PACKAGE:
                        raise ValueError("The skill package is too large.")
                    for member in members:
                        name = member.filename.replace("\\", "/")
                        relative = PurePosixPath(name)
                        if relative.is_absolute() or ".." in relative.parts or re.match(r"^[a-zA-Z]:", name) or stat.S_ISLNK(member.external_attr >> 16):
                            raise ValueError("The skill archive contains an unsafe path or symbolic link.")
                        target = staged.joinpath(*relative.parts)
                        if member.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(archive.read(member))
            elif source.is_dir():
                candidates = [p for p in source.rglob("*") if not any(part.startswith(".") or part == "node_modules" for part in p.relative_to(source).parts)]
                if len(candidates) > 4000 or any(p.is_symlink() for p in candidates) or sum(p.stat().st_size for p in candidates if p.is_file()) > MAX_PACKAGE:
                    raise ValueError("Choose a skill package smaller than 128 MB without symbolic links.")
                for candidate in candidates:
                    for part in candidate.relative_to(source).parts:
                        self.identifier(part, package=True)
                    if candidate.is_file():
                        target = staged / candidate.relative_to(source)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(candidate, target)
            else:
                raise ValueError("Choose a SKILL.md file, skill folder, or ZIP package.")
            if not (staged / "SKILL.md").is_file():
                containers = [p for p in staged.iterdir() if p.is_dir()]
                if len(list(staged.iterdir())) == 1 and len(containers) == 1 and (containers[0] / "SKILL.md").is_file():
                    staged = containers[0]
                else:
                    raise ValueError("The imported package must contain SKILL.md at its root.")
            self.validate(group, item_id, self.read_text(staged / "SKILL.md"))
            event, _ = self.snapshot(group, item_id, destination, "package_create")
            os.replace(staged, destination)
        return {**self.record(event, destination), "item": self.item(group, item_id)}

    def history(self):
        events = []
        for path in self.history_root.glob("*/event.json"):
            try:
                event = json.loads(self.confined(path).read_text())
                target = self.event_target(event)
            except (ValueError, KeyError, OSError):
                continue
            if not event.get("restored"):
                events.append({**event, "can_restore": digest(target) == event["after_hash"]})
        return sorted(events, key=lambda item: item["time"], reverse=True)[:60]

    def restore(self, body):
        event_id = body["revision"]
        if not re.fullmatch(r"[0-9a-f]{32}", event_id):
            raise ValueError("Choose a library revision.")
        event_path = self.confined(self.history_root / event_id / "event.json")
        event = json.loads(event_path.read_text())
        target = self.event_target(event)
        if event.get("restored") or digest(target) != event["after_hash"]:
            raise LibraryConflict("A newer library edit exists. Restore the newer revision first.")
        backup = self.confined(event_path.parent / "before")
        if event["kind"] == "package_delete":
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, target)
        elif event["kind"] == "package_create":
            os.replace(target, event_path.parent / "removed")
        elif event["before_exists"]:
            write_bytes(target, backup.read_bytes())
        else:
            target.unlink(missing_ok=True)
        event["restored"] = True
        write_bytes(event_path, json.dumps(event, ensure_ascii=False).encode())
        return {"restored": True}

    def event_target(self, event):
        _, kind, root = self.group(event["group"])
        target = self.confined(self.root / event["target"])
        if kind == "skill":
            package = root / self.identifier(event["item"], package=True)
            allowed = target == package if event["kind"] in {"package_create", "package_delete"} else target.is_relative_to(package) and target.suffix.lower() in TEXT_EXTENSIONS
        elif event["group"] == "templates":
            allowed = event["item"] in PROMPTS and target == self.confined(root / event["item"]) and event["kind"] == "file"
        else:
            allowed = target == root and event["kind"] == "file"
        if not allowed:
            raise ValueError("Choose a valid library revision.")
        return target
