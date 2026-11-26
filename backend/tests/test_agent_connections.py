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
from local_agents import arguments, check, codebuddy_result, codex_result, discover, selected


class AgentConnectionTests(unittest.TestCase):
    def test_project_default_and_step_override(self):
        settings = {"agents": {"default": "agy"}, "step5": {"agent_cli": "claude"}}
        self.assertEqual([selected(settings, step) for step in ["1", "4", "5", "6"]],
                         ["agy", "agy", "claude", "agy"])
        self.assertEqual(selected({}, "1"), "agy")
        self.assertEqual(selected({"step1": {"agent_cli": "invalid"}}, "1"), "agy")

    def test_codebuddy_can_be_selected_globally_or_per_step(self):
        settings = {"agents": {"default": "codebuddy"}, "step5": {"agent_cli": "agy"}}
        self.assertEqual(selected(settings, "4"), "codebuddy")
        self.assertEqual(selected(settings, "5"), "agy")
        self.assertEqual(selected({"step5": {"agent_cli": "codebuddy"}}, "5"), "codebuddy")

    def test_codex_can_be_selected_globally_or_per_step(self):
        settings = {"agents": {"default": "codex"}, "step5": {"agent_cli": "agy"}}
        self.assertEqual(selected(settings, "4"), "codex")
        self.assertEqual(selected(settings, "5"), "agy")
        self.assertEqual(selected({"step5": {"agent_cli": "codex"}}, "5"), "codex")

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

    def test_codex_status_reads_the_login_report(self):
        version = subprocess.CompletedProcess([], 0, stdout="codex-cli 0.172.0\n", stderr="")
        for code, output, diagnostic, expected in [
            (0, "", "Logged in using ChatGPT\n", True),
            (1, "", "Not logged in. Run `codex login`.", False),
            (1, "", "network unavailable", None),
        ]:
            with self.subTest(code=code, output=output, diagnostic=diagnostic):
                result = subprocess.CompletedProcess([], code, stdout=output, stderr=diagnostic)
                with patch("local_agents.discover", return_value=("/test/codex", {})), \
                     patch("local_agents.subprocess.run", side_effect=[version, result]):
                    self.assertIs(check("codex")["logged_in"], expected)

    def test_codex_session_finishes_with_a_completed_turn(self):
        finished = "\n".join([
            '{"type":"thread.started","thread_id":"t"}',
            '{"type":"item.completed","item":{"type":"error","message":"deprecation warning"}}',
            '{"type":"turn.started"}',
            '{"type":"turn.completed","usage":{"output_tokens":5}}',
        ]) + "\n"
        self.assertEqual(codex_result(finished), {"type": "turn.completed", "usage": {"output_tokens": 5}})
        self.assertIsNone(codex_result('{"type":"turn.started"}'))
        self.assertIsNone(codex_result(""))

    def test_codex_reads_the_task_brief_then_waits_or_runs_headless(self):
        context = "/project with spaces/context.md"
        manual = {"agent": "codex", "context": context, "cwd": str(Path(context).parent)}
        manual_args = arguments(manual)
        self.assertIn("--approve-for-me", manual_args)
        self.assertIn(f'projects.{json.dumps(str(Path(manual["cwd"]).resolve()))}.trust_level="trusted"', manual_args)
        self.assertEqual(manual_args[0], "-c")
        key, value = manual_args[1].split("=", 1)
        self.assertEqual(key, "developer_instructions")
        self.assertIn(f'On the first user request, read the Grist task brief at "{context}"', json.loads(value))
        kickoff = "请根据要求开始工作。"
        automatic = arguments(manual, prompt=kickoff, max_turns=10)
        self.assertEqual(automatic[:2], ["exec", "--json"])
        self.assertIn("--skip-git-repo-check", automatic)
        self.assertIn("--sandbox", automatic)
        self.assertTrue(automatic[-1].endswith(kickoff))
        project = Path(tempfile.gettempdir()).resolve() / "a project with spaces"
        production = {"agent": "codex", "context": context, "step": "5", "cwd": str(project), "project_dir": str(project)}
        self.assertEqual(arguments(production)[-2:], ["--add-dir", str(project)])
        production_args = arguments(production, prompt=kickoff, max_turns=10)
        self.assertIn("--add-dir", production_args)
        self.assertIn(str(project), production_args)

    def test_manual_adapters_use_auto_mode_and_the_task_brief(self):
        context = "/project with spaces/context.md"
        agy = {"agent": "agy", "context": context, "cwd": str(Path(context).parent)}
        self.assertEqual(arguments(agy)[0], "--prompt-interactive")
        self.assertIn(context, arguments(agy)[1])
        self.assertEqual(arguments(agy)[2:4], ["--mode", "auto"])
        self.assertEqual(json.loads(arguments(agy)[-1])["trustedWorkspaces"], [agy["cwd"]])
        self.assertEqual(arguments(agy, prompt="Do the work", max_turns=10)[2:], ["--output-format", "json"])
        claude = {"agent": "claude", "context": context}
        self.assertEqual(arguments(claude), ["--append-system-prompt-file", context, "--permission-mode", "auto"])

    def test_production_templates_are_system_prompts_with_no_interactive_kickoff(self):
        project = Path(tempfile.gettempdir()).resolve() / "a project with spaces"
        context = str(project / "context.md")
        for step, agent_name in [("5", "grist-assembly"), ("6", "grist-covers")]:
            cwd = project / agent_name
            launch = {"step": step, "context": context, "cwd": str(cwd), "first_message": "legacy kickoff"}
            project_args = ["--add-dir", str(project)] if step == "5" else []
            if project_args:
                launch["project_dir"] = str(project)
            for agent, system_args in [
                ("claude", ["--append-system-prompt-file", context]),
                ("codebuddy", ["--system-prompt-file", context, *project_args]),
                ("agy", ["--agent", agent_name, *project_args]),
            ]:
                with self.subTest(step=step, agent=agent):
                    session = {**launch, "agent": agent}
                    manual = arguments(session)
                    self.assertEqual(manual[:len(system_args)], system_args)
                    if agent == "agy":
                        self.assertEqual(manual[-4:-1], ["--mode", "auto", "--settings"])
                        self.assertEqual(json.loads(manual[-1])["trustedWorkspaces"], [str(cwd), *project_args[1:]])
                    else:
                        self.assertEqual(manual[manual.index("--permission-mode") + 1], "auto")
                        if agent == "codebuddy":
                            self.assertEqual(json.loads(manual[-1])["trustedDirectories"], [str(cwd), *project_args[1:]])
                    kickoff = "请根据要求开始工作。"
                    automatic = arguments(session, prompt=kickoff, max_turns=10)
                    self.assertEqual(automatic[automatic.index("--print") + 1], kickoff)
                    if agent == "agy":
                        settings = json.loads(automatic[automatic.index("--settings") + 1])
                        self.assertEqual(settings["trustedWorkspaces"], [str(cwd), *project_args[1:]])
                        self.assertEqual(settings["toolPermission"], "always-proceed")
                    else:
                        if agent == "codebuddy":
                            self.assertEqual(automatic[automatic.index("--tool-mode") + 1], "cli")
                        self.assertEqual(automatic[automatic.index("--permission-mode") + 1], "acceptEdits")
                        allowed = ",".join(automatic[automatic.index("--allowedTools") + 1:])
                        self.assertIn("Bash(python *)", allowed)
                        self.assertEqual("Bash(ffmpeg *)" in allowed, step == "5")

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
