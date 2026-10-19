import http.client
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from library import Library, LibraryConflict, digest, install_bundled
from server import Studio, Handler, atomic_json

DESKTOP = Path(__file__).resolve().parents[2]
# The engine as the app ships it (npm run stage).
ENGINE = DESKTOP / "build/engine"


@unittest.skipUnless((ENGINE / "prompts/step4_styles.yaml").is_file(), "The CLI resource fixtures are required")
class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Path(self.temp.name) / "测试 engine"
        shutil.copytree(ENGINE / "prompts", self.engine / "prompts")
        shutil.copytree(ENGINE / "skills/step4", self.engine / "skills/step4")
        writing = self.engine / "skills/step1/test_writer"
        (writing / "references").mkdir(parents=True)
        (writing / "SKILL.md").write_text("# Test writing skill\nRead references/guide.md.")
        (writing / "references/guide.md").write_text("Original guide")
        self.library = Library(self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, group, item_id, value, file=None):
        item = self.library.item(group, item_id, file)
        return self.library.save({"group": group, "id": item_id, "value": value,
                                  "file": file, "expected_hash": item["hash"]})

    def create(self, group, item_id, value):
        return self.library.save({"group": group, "id": item_id, "value": value,
                                  "expected_hash": digest(self.library.group(group)[2])}, create=True)

    def test_edits_write_real_files_and_history_restores_them(self):
        for group, item_id, file in [("summary", "summary", None), ("writing_skills", "test_writer", None),
                                    ("writing_skills", "test_writer", "references/guide.md")]:
            with self.subTest(group=group, file=file):
                before = self.library.item(group, item_id, file)
                result = self.save(group, item_id, "Edited instructions", file)
                self.assertEqual(Path(result["item"]["path"]).read_text(), "Edited instructions")
                self.library.restore({"revision": result["revision"]})
                self.assertEqual(Path(before["path"]).read_text(), before["value"])

    def test_external_edits_are_not_overwritten(self):
        item = self.library.item("summary", "summary")
        Path(item["path"]).write_text("External edit")
        with self.assertRaises(LibraryConflict):
            self.library.save({"group": "summary", "id": "summary", "value": "Editor draft", "expected_hash": item["hash"]})
        self.assertEqual(Path(item["path"]).read_text(), "External edit")

    def test_image_style_catalog_contains_the_full_description(self):
        text = "Keep this complete style description available for comparison. " * 5
        self.create("image_styles", "long_style", text)
        group = next(group for group in self.library.catalog()["groups"] if group["id"] == "image_styles")
        choice = next(choice for choice in group["items"] if choice["id"] == "long_style")
        self.assertEqual(choice["description"], text)

    def test_library_http_requires_authentication_and_reports_conflicts(self):
        (self.engine / "core/pipeline").mkdir(parents=True)
        (self.engine / "core/pipeline/steps.py").write_text("")
        shutil.copy2(ENGINE / "config.example.yaml", self.engine / "config.example.yaml")
        service = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        service.studio = Studio(self.engine, Path(self.temp.name) / "data", Path(self.temp.name) / "projects"); service.token = "test-token"
        threading.Thread(target=service.serve_forever, daemon=True).start()
        connection = http.client.HTTPConnection("127.0.0.1", service.server_port)
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        try:
            connection.request("GET", "/api/library")
            response = connection.getresponse(); self.assertEqual(response.status, 401); response.read()
            connection.request("GET", "/api/bootstrap", headers=headers)
            response = connection.getresponse(); self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())["library"]["groups"])
            connection.request("GET", "/api/library/item?" + urlencode({"group":"summary", "id":"summary"}), headers=headers)
            response = connection.getresponse(); self.assertEqual(response.status, 200)
            item = json.loads(response.read())
            body = {"group":"summary", "id":"summary", "value":"HTTP editor instructions", "expected_hash":item["hash"]}
            connection.request("POST", "/api/library/save", json.dumps(body), headers)
            response = connection.getresponse(); self.assertEqual(response.status, 200); saved = json.loads(response.read())
            self.assertEqual(Path(item["path"]).read_text(), body["value"])
            connection.request("POST", "/api/library/save", json.dumps(body), headers)
            response = connection.getresponse(); self.assertEqual(response.status, 409); response.read()
            connection.request("POST", "/api/library/restore", json.dumps({"revision":saved["revision"]}), headers)
            response = connection.getresponse(); self.assertEqual(response.status, 200); response.read()
            self.assertEqual(Path(item["path"]).read_text(), item["value"])
        finally:
            connection.close(); service.shutdown(); service.server_close()

    def test_add_edit_remove_and_restore_styles(self):
        for group in ["image_styles", "cover_styles"]:
            with self.subTest(group=group):
                before = self.library.mapping(self.library.group(group)[2])
                added = self.create(group, "custom_style", "我的自定义风格")
                self.assertEqual(added["item"]["value"], "我的自定义风格")
                edited = self.save(group, "custom_style", "Changed style")
                removed = self.library.remove({"group": group, "id": "custom_style", "expected_hash": edited["item"]["hash"]})
                self.assertEqual(self.library.mapping(self.library.group(group)[2]), before)
                self.library.restore({"revision": removed["revision"]})
                self.assertEqual(self.library.item(group, "custom_style")["value"], "Changed style")
                self.library.restore({"revision": edited["revision"]})
                self.library.restore({"revision": added["revision"]})
                self.assertEqual(self.library.mapping(self.library.group(group)[2]), before)

    def test_cover_style_descriptions_are_complete_before_selection(self):
        description = "Read the complete composition and typography guidance.\n" * 5 + "Visible final detail."
        self.create("cover_styles", "described_cover", description)
        group = next(group for group in self.library.catalog()["groups"] if group["id"] == "cover_styles")
        choice = next(choice for choice in group["items"] if choice["id"] == "described_cover")
        self.assertEqual(choice["description"], description)

    def test_template_variables_and_last_style_are_protected(self):
        with self.assertRaisesRegex(ValueError, "variables"):
            self.create("image_templates", "custom", {"label": "Custom", "description": "Custom", "template": "{summary} {style_block}"})
        with self.assertRaisesRegex(ValueError, "variables"):
            self.save("templates", "step6_cover.md", "Forgot variables")
        with self.assertRaisesRegex(ValueError, "variables"):
            self.save("templates", "step1_5_segment.md", "Forgot paths")
        path = self.library.group("image_styles")[2]
        path.write_text("only: Remaining style\n")
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.library.remove({"group": "image_styles", "id": "only", "expected_hash": digest(path)})

    def test_malformed_external_yaml_can_be_repaired_inside_editor(self):
        path = self.library.group("image_templates")[2]
        path.write_text("broken: [\n")
        group = next(g for g in self.library.catalog()["groups"] if g["id"] == "image_templates")
        self.assertTrue(group["error"])
        self.assertEqual(group["items"], [])
        original = (ENGINE / "prompts/step4_image_templates.yaml").read_text()
        self.save("image_templates", "__source__", original)
        self.assertFalse(next(g for g in self.library.catalog()["groups"] if g["id"] == "image_templates")["error"])
        with self.assertRaises(ValueError):
            self.save("image_templates", "__source__", "broken: [\n")
        self.assertEqual(path.read_text(), original)
        path.write_text("broken: !!binary YmluYXJ5\n")
        self.assertTrue(next(g for g in self.library.catalog()["groups"] if g["id"] == "image_templates")["error"])

    def test_import_preserves_package_files_and_can_be_undone(self):
        source = Path(self.temp.name) / "import source"
        (source / "references").mkdir(parents=True)
        (source / "assets").mkdir()
        (source / "SKILL.md").write_text("Imported writing instructions")
        (source / "references/guide.md").write_text("Imported guide")
        (source / "assets/image.bin").write_bytes(b"asset")
        for mode in ["folder", "zip", "md"]:
            selected = source
            if mode == "zip":
                selected = Path(self.temp.name) / "skill.zip"
                with zipfile.ZipFile(selected, "w") as archive:
                    for file in source.rglob("*"):
                        if file.is_file(): archive.write(file, "nested/" + file.relative_to(source).as_posix())
            elif mode == "md": selected = source / "SKILL.md"
            body = {"group": "writing_skills", "id": "导入_" + mode, "source": str(selected)}
            result = self.library.import_skill(body)
            package = Path(result["item"]["path"]).parent
            self.assertEqual((package / "SKILL.md").read_text(), "Imported writing instructions")
            if mode != "md": self.assertEqual((package / "assets/image.bin").read_bytes(), b"asset")
            with self.assertRaisesRegex(ValueError, "already exists"): self.library.import_skill(body)
            self.library.restore({"revision": result["revision"]})
            self.assertFalse(package.exists())

    def test_delete_package_checks_references_and_restores_folder(self):
        item = self.library.item("writing_skills", "test_writer")
        self.save("writing_skills", "test_writer", "New guide", "references/guide.md")
        with self.assertRaises(LibraryConflict):
            self.library.remove({"group": "writing_skills", "id": "test_writer", "expected_hash": item["package_hash"]})
        current = self.library.item("writing_skills", "test_writer")
        deleted = self.library.remove({"group": "writing_skills", "id": "test_writer", "expected_hash": current["package_hash"]})
        self.assertFalse(Path(current["path"]).exists())
        self.library.restore({"revision": deleted["revision"]})
        self.assertEqual(self.library.item("writing_skills", "test_writer", "references/guide.md")["value"], "New guide")

    def test_archive_traversal_symlink_and_incompatible_names_are_rejected(self):
        for name in ["../escape", "/tmp/escape", "C:\\escape", "CON/file.md"]:
            archive_path = Path(self.temp.name) / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("SKILL.md", "Instructions")
                archive.writestr(name, "unsafe")
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.library.import_skill({"group": "writing_skills", "id": "unsafe", "source": str(archive_path)})
        archive_path = Path(self.temp.name) / "symlink.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            entry = zipfile.ZipInfo("link.md"); entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "outside.md")
        with self.assertRaises(ValueError):
            self.library.import_skill({"group": "writing_skills", "id": "unsafe", "source": str(archive_path)})
        for item_id in ["CON", "a/b", "trailing.", "a:b", ".."]:
            with self.assertRaises(ValueError): self.library.identifier(item_id, package=True)
        with self.assertRaises(ValueError): self.library.item("writing_skills", "test_writer", "../../other.md")

    def test_history_cannot_target_files_outside_the_library(self):
        event_dir = self.engine / ".studio-library" / ("a" * 32)
        event_dir.mkdir(parents=True)
        outside = self.engine / "unrelated.txt"; outside.write_text("Preserve")
        atomic_json(event_dir / "event.json", {"group": "summary", "item": "summary", "target": "unrelated.txt", "kind": "file"})
        self.assertFalse(self.library.history())
        with self.assertRaises(ValueError): self.library.restore({"revision": "a" * 32})
        self.assertEqual(outside.read_text(), "Preserve")

    def test_new_motion_style_and_reference_validation(self):
        value = {"label": "Custom motion", "official_style": "Swiss Pulse", "palette": "dark-premium", "best_for": "Charts", "references": []}
        self.create("motion_styles", "custom_motion", value)
        value["references"] = ["../../unrelated.md"]
        with self.assertRaises(ValueError): self.save("motion_styles", "custom_motion", value)

    def test_removing_keywords_keeps_cli_template_loader_usable(self):
        item = self.library.item("image_templates", "keywords")
        self.library.remove({"group": "image_templates", "id": "keywords", "expected_hash": item["hash"]})
        # The engine reads this library the way the app points it there.
        probe = "import sys; sys.path.insert(0, sys.argv[1]); import core.prompts as p; print(p.DEFAULT_STEP4_IMAGE_PROMPT_TEMPLATE)"
        result = subprocess.run([sys.executable, "-c", probe, str(ENGINE)], capture_output=True, text=True, timeout=30,
                                env={**os.environ, "AIGC_VIDEO_LIBRARY_DIR": str(self.engine)})
        self.assertEqual(result.stdout.strip(), "pure_images", result.stderr[-2000:])

    def test_writing_activation_uses_imported_skill_and_edited_template(self):
        from context import prepare
        studio = object.__new__(Studio); studio.engine = studio.library_root = self.engine
        shutil.copy2(ENGINE / "config.example.yaml", self.engine / "config.example.yaml")
        project = self.engine / "output/test"; (project / ".studio").mkdir(parents=True)
        source = project / "source.txt"; source.write_text("Source")
        atomic_json(project / ".studio/state.json", {"settings": {"step1": {"agent_skill": "test_writer"}}, "source": str(source)})
        original = self.library.item("templates", "step1_agent.md")["value"]
        self.save("templates", "step1_agent.md", original + "\nCustom writing requirement")
        launch = prepare(studio, project, {"step": "1"})
        text = Path(launch["context"]).read_text()
        self.assertIn(str(self.engine / "skills/step1/test_writer/SKILL.md"), text)
        self.assertIn("Custom writing requirement", text)

    def test_real_generation_steps_consume_saved_summary_image_and_cover_templates(self):
        # Work in a disposable engine. Intercept provider calls, run the actual CLI stages.
        shutil.copytree(ENGINE / "core", self.engine / "core", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ENGINE / "config.example.yaml", self.engine / "config.example.yaml")
        self.save("summary", "summary", "CUSTOM SUMMARY RULE")
        self.create("image_styles", "custom_image", "CUSTOM IMAGE STYLE")
        self.create("cover_styles", "custom_cover", "CUSTOM COVER STYLE")
        self.create("image_templates", "custom_template", {"label": "Custom", "description": "Custom", "template": "CUSTOM IMAGE TEMPLATE {summary} {segment} {style_block}"})
        self.save("templates", "step6_cover.md", "CUSTOM COVER TEMPLATE {cover_title} {cover_subtitle} {style_block}")
        project = self.engine / "output/test"
        for folder in ["text", "images", ".studio"]: (project / folder).mkdir(parents=True)
        raw = {"source_name": "Test", "content": "Test narration.", "video_titles": ["Title"], "cover_titles": ["Cover"], "cover_subtitles": ["Subtitle"], "golden_quotes": []}
        atomic_json(project / "text/raw.json", raw)
        atomic_json(project / "text/script.json", {**raw, "actual_segments": 1, "segments": [{"index": 1, "content": "Test narration.", "visualizer": "image"}]})
        probe = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core.domain import summarizer
