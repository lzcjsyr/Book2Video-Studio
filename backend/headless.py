"""Claude Code sessions that run without a terminal: motion clips, and Assembly and Covers in an automatic run."""
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time

PROVIDER_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_API_KEY_FILE_DESCRIPTOR",
)
TOOLS = "Read,Write,Edit,Bash,Glob,Grep"
# How many turns each session may take and how long it may run (seconds).
SESSIONS = {"5": (200, 3 * 3600), "6": (100, 3600)}
_running = {}
# Held while a CLI starts, so a cancelled job neither misses a session nor starts another one.
_starting = threading.RLock()
_cancelled = False


def _signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def stop_sessions(*processes):
    """Stop CLIs and their renders, including background shells they started."""
    if os.name == "nt":
        for process in processes:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    else:
        for process in processes:
            _signal_group(process, signal.SIGTERM)
        deadline = time.monotonic() + 3
        for process in processes:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        # A shell can exit before its background render. Reap the whole group.
        for process in processes:
            _signal_group(process, signal.SIGKILL)
    for process in processes:
        process.wait()


def install_signal_handlers():
    """Cancelling a job stops each separately supervised CLI, then ends the job at once: work still
    queued in a thread pool must not start, and a thread waiting on a provider must not hold the exit."""
    def stop(signum, _frame):
        global _cancelled
        with _starting:
            _cancelled = True
            sessions = list(_running.values())
        stop_sessions(*sessions)
        os._exit(128 + signum)
    signal.signal(signal.SIGTERM, stop)


def session_limits(request):
    turns, timeout = SESSIONS[request["step"]]
    if request["step"] == "5":
        import editing
        materials = editing.folder(Path(request["project"]), request["launch"]["assembly_edit"]) / "materials.json"
        if materials.is_file():
            found = json.loads(materials.read_text(encoding="utf-8"))
            seconds = sum(s["audio_seconds"] for s in found["segments"])
            if found.get("opening"):
                seconds += found["opening"]["audio_seconds"]
            seconds /= max(.1, float(found.get("narration_speed", 1)))
            # Allow one captured frame/second at 30 fps, retaining the three-hour
            # minimum for design work. Long videos need more than a fixed limit.
            if math.isfinite(seconds) and seconds > 0:
                timeout = max(timeout, math.ceil(seconds * 30))
    return turns, timeout


def first_message():
    """What a session without a terminal is told first: agent_rules.md's "automatic" section."""
    from core.infra.ai.task_context import agent_rules
    return agent_rules("automatic")


def claude_command(saved, step="4"):
    """Honor the same subscription/API choice as the interactive terminal, with the step's provider:
    motion uses its own agent provider; Assembly and Covers use Writing's, as their terminals do."""
    env = os.environ.copy()
    for key in (*PROVIDER_ENV, "CLAUDECODE"):
        env.pop(key, None)
    if saved.get("mode") == "subscription":
        program = saved.get("path") or shutil.which("claude")
    else:
        for key in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"):
            env.pop(key, None)
        program = os.environ.get("STUDIO_CLAUDE") or shutil.which("claude")
        if step == "4":
            from core.infra.ai.claude_agent import build_step4_motion_agent_env as provider
        else:
            from core.infra.ai.claude_agent import build_step1_agent_env as provider
        env.update(provider())
    if not program or not Path(program).is_file():
        raise ValueError("Claude Code is unavailable. Check its setup in Settings.")
    return str(program), env


def run_claude(program, env, launch, prompt, max_turns, timeout, logs: Path):
    """One print-mode session with the launch's context; returns its result, or raises when it did not finish."""
    with (logs / "claude-session.json").open("w", encoding="utf-8") as log, (logs / "claude-stderr.log").open("w", encoding="utf-8") as errors:
        with _starting:
            if _cancelled:
                raise ValueError("The job was stopped.")
            process = subprocess.Popen([
                program, "-p", prompt, *launch["args"], "--output-format", "json", "--max-turns", str(max_turns),
                "--permission-mode", "acceptEdits", "--tools", TOOLS, "--allowedTools", TOOLS, "--setting-sources", "project,local",
                *(["--model", env["ANTHROPIC_MODEL"]] if env.get("ANTHROPIC_MODEL") else []),
            ], cwd=launch["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=errors, text=True,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            _running[process.pid] = process
        try:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise ValueError("Claude Code timed out. Completed files were kept; retry Missing and outdated to continue.") from None
        finally:
            try:
                stop_sessions(process)
            finally:
                _running.pop(process.pid, None)
    # Check the result as well as the exit code: max-turns and tool failures can exit with zero.
    try:
        session = json.loads((logs / "claude-session.json").read_text(encoding="utf-8"))
    except ValueError:
        session = None
    if code or not isinstance(session, dict) or session.get("is_error"):
        raise ValueError(f"Claude Code did not finish. See {logs / 'claude-session.json'}")
    return session


def run_session(request):
    """Assembly or Covers by Claude Code without a terminal; the job succeeds when it delivered."""
    step, launch = request["step"], request["launch"]
    project = Path(request["project"])
    turns, timeout = session_limits(request)
    program, env = claude_command(request.get("claude", {}), step)
    print("Claude Code is editing the video…" if step == "5" else "Claude Code is designing the covers…", flush=True)
    run_claude(program, env, launch, first_message(), turns, timeout, Path(launch["context"]).parent)
    if step == "5":
        import editing
        if not editing.detail(project, launch["assembly_edit"])["delivered"]:
            raise ValueError("Claude Code ended without delivering the final video.")
        return {"success": True, "message": "Claude Code delivered the final video."}
    import covers
    if not covers.detail(project, launch["cover_design"])["complete"]:
        raise ValueError("Claude Code ended without saving every cover design.")
    return {"success": True, "message": "Claude Code designed the covers."}
