"""Local Agent sessions that run without a terminal: motion clips, and Assembly and Covers in an automatic run."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from local_agents import NAMES, arguments, codebuddy_result, codex_result, discover

# How many turns each session may take and how long it may run (seconds).
SESSIONS = {"1": (80, 1800), "5": (200, 3 * 3600), "6": (100, 3600)}
_running = {}
# Held while a CLI starts, so a cancelled job neither misses a session nor starts another one.
_starting = threading.RLock()
_cancelled = False


def _signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS may refuse signals to a remaining helper after the CLI itself has exited.
        if process.poll() is None:
            raise


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
    """Kickoff message for standalone motion generation."""
    from core.infra.ai.task_context import agent_rules
    return agent_rules("automatic")


def run_agent(program, env, launch, prompt, max_turns, timeout, logs: Path):
    """One print-mode session with the launch's context; returns its result, or raises when it did not finish."""
    agent = launch["agent"]
    if launch.get("deferred_render"):
        env = {**env, "GRIST_DEFER_RENDER": "1"}
    name = NAMES[agent]
    report = logs / f"{agent}-session.json"
    with report.open("w", encoding="utf-8") as log, (logs / f"{agent}-stderr.log").open("w", encoding="utf-8") as errors:
        with _starting:
            if _cancelled:
                raise ValueError("The job was stopped.")
            process = subprocess.Popen([
                program, *arguments(launch, prompt=prompt, max_turns=max_turns),
            ], cwd=launch["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=errors, text=True,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            _running[process.pid] = process
        try:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise ValueError(f"{name} timed out. Completed files were kept; retry Missing and outdated to continue.") from None
        finally:
            try:
                stop_sessions(process)
            finally:
                _running.pop(process.pid, None)
    # Check the result as well as the exit code: max-turns and tool failures can exit with zero.
    text = report.read_text(encoding="utf-8")
    try:
        session = json.loads(text)
    except ValueError:
        session = None
    if agent == "codebuddy":
        session = codebuddy_result(session)
        if session and session.get("is_error") is not False:
            session = None
    elif agent == "codex":
        # Codex writes JSONL events; the run finished only when a turn completed.
        session = codex_result(text)
    if code or not isinstance(session, dict) or (session.get("status") != "SUCCESS" if agent == "agy" else session.get("is_error")):
        raise ValueError(f"{name} did not finish. See {report}")
    return session


def run_session(request):
    """Run a local Agent without a terminal and verify the expected deliverable."""
    step, launch = request["step"], request["launch"]
    project = Path(request["project"])
    turns, timeout = session_limits(request)
    name = NAMES[launch["agent"]]
    program, env = discover(launch["agent"])
    action = {"1": "writing the manuscript", "5": "editing the video", "6": "designing the covers"}[step]
    print(f"{name} is {action}…", flush=True)
    run_agent(program, env, launch, "请根据要求开始工作。", turns, timeout, Path(launch["context"]).parent)
    if step == "1":
        # The service validates the staged manuscript before publishing it.
        return {"success": True, "message": f"{name} finished writing."}
    if step == "5":
        import editing
        edit = launch["assembly_edit"]
        if launch.get("deferred_render") and not editing.detail(project, edit)["delivered"]:
            from core.infra.motion.assembly import run_manifest
            work = editing.folder(project, edit)
            if (work / "render.json").is_file():
                print("Rendering Codex CLI's assembly manifest with the bundled renderer…", flush=True)
                run_manifest(work, delivery=project / editing.DELIVERY)
        if not editing.detail(project, edit)["delivered"]:
            raise ValueError(f"{name} ended without delivering the final video.")
        return {"success": True, "message": f"{name} delivered the final video."}
    import covers
    if not covers.detail(project, launch["cover_design"])["complete"]:
        raise ValueError(f"{name} ended without saving every cover design.")
    return {"success": True, "message": f"{name} designed the covers."}
