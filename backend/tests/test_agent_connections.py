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
from local_agents import arguments, check, discover, selected


class AgentConnectionTests(unittest.TestCase):
    def test_project_default_and_step_override(self):
        settings = {"agents": {"default": "agy"}, "step5": {"agent_cli": "claude"}}
        self.assertEqual([selected(settings, step) for step in ["1", "4", "5", "6"]],
                         ["agy", "agy", "claude", "agy"])
        self.assertEqual(selected({}, "1"), "claude")
        self.assertEqual(selected({"step1": {"agent_cli": "invalid"}}, "1"), "claude")

    def test_adapters_use_the_task_brief_without_permission_flags(self):
        context = "/project with spaces/context.md"
        agy = {"agent": "agy", "context": context}
        self.assertEqual(arguments(agy)[0], "--prompt-interactive")
        self.assertIn(context, arguments(agy)[1])
        self.assertEqual(arguments(agy, prompt="Do the work", max_turns=10)[2:], ["--output-format", "json"])
        claude = {"agent": "claude", "context": context}
        self.assertEqual(arguments(claude), ["--append-system-prompt-file", context])

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
