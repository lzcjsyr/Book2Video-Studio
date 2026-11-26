"""Discover and start user-installed Agent CLIs."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

NAMES = {"claude": "Claude Code", "agy": "AGY", "codebuddy": "CodeBuddy Code", "codex": "Codex CLI"}
# Capture the desktop's environment before runner.py applies production API keys.
LOCAL_ENV = os.environ.copy()
LOCAL_ENV.pop("CLAUDECODE", None)
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
WORKSPACE_AGENTS = {
    "5": ("grist-assembly", "Video assembly in Grist"),
    "6": ("grist-covers", "Cover design in Grist"),
}


def selected(settings, step):
    override = settings.get(f"step{step}", {}).get("agent_cli")
    default = settings.get("agents", {}).get("default")
    return next((agent for agent in (override, default) if isinstance(agent, str) and agent in NAMES), "agy")


def discover(agent):
    name = NAMES[agent]
    env = LOCAL_ENV.copy()
    paths = env.get("PATH", "").split(os.pathsep)
    if os.name != "nt":
        # Finder does not inherit the user's terminal PATH.
        try:
            result = subprocess.run(
                [env.get("SHELL") or "/bin/zsh", "-ilc", 'printf "\\nGRIST_AGENT_PATH=%s\\n" "$PATH"'],
                cwd=Path.home(), env=env, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, errors="replace", timeout=5,
            )
            for line in reversed(result.stdout.splitlines()):
                if line.startswith("GRIST_AGENT_PATH="):
                    paths.extend(line.removeprefix("GRIST_AGENT_PATH=").split(os.pathsep))
                    break
        except (OSError, subprocess.TimeoutExpired):
            pass
    paths.extend(str(Path.home() / part) for part in (".local/bin", ".npm-global/bin", ".bun/bin", ".volta/bin"))
    extra_path = {"claude": ".claude/local", "agy": ".gemini/bin"}.get(agent)
    if extra_path:
        paths.append(str(Path.home() / extra_path))
    if os.name == "nt":
        if appdata := env.get("APPDATA"):
            paths.append(str(Path(appdata) / "npm"))
        if local_appdata := env.get("LOCALAPPDATA"):
            paths.append(str(Path(local_appdata) / "Programs"))
            if agent == "agy":
                paths.append(str(Path(local_appdata) / "agy" / "bin"))
    else:
        paths.extend(("/opt/homebrew/bin", "/usr/local/bin"))
    env["PATH"] = os.pathsep.join(dict.fromkeys(path for path in paths if Path(path).is_absolute()))
    program = shutil.which(agent, path=env["PATH"])
    if not program:
        raise ValueError(f"{name} is not installed or could not be found on PATH.")
    return program, env


def prepare_workspace_agent(step, cwd, context):
    """AGY loads the Markdown body of a workspace agent as its system prompt."""
    name, description = WORKSPACE_AGENTS[step]
    definition = cwd / ".agents" / "agents" / f"{name}.md"
    definition.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        "subagent: false\ntools:\n"
        "  - view_file\n  - list_dir\n  - grep_search\n  - write_to_file\n"
        "  - replace_file_content\n  - run_command\n  - command_status\n---\n\n"
        f"{context}\n", encoding="utf-8",
    )


def arguments(launch, *, prompt=None, max_turns=None):
    agent = launch["agent"]
    context = Path(launch["context"])
    step = launch.get("step")
    production_agent = step in WORKSPACE_AGENTS
    project_dir = launch.get("project_dir")
    project_args = ["--add-dir", project_dir] if project_dir else []
    if agent == "agy":
        if production_agent:
            args = ["--agent", WORKSPACE_AGENTS[step][0], *project_args]
        else:
            task = f"Read the task brief at {context} before working on this task."
            if prompt is not None:
                return ["--print", f"{task}\n\n{prompt}", "--output-format", "json"]
            args = ["--prompt-interactive", task + "\nThen wait for my instructions.", *project_args]
        settings = {"trustedWorkspaces": list(dict.fromkeys([str(Path(launch["cwd"]).resolve()), *project_args[1:]]))}
        if prompt is None:
            return [*args, "--mode", "auto", "--settings", json.dumps(settings)]
        settings["toolPermission"] = "always-proceed"
        return [*args, "--settings", json.dumps(settings), "--print", prompt, "--output-format", "json"]
    if agent == "codex":
        task = f"Read the task brief at {context} before working on this task."
        if prompt is None:
            # A positional prompt starts a turn immediately. Keep the brief on disk
            # and give Codex its path as a session instruction instead.
            instructions = f"On the first user request, read the Grist task brief at \"{context}\" before acting."
            cwd = json.dumps(str(Path(launch["cwd"]).resolve()), ensure_ascii=False)
            return ["-c", f"developer_instructions={json.dumps(instructions, ensure_ascii=False)}",
                    "--approve-for-me", "-c", f'projects.{cwd}.trust_level="trusted"', *project_args]
        # An unattended session works inside the edit or design workspace and the project folder,
        # with edits and renders approved through the workspace-write sandbox.
        headless_args = ["exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write",
                         "--approve-for-me", *project_args]
        return [*headless_args, f"{task}\n\n{prompt}"]
    if agent == "codebuddy":
        if production_agent:
            args = ["--system-prompt-file", str(context), *project_args]
        else:
            brief = f'Use the Read tool to read the Grist task brief at "{context}" before acting on this task.'
            args = ["--append-system-prompt", brief, *project_args]
    else:
        args = ["--append-system-prompt-file", str(context)]
    if prompt is None:
        if agent == "codebuddy":
            directories = list(dict.fromkeys([str(Path(launch["cwd"]).resolve()), *project_args[1:]]))
            return [*args, "--permission-mode", "auto", "--settings", json.dumps({"trustedDirectories": directories})]
        return [*args, "--permission-mode", "auto"]

    args = ["--print", prompt, *args, "--output-format", "json", "--max-turns", str(max_turns)]
    if production_agent or agent == "codebuddy":
        # Pre-approve edits and the render commands for this run without changing user settings.
        allowed = ["Bash(python *)", "Bash(python3 *)"]
        if step != "6":
            allowed.extend(["Bash(ffmpeg *)", "Bash(ffprobe *)"])
        if agent == "codebuddy":
            if production_agent:
                # The approval rules name CLI tools; a user's REPL mode exposes different tools.
                args.extend(["--tool-mode", "cli"])
            args.extend(["--permission-mode", "acceptEdits", "--allowedTools", "Read", "Glob", "Grep", *allowed])
        else:
            args.extend(["--permission-mode", "acceptEdits", "--allowedTools", ",".join(allowed), *project_args])
    return args


def codebuddy_result(payload):
    # CodeBuddy JSON output is a transcript array whose final entry is the result.
    if isinstance(payload, list) and payload:
        result = payload[-1]
        if isinstance(result, dict) and result.get("type") == "result":
            return result
    return None


def codex_result(text):
    """The last turn.completed event of Codex exec JSONL; warning items do not mean a failed run."""
    final = None
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "turn.completed":
            final = event
    return final


def check(agent):
    status = {"id": agent, "installed": False, "path": None, "version": None, "logged_in": None}
    try:
        program, env = discover(agent)
    except ValueError:
        return status
    status.update(installed=True, path=program)
    try:
        with tempfile.TemporaryDirectory(prefix="grist-agent-check-") as cwd:
            def run(args, timeout):
                return subprocess.run([program, *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                      creationflags=NO_WINDOW)
            version = run(["--version"], 10)
            if version.returncode == 0 and version.stdout.strip():
                status["version"] = version.stdout.strip().splitlines()[0]
            if agent == "claude":
                result = run(["auth", "status", "--json"], 15)
                logged_in = json.loads(result.stdout).get("loggedIn")
                if isinstance(logged_in, bool):
                    status["logged_in"] = logged_in
            elif agent == "codebuddy":
                result = run(["--print", "Reply only with OK.", "--tools", "",
                              "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                              "--no-session-persistence", "--max-turns", "1", "--output-format", "json"], 25)
                try:
                    session = codebuddy_result(json.loads(result.stdout))
                except ValueError:
                    session = None
                if result.returncode == 0 and session and session.get("is_error") is False:
                    status["logged_in"] = True
            elif agent == "codex":
                # Without a terminal, the login report is written to stderr.
                result = run(["login", "status"], 15)
                report = (result.stdout + "\n" + result.stderr).lower()
                if result.returncode == 0 and "logged in" in report and "not logged in" not in report:
                    status["logged_in"] = True
            else:
                # The model catalog requires an account. It reports sign-out without opening a chat.
                result = run(["models"], 20)
                if result.returncode == 0 and any("\t" in line for line in result.stdout.splitlines()):
                    status["logged_in"] = True
            if status["logged_in"] is None and agent != "claude" and re.search(
                r"not (?:logged|signed) in|login required|authentication required|unauthenticated|please (?:log|sign) in",
                result.stdout + "\n" + result.stderr, re.IGNORECASE,
            ):
                status["logged_in"] = False
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass  # A failed or timed-out status probe leaves the login unknown.
    return status


if __name__ == "__main__":
    # Native PTY startup uses this same adapter as the background runner.
    try:
        raw_manifest = sys.argv[1]
        if os.name == "nt" and raw_manifest.startswith("\\\\?\\"):
            raw_manifest = raw_manifest[4:]
        manifest = Path(raw_manifest).resolve(strict=True)
        launch = json.loads(manifest.read_text(encoding="utf-8"))
        # The native host checked the context against the registered project's .studio/agent directory.
        project = manifest.parents[3]
        if not Path(launch["cwd"]).resolve(strict=True).is_relative_to(project) or (
            launch.get("project_dir") and Path(launch["project_dir"]).resolve(strict=True) != project
        ):
            raise ValueError("The agent workspace must be inside the project.")
        program, env = discover(launch["agent"])
        print(json.dumps({"program": program, "path": env["PATH"], "args": arguments(launch)}))
    except (OSError, ValueError, KeyError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
