"""Claude Code sessions that run without a terminal: motion clips, and Assembly and Covers in an automatic run."""
import json
import os
from pathlib import Path
import shutil
import subprocess

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
        result = subprocess.run([
            program, "-p", prompt, *launch["args"], "--output-format", "json", "--max-turns", str(max_turns),
            "--permission-mode", "acceptEdits", "--tools", TOOLS, "--allowedTools", TOOLS, "--setting-sources", "project,local",
            *(["--model", env["ANTHROPIC_MODEL"]] if env.get("ANTHROPIC_MODEL") else []),
        ], cwd=launch["cwd"], env=env, stdout=log, stderr=errors, text=True, timeout=timeout)
    # Check the result as well as the exit code: max-turns and tool failures can exit with zero.
    try:
        session = json.loads((logs / "claude-session.json").read_text(encoding="utf-8"))
    except ValueError:
        session = None
    if result.returncode or not isinstance(session, dict) or session.get("is_error"):
        raise ValueError(f"Claude Code did not finish. See {logs / 'claude-session.json'}")
    return session


def run_session(request):
    """Assembly or Covers by Claude Code without a terminal; the job succeeds when it delivered."""
    step, launch = request["step"], request["launch"]
    project = Path(request["project"])
    turns, timeout = SESSIONS[step]
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
