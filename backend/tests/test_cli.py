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
