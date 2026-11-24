"""Discover and start user-installed Agent CLIs."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

NAMES = {"claude": "Claude Code", "agy": "AGY"}
# Capture the desktop's environment before runner.py applies production API keys.
LOCAL_ENV = os.environ.copy()
LOCAL_ENV.pop("CLAUDECODE", None)
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
ASSEMBLY_AGENT = "grist-assembly"


def selected(settings, step):
    override = settings.get(f"step{step}", {}).get("agent_cli")
    default = settings.get("agents", {}).get("default")
    return next((agent for agent in (override, default) if isinstance(agent, str) and agent in NAMES), "claude")


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
    paths.append(str(Path.home() / (".claude/local" if agent == "claude" else ".gemini/bin")))
    if os.name != "nt":
        paths.extend(("/opt/homebrew/bin", "/usr/local/bin"))
    env["PATH"] = os.pathsep.join(dict.fromkeys(path for path in paths if Path(path).is_absolute()))
    program = shutil.which(agent, path=env["PATH"])
    if not program:
        raise ValueError(f"{name} is not installed or could not be found on PATH.")
    return program, env


def prepare_assembly_agent(cwd, context):
    """AGY loads the Markdown body of a workspace agent as its system prompt."""
    definition = cwd / ".agents" / "agents" / f"{ASSEMBLY_AGENT}.md"
    definition.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text(
        f"---\nname: {ASSEMBLY_AGENT}\ndescription: Video assembly in Grist\n"
        "subagent: false\ntools:\n"
        "  - view_file\n  - list_dir\n  - grep_search\n  - write_to_file\n"
        "  - replace_file_content\n  - run_command\n  - command_status\n---\n\n"
        f"# Grist assembly\n\n{context}\n", encoding="utf-8",
    )


def arguments(launch, *, prompt=None, max_turns=None):
    agent = launch["agent"]
    context = Path(launch["context"])
    assembly = launch.get("step") == "5"
    project_dir = launch.get("project_dir")
    project_args = ["--add-dir", project_dir] if project_dir else []
    if agent == "agy":
        if assembly:
            args = ["--agent", ASSEMBLY_AGENT, *project_args]
            if prompt is None:
                return args
            workspaces = [str(Path(launch["cwd"]).resolve()), *([project_dir] if project_dir else [])]
            settings = {"trustedWorkspaces": workspaces, "toolPermission": "always-proceed"}
            return [*args, "--settings", json.dumps(settings), "--print", prompt, "--output-format", "json"]
        task = f"Read the task brief at {context} before working on this task."
        if prompt is None:
            return ["--prompt-interactive", task + "\nThen wait for my instructions."]
        return ["--print", f"{task}\n\n{prompt}", "--output-format", "json"]
    args = ["--append-system-prompt-file", str(context)]
    if prompt is None:
        return args
    args = ["--print", prompt, *args, "--output-format", "json", "--max-turns", str(max_turns)]
    if assembly:
        # Print mode has no trust dialog. Approve the tools required by the render contract for this run.
        args.extend(["--permission-mode", "acceptEdits", "--allowedTools",
                     "Bash(python *),Bash(python3 *),Bash(ffmpeg *),Bash(ffprobe *)", *project_args])
    return args


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
                                      capture_output=True, text=True, errors="replace", timeout=timeout,
                                      creationflags=NO_WINDOW)
            version = run(["--version"], 10)
            if version.returncode == 0 and version.stdout.strip():
                status["version"] = version.stdout.strip().splitlines()[0]
            if agent == "claude":
                result = run(["auth", "status", "--json"], 15)
                logged_in = json.loads(result.stdout).get("loggedIn")
                if isinstance(logged_in, bool):
                    status["logged_in"] = logged_in
            else:
                # The model catalog requires an account. It reports sign-out without opening a chat.
                result = run(["models"], 20)
                if result.returncode == 0 and any("\t" in line for line in result.stdout.splitlines()):
                    status["logged_in"] = True
                if status["logged_in"] is None and re.search(
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
        launch = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        program, env = discover(launch["agent"])
        print(json.dumps({"program": program, "path": env["PATH"], "args": arguments(launch)}))
    except (OSError, ValueError, KeyError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