from core.infra.ai import image_client
from PIL import Image
captured = {}
def summary(**kwargs):
    captured['summary'] = kwargs['system_message']
    return json.dumps({'summary':'测试内容的完整小结。'})
def image(args):
    captured['image'] = args[1]
    path = Path(args[4]) / f'segment_{args[0]}.png'
    Image.new('RGB',(32,32)).save(path)
    return {'success':True,'segment_index':args[0],'image_path':str(path)}
def cover(server,model,size,prompt,output,suffix):
    captured['cover'] = prompt
    path = Path(output) / 'cover_test.png'; Image.new('RGB',(32,32)).save(path)
    return {'success':True,'image_path':str(path)}
summarizer.text_to_text = summary
image_client._generate_single_image = image
image_client._generate_single_cover = cover
sys.path.insert(0,sys.argv[2])
from runner import run
settings={'global':{'opening_quote':False},'step4':{'image_style_preset':'custom_image','image_prompt_template':'custom_template'},'step6':{'cover_image_style':'custom_cover','cover_image_count':1}}
# Visuals write their shared direction first, as the service requests when it is missing.
for step in ['4','6']:
    result=run({'engine':sys.argv[1],'project':sys.argv[3],'step':step,'mode':'static','indices':[1],'summary':step=='4','settings':settings})
    assert result['success'], result
