"""Contract tests through the loopback service and the public CLI entry point."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import quote

BACKEND = Path(__file__).resolve().parents[1]
ENGINE = BACKEND.parent / "build/engine"
sys.path.insert(0, str(BACKEND))
from service import discover, request  # noqa: E402


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        cls.data = root / "data"
        cls.workspace = root / "projects"
        cls.env = {**os.environ, "GRIST_RESOLVED_DATA_DIR": str(cls.data)}
        cls.env.pop("STUDIO_TOKEN", None)
        if os.name != "nt":
            directory = root / "bin"
            directory.mkdir()
            fixture = directory / "writer.py"
            fixture.write_text(
                "import json, sys\nfrom pathlib import Path\n"
                "context = Path(sys.argv[sys.argv.index('--append-system-prompt-file') + 1])\n"
                "launch = json.loads(context.with_name('launch.json').read_text())\n"
                "raw = json.loads(sys.argv[1])\n"
                "if 'INVALID_TEST_MANUSCRIPT' in context.read_text(): raw = {'content': 42}\n"
                "Path(launch['manuscript']).write_text(json.dumps(raw))\n"
                "print(json.dumps({'is_error': False}))\n",
                encoding="utf-8")
            raw = {"source_name": "Test", "content": "New manuscript.", "video_titles": ["Test"],
                   "cover_titles": ["Test"], "cover_subtitles": [], "golden_quotes": []}
            fake = directory / "claude"
            fake.write_text("#!/bin/sh\nexec " + " ".join(shlex.quote(str(arg)) for arg in
                            [sys.executable, fixture, json.dumps(raw)]) + ' "$@"\n', encoding="utf-8")
            fake.chmod(0o700)
            cls.env["PATH"] = str(directory) + os.pathsep + cls.env.get("PATH", "")
        command = [sys.executable, str(BACKEND / "service.py"), "--engine", str(ENGINE),
                   "--data", str(cls.data), "--workspace", str(cls.workspace)]

        def start(_):
            launched = subprocess.run(command, input="{}", capture_output=True, text=True, env=cls.env, timeout=35)
            if launched.returncode:
                raise RuntimeError(launched.stderr)
            return json.loads(launched.stdout)

        with ThreadPoolExecutor() as pool:
            started = list(pool.map(start, range(3)))
        cls.endpoint = started[0]
        cls.addClassCleanup(cls.finish_service)
        if not all(info == cls.endpoint for info in started):
            raise AssertionError("Concurrent launchers started different servers.")

    @classmethod
    def finish_service(cls):
        try:
            request(cls.endpoint, "/api/service/stop", b"{}")
        except OSError:
            pass
        for _ in range(60):
            if not (cls.data / "service.json").exists():
                break
            time.sleep(0.05)

    def cli(self, *arguments, body=None, code=0):
        result = subprocess.run([sys.executable, str(BACKEND / "cli.py"), *arguments],
                                input=json.dumps(body) if body is not None else None,
                                env=self.env, capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, code, result.stderr or result.stdout)
        return json.loads(result.stdout or result.stderr)

    def test_endpoint_is_private_and_requires_token(self):
        if os.name != "nt":
            self.assertEqual((self.data / "service.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(discover(self.data), self.endpoint)
        with self.assertRaises(HTTPError) as failure:
            request({**self.endpoint, "token": "incorrect-test-token"}, "/api/service")
        self.assertEqual(failure.exception.code, 401)
        failure.exception.close()

    def test_schema_reports_dynamic_style_and_agent_choices(self):
        visuals = self.cli("schema", "visuals", "--json")
        settings = visuals["settings"]["step4"]
        self.assertIn("style02", settings["image_style_preset"]["choices"])
        self.assertEqual(settings["image_style_preset"]["library_group"], "image_styles")
        self.assertIn("codex", settings["agent_cli"]["choices"])
        full = self.cli("schema")
        self.assertEqual(set(full["steps"]), set("123456"))
        for step in full["steps"].values():
            self.assertEqual(set(step["modes"]), set(step["mode_details"]))
        self.assertEqual(set(visuals["steps"]["4"]["job_input_details"]),
                         set(visuals["steps"]["4"]["job_inputs"]) - {"mode"})
        for fields in full["settings"].values():
            self.assertTrue(all("description" in field for field in fields.values()))
        self.assertEqual(full["settings"]["step6"]["cover_image_model"]["recommended_by_provider"]["doubao"][0],
                         "doubao-seedream-5-5-261028")

    def test_project_and_run_overrides_have_distinct_scope(self):
        created = self.cli("project", "create", "CLI test 项目", "--params", "-", body={
            "global": {"opening_quote": False}, "step2": {"split_mode": "manual"},
            "agents": {"default": "codex"}, "step4": {"agent_cli": "claude"},
        })
        id = created["id"]
        other = self.cli("project", "create", "Other CLI project")["id"]
        self.assertNotEqual(self.cli("config", other)["agents"]["4"], "claude")
        self.assertEqual(self.cli("config", id)["agents"]["4"], "claude")
        bad = self.cli("config", id, "--set", "-", body={"step4": {"imag_style": "style02"}}, code=1)
        self.assertIn("imag_style", bad["error"])
        self.cli("writing", "import", id, "--file", "-", body={
            "source_name": "Title", "content": "One. Two.\nThree.",
            "video_titles": ["Video"], "cover_titles": ["Cover"], "cover_subtitles": [], "golden_quotes": [],
        })
        job = self.cli("step", "run", id, "segments", "--params", "-", "--wait",
                       body={"step2": {"split_mode": "punctuation"}})
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["configuration"]["settings"]["step2"]["split_mode"], "punctuation")
        self.assertEqual(self.cli("job", "status", job["id"])["status"], "succeeded")
        self.assertEqual(len(self.cli("content", id, "script")["value"]["segments"]), 3)
        self.assertEqual(self.cli("config", id)["settings"]["step2"]["split_mode"], "manual")
        self.cli("step", "run", id, "segments", "--wait")
        self.assertEqual(len(self.cli("content", id, "script")["value"]["segments"]), 2)
        self.assertEqual(self.cli("config", id, "--unset", "step4.agent_cli")["agents"]["4"], "codex")

    def test_library_previews_describe_skills_and_resolve_motion_references(self):
        group = self.cli("library", "writing_skills")
        skill = next(item for item in group["items"] if item["id"] == "book-explainer")
        self.assertIn("口播稿", skill["description"])
        self.assertTrue(Path(skill["path"]).is_absolute())
        preview = self.cli("library", group["id"], skill["id"])
        self.assertEqual(preview["path"], skill["path"])
        self.assertEqual(preview["value"], Path(skill["path"]).read_text(encoding="utf-8"))
        file = self.cli("library", group["id"], skill["id"], "--file", "references/output-format.md")
        self.assertEqual(file["value"], Path(file["path"]).read_text(encoding="utf-8"))
        self.assertIn("skill group", self.cli("library", group["id"], "--file", "SKILL.md", code=1)["error"])
        self.cli("library", "image_styles", "style02", "--file", "SKILL.md", code=1)
        self.cli("library", group["id"], skill["id"], "--file", "../SKILL.md", code=1)

        custom = Path(group["path"]) / "自定义 skill"
        custom.mkdir()
        (custom / "SKILL.md").write_text("---\ndescription: [invalid\n---\n# Custom\n", encoding="utf-8")
        custom_item = next(item for item in self.cli("library", group["id"])["items"] if item["id"] == custom.name)
        self.assertEqual(custom_item["description"], "")
        self.assertEqual(self.cli("library", group["id"], custom.name)["path"], str(custom / "SKILL.md"))

        style = self.cli("library", "motion_styles", "data_driven")
        self.assertEqual(style["value"]["palette"], "dark-premium")
        self.assertEqual(style["references"][0]["section"], "Swiss Pulse")
        self.assertTrue(all(Path(ref["path"]).is_absolute() and Path(ref["path"]).is_file()
                            for ref in style["references"]))
        for name in ("image_styles", "cover_styles"):
            item = self.cli("library", name)["items"][0]
            self.assertEqual(self.cli("library", name, item["id"])["value"], item["description"])

    def test_absolute_asset_paths_keep_ids_and_allow_cover_selection(self):
        location = self.workspace.parent / "外部 项目"
        location.mkdir()
        created = self.cli("project", "create", "Absolute assets", "--location", str(location))
        id, project = created["id"], Path(created["path"])
        empty = self.cli("assets", "list", id, "--absolute")
        self.assertEqual(empty["text"], dict.fromkeys(("raw", "script", "summary")))
        self.assertIsNone(empty["voice_reference_path"])
        self.assertIsNone(empty["selected_cover"])

        self.cli("writing", "import", id, "--file", "-", body={
            "source_name": "Paths", "content": "One.",
            "video_titles": ["Video"], "cover_titles": ["Cover"], "cover_subtitles": [], "golden_quotes": [],
        })
        self.cli("step", "run", id, "segments", "--wait")
        take_id = "motion-20261001-000000-000-abcd"
        motion = project / "images/takes/segment_1" / take_id
        frame = motion / "snapshots/frame-001.png"
        frame.parent.mkdir(parents=True)
        for artifact in (frame, motion / "index.html", motion / "video.mp4", project / "final_video.mp4",
                         project / "cover_a.png", project / "cover_b.png"):
            artifact.touch()
        (project / "cover_folder.jpg").mkdir()
        (project / "final_video_3x4.mp4").mkdir()
        self.cli("assets", "take", id, "1", take_id)
        selected = project / "cover_b.png"
        self.cli("assets", "cover", id, str(selected))
        self.cli("assets", "cover", id, str(location / selected.name), code=1)

        assets = self.cli("assets", "list", id, "--absolute")
        self.assertEqual(assets["path"], str(project))
        self.assertEqual(assets["exports"], [str(project / "final_video.mp4")])
        self.assertEqual(assets["selected_cover"], str(selected))
        self.assertEqual(assets["text"], {"raw": str(project / "text/raw.json"),
                         "script": str(project / "text/script.json"), "summary": None})
        [segment] = assets["segments"]
        self.assertEqual(segment["take"], take_id)
        self.assertEqual(segment["composition"], str(motion / "index.html"))
        self.assertEqual(segment["motion"]["keyframes"], [str(frame)])
        self.assertEqual(segment["takes"][0]["path"], str(motion / "video.mp4"))
        relative = self.cli("assets", "list", id)
        self.assertEqual(relative["selected_cover"], selected.name)
        self.assertEqual(relative["segments"][0]["poster"], frame.relative_to(project).as_posix())

    def test_visual_modes_default_scope_and_reject_conflicting_inputs(self):
        id = self.cli("project", "create", "Visual jobs")["id"]
        no_script = self.cli("step", "run", id, "visuals", "--mode", "segments", code=1)
        self.assertIn("Generate segments", no_script["error"])
        conflict = self.cli("step", "run", id, "visuals", "--mode", "summary", "--batch", "all", code=1)
        self.assertIn("batch only", conflict["error"])
        take = self.cli("step", "run", id, "visuals", "--job", "-", body={
            "mode": "render", "take": "motion-example", "indices": [1, 2],
        }, code=1)
        self.assertIn("exactly one", take["error"])

    def test_agent_revision_inputs_reject_incompatible_modes_and_missing_source(self):
        id = self.cli("project", "create", "Revision inputs")["id"]
        for step, other_modes, missing in (
            ("assembly", ("standard", "three_by_four"), "Agent assembly"),
            ("covers", ("api", "cover_render"), "Agent HTML cover design"),
        ):
            with self.subTest(step=step):
                schema = self.cli("schema", step)["steps"]
                self.assertEqual(next(iter(schema.values()))["job_inputs"]["instructions"], "string")
                for mode in other_modes:
                    error = self.cli("step", "run", id, step, "--mode", mode,
                                     "--instructions", "Keep the title", code=1)
                    self.assertIn("Use Agent mode", error["error"])
                error = self.cli("step", "run", id, step, "--missing", "--instructions", "Keep the title", code=1)
                self.assertIn("without --missing", error["error"])
                error = self.cli("step", "run", id, step, "--mode", "agent", "--revise",
                                 "--instructions", "Keep the title", code=1)
                self.assertIn(missing, error["error"])

    def test_plan_can_include_writing_and_reject_a_missing_brief(self):
        id = self.cli("project", "create", "CLI writing plan", "--params", "-",
                      body={"step1": {"writing_mode": "direct"}, "global": {"opening_quote": True}})["id"]
        blocked = self.cli("production", "plan", id, "--steps", "1,2,3,4,5,6", code=1)
        self.assertIn("instructions", blocked["blocked"])
        plan = self.cli("production", "plan", id, "--steps", "writing,segments,covers",
                        "--instructions", "Write about tea")
        self.assertIsNone(plan["blocked"])
        self.assertEqual([step["step"] for step in plan["steps"] if step["run"]], ["1", "2", "6"])
        status = self.cli("project", "status", id)
        self.assertFalse(status["ready"]["1"])
        route = "/api/projects/" + quote(id, safe="")
        request(self.endpoint, route + "/opening", b'{"enabled":false}')
        self.assertFalse(self.cli("config", id)["settings"]["global"]["opening_quote"])
        self.assertEqual(status["configuration"]["settings"]["step1"]["writing_mode"], "direct")


    @unittest.skipIf(os.name == "nt", "The fixture executable is a Unix launcher.")
    def test_headless_writer_only_publishes_valid_manuscripts(self):
        id = self.cli("project", "create", "CLI writing delivery")["id"]
        params = {"step1": {"agent_skill": ""}}
        done = self.cli("step", "run", id, "writing", "--agent", "claude", "--params", "-",
                        "--instructions", "Write the fixture manuscript.", "--wait", body=params)
        self.assertEqual(done["result"]["artifact"], "text/raw.json")
        manuscript = self.cli("content", id, "raw")
        self.assertEqual(manuscript["value"]["content"], "New manuscript.")
        self.assertEqual(manuscript["value"]["total_length"], len("New manuscript."))
        failed = self.cli("step", "run", id, "writing", "--agent", "claude", "--params", "-",
                          "--instructions", "INVALID_TEST_MANUSCRIPT", "--wait", body=params, code=1)
        self.assertIn("valid manuscript", failed["result"]["message"])
        self.assertEqual(self.cli("content", id, "raw"), manuscript)
