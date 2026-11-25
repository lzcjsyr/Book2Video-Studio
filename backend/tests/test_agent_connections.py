"""Local CLI discovery, status, and separation from app production credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_agents import arguments, check, codebuddy_result, discover, selected


class AgentConnectionTests(unittest.TestCase):
    def test_project_default_and_step_override(self):
        settings = {"agents": {"default": "agy"}, "step5": {"agent_cli": "claude"}}
        self.assertEqual([selected(settings, step) for step in ["1", "4", "5", "6"]],
                         ["agy", "agy", "claude", "agy"])
        self.assertEqual(selected({}, "1"), "claude")
        self.assertEqual(selected({"step1": {"agent_cli": "invalid"}}, "1"), "claude")

    def test_codebuddy_can_be_selected_globally_or_per_step(self):
        settings = {"agents": {"default": "codebuddy"}, "step5": {"agent_cli": "agy"}}
        self.assertEqual(selected(settings, "4"), "codebuddy")
        self.assertEqual(selected(settings, "5"), "agy")
        self.assertEqual(selected({"step5": {"agent_cli": "codebuddy"}}, "5"), "codebuddy")

    def test_codebuddy_interactive_and_headless_arguments(self):
        context = str(Path("/project with spaces/context.md"))
        launch = {"agent": "codebuddy", "step": "5", "context": context, "project_dir": "/project with spaces"}
        interactive = arguments(launch)
        self.assertEqual(interactive[0], "--append-system-prompt")
        self.assertIn(context, interactive[1])
        self.assertEqual(interactive[2:], ["--add-dir", launch["project_dir"]])
        automatic = arguments(launch, prompt="Start editing", max_turns=10)
        self.assertEqual(automatic[:len(interactive)], interactive)
        self.assertEqual(automatic[len(interactive):len(interactive) + 6],
                         ["--print", "Start editing", "--output-format", "json", "--max-turns", "10"])
        self.assertIn("Bash(ffmpeg *)", automatic)

    def test_codebuddy_status_uses_the_final_result(self):
        version = subprocess.CompletedProcess([], 0, stdout="2.185.0\n", stderr="")
        success = {"type": "result", "is_error": False, "result": "OK"}
        self.assertEqual(codebuddy_result([{"type": "message"}, success]), success)
        self.assertIsNone(codebuddy_result([success, {"type": "message"}]))
        for code, output, diagnostic, expected in [
            (0, json.dumps([success]), "", True),
            (1, "", "Please log in", False),
            (1, "", "network unavailable", None),
            (0, json.dumps([{"type": "result", "is_error": True}]), "", None),
        ]:
            with self.subTest(code=code, output=output, diagnostic=diagnostic):
                result = subprocess.CompletedProcess([], code, stdout=output, stderr=diagnostic)
                with patch("local_agents.discover", return_value=("/test/codebuddy", {})), \
                     patch("local_agents.subprocess.run", side_effect=[version, result]):
                    self.assertIs(check("codebuddy")["logged_in"], expected)

    def test_adapters_use_the_task_brief_without_permission_flags(self):
        context = str(Path("/project with spaces/context.md"))
        agy = {"agent": "agy", "context": context}
        self.assertEqual(arguments(agy)[0], "--prompt-interactive")
        self.assertIn(context, arguments(agy)[1])
        self.assertEqual(arguments(agy, prompt="Do the work", max_turns=10)[2:], ["--output-format", "json"])
        claude = {"agent": "claude", "context": context}
        self.assertEqual(arguments(claude), ["--append-system-prompt-file", context])

    def test_interactive_assembly_never_sends_a_first_message(self):
        project = Path(tempfile.gettempdir()).resolve() / "a project with spaces"
        cwd = project / "edit"
        context = str(cwd / "context.md")
        launch = {"step": "5", "context": context, "cwd": str(cwd), "project_dir": str(project),
                  "first_message": "legacy kickoff"}
        self.assertEqual(arguments({**launch, "agent": "claude"}), ["--append-system-prompt-file", context])
        self.assertEqual(arguments({**launch, "agent": "agy"}), ["--agent", "grist-assembly", "--add-dir", str(project)])
        automatic = arguments({**launch, "agent": "agy"}, prompt="Start editing", max_turns=10)
        self.assertEqual(automatic[:5], ["--agent", "grist-assembly", "--add-dir", str(project), "--settings"])
        self.assertEqual(json.loads(automatic[5])["trustedWorkspaces"], [str(cwd), str(project)])
        self.assertEqual(automatic[6:], ["--print", "Start editing", "--output-format", "json"])

    def test_auth_reports_signed_out_and_missing_installation_separately(self):
        version = subprocess.CompletedProcess([], 0, stdout="2.0\n", stderr="")
        signed_out = subprocess.CompletedProcess([], 1, stdout='{"loggedIn": false}', stderr="")
        with patch("local_agents.discover", return_value=("/test/claude", {})), \
             patch("local_agents.subprocess.run", side_effect=[version, signed_out]):
            result = check("claude")
        self.assertTrue(result["installed"])
        self.assertIs(result["logged_in"], False)
        with patch("local_agents.discover", side_effect=ValueError("not installed")):
            missing = check("claude")
        self.assertFalse(missing["installed"])
        self.assertIsNone(missing["logged_in"])

    def test_agy_checks_for_an_authenticated_model_catalog(self):
        version = subprocess.CompletedProcess([], 0, stdout="1.5.15\n", stderr="")
        for code, output, diagnostic, expected in [
            (0, 'example\tExample model\n', "", True),
            (1, "", "Please sign in", False),
            (1, "", "network unavailable", None),
            (0, 'login required', "", False),
        ]:
            with self.subTest(code=code, output=output, diagnostic=diagnostic):
                result = subprocess.CompletedProcess([], code, stdout=output, stderr=diagnostic)
                with patch("local_agents.discover", return_value=("/test/agy", {})), \
                     patch("local_agents.subprocess.run", side_effect=[version, result]):
                    self.assertIs(check("agy")["logged_in"], expected)

    def test_worker_agent_environment_keeps_local_configuration_only(self):
        import local_agents
        backend = Path(local_agents.__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "ANTHROPIC_BASE_URL": "https://local-config.example"}
            code = (
                "import os, local_agents; "
                "os.environ['DEEPSEEK_API_KEY'] = 'studio-only-key'; "
                "os.environ['ANTHROPIC_BASE_URL'] = 'studio-only-url'; "
                "from unittest.mock import patch; "
                "p = patch('local_agents.shutil.which', return_value='/fake/agy').start(); "
                "program, env = local_agents.discover('agy'); "
                "assert env.get('DEEPSEEK_API_KEY') != 'studio-only-key'; "
                "assert env['ANTHROPIC_BASE_URL'] == 'https://local-config.example'"
            )
            result = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, " + repr(str(backend)) + "); " + code],
                                    cwd=directory, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