assert captured['summary']=='CUSTOM SUMMARY RULE',captured
assert 'CUSTOM IMAGE TEMPLATE' in captured['image'] and 'CUSTOM IMAGE STYLE' in captured['image'],captured
assert 'CUSTOM COVER TEMPLATE' in captured['cover'] and 'CUSTOM COVER STYLE' in captured['cover'],captured
'''
        result = subprocess.run([sys.executable, "-c", probe, str(self.engine), str(DESKTOP / "backend"), str(project)], capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])



# A step 5 template in the current shape: every variable Studio fills in.
BUNDLED_STEP5 = "".join("{" + v + "}" for v in ["style", "skill_path", "materials", "video_size", "narration_speed", "subtitles", "music", "opening", "session", "work_dir", "delivery"])


class BundledLibraryTests(unittest.TestCase):
    """The editable library follows app updates without losing the person's changes."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bundled = Path(self.temp.name) / "bundled"; self.root = Path(self.temp.name) / "library"
        self.write("prompts/step2_summary.md", "v1 summary"); self.write("prompts/step6_cover.md", "v1 cover")
        self.write("skills/step1/writer/SKILL.md", "v1 skill"); self.write("skills/step1/gone/SKILL.md", "v1 gone")
        self.write("skills/step1/writer/__pycache__/x.pyc", "cache")
        install_bundled(self.bundled, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, text):
        path = self.bundled / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)

    def read(self, relative):
        return (self.root / relative).read_text()

    def test_an_edited_template_the_app_cannot_use_moves_to_history(self):
        from library import Library
        self.write("prompts/step5_editing_agent.md", "<!-- section: main -->\nold layout")
        install_bundled(self.bundled, self.root)
        (self.root / "prompts/step5_editing_agent.md").write_text("<!-- section: main -->\nmy old edit")
        self.write("prompts/step5_editing_agent.md", BUNDLED_STEP5)
        install_bundled(self.bundled, self.root)
        self.assertIn("my old edit", self.read("prompts/step5_editing_agent.md"))  # Kept as an edit until it is retired.
        library = Library(self.root)
        self.assertTrue(library.retire_invalid("step5_editing_agent.md", self.bundled / "prompts/step5_editing_agent.md"))
        self.assertEqual(self.read("prompts/step5_editing_agent.md"), BUNDLED_STEP5)
        event = library.history()[0]
        self.assertEqual((event["item"], event["can_restore"]), ("step5_editing_agent.md", True))
        library.restore({"revision": event["id"]})
        self.assertIn("my old edit", self.read("prompts/step5_editing_agent.md"))
        # Nothing to retire once the template is in the current layout.
        (self.root / "prompts/step5_editing_agent.md").write_text(BUNDLED_STEP5 + "my edit")
        self.assertFalse(library.retire_invalid("step5_editing_agent.md", self.bundled / "prompts/step5_editing_agent.md"))

    def test_first_launch_copies_everything_but_caches(self):
        self.assertEqual(self.read("prompts/step2_summary.md"), "v1 summary")
        self.assertEqual(self.read("skills/step1/writer/SKILL.md"), "v1 skill")
        self.assertFalse((self.root / "skills/step1/writer/__pycache__").exists())

    def test_update_keeps_edits_and_removals_and_refreshes_untouched_files(self):
        (self.root / "prompts/step2_summary.md").write_text("my summary")
        (self.root / "prompts/step6_cover.md").unlink()
        shutil.rmtree(self.root / "skills/step1/gone")
        for relative in ["prompts/step2_summary.md", "prompts/step6_cover.md", "skills/step1/writer/SKILL.md", "skills/step1/gone/SKILL.md"]:
            self.write(relative, "v2")
        self.write("skills/step1/gone/references/new.md", "v2 reference"); self.write("prompts/new.md", "v2 new")
        install_bundled(self.bundled, self.root)
        self.assertEqual(self.read("prompts/step2_summary.md"), "my summary")
        self.assertFalse((self.root / "prompts/step6_cover.md").exists())
        self.assertFalse((self.root / "skills/step1/gone").exists())
        self.assertEqual(self.read("skills/step1/writer/SKILL.md"), "v2")
        self.assertEqual(self.read("prompts/new.md"), "v2 new")
        # A file set back to the bundled text follows updates again from the next launch.
        (self.root / "prompts/step2_summary.md").write_text("v2"); install_bundled(self.bundled, self.root)
        self.write("prompts/step2_summary.md", "v3"); install_bundled(self.bundled, self.root)
        self.assertEqual(self.read("prompts/step2_summary.md"), "v3")

    def test_update_drops_files_the_bundle_no_longer_ships_unless_edited(self):
        self.write("skills/step4/old/SKILL.md", "v1 old"); self.write("skills/step4/old/references/a.md", "v1 a")
        self.write("skills/step4/kept/SKILL.md", "v1 kept"); self.write("skills/step4/kept/stale.md", "v1 stale")
        install_bundled(self.bundled, self.root)
        (self.root / "skills/step4/kept/stale.md").write_text("my notes")
        shutil.rmtree(self.bundled / "skills/step4/old"); (self.bundled / "skills/step4/kept/stale.md").unlink()
        install_bundled(self.bundled, self.root)
        self.assertFalse((self.root / "skills/step4/old").exists())
        self.assertTrue((self.root / "skills/step4").is_dir())
        self.assertEqual(self.read("skills/step4/kept/stale.md"), "my notes")
        record = json.loads((self.root / ".bundled.json").read_text())
        self.assertNotIn("skills/step4/kept/stale.md", record)
        # Restoring an upstream package later installs it fresh.
        self.write("skills/step4/old/SKILL.md", "v2 old"); install_bundled(self.bundled, self.root)
        self.assertEqual(self.read("skills/step4/old/SKILL.md"), "v2 old")
