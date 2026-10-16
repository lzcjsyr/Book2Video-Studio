"""Generate each selected visual using its saved type, with isolated motion sessions."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess


MOTION_CONCURRENCY = 3
PROVIDER_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_API_KEY_FILE_DESCRIPTOR",
)


def claude_command(saved):
    """Honor the same subscription/API choice as the interactive terminal."""
    env = os.environ.copy()
    for key in (*PROVIDER_ENV, "CLAUDECODE"):
        env.pop(key, None)
    if saved.get("mode") == "subscription":
        program = saved.get("path") or shutil.which("claude")
    else:
        for key in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"):
            env.pop(key, None)
        program = os.environ.get("STUDIO_CLAUDE") or shutil.which("claude")
        from core.infra.ai.claude_agent import build_step4_motion_agent_env
        env.update(build_step4_motion_agent_env())
    if not program or not Path(program).is_file():
        raise ValueError("Claude Code is unavailable. Check its setup in Settings.")
    return str(program), env


def generate_motion(launch, program, env, max_turns):
    """A separate CLI process, prompt, output and render report for one segment."""
    work = Path(launch["cwd"])
    payload = json.loads((work / "studio-payload.json").read_text(encoding="utf-8"))
    index = payload["segmentIndex"]
    print(f"Generating motion for segment {index} with Claude Code…", flush=True)
    with (work / "claude-session.json").open("w", encoding="utf-8") as log, (work / "claude-stderr.log").open("w", encoding="utf-8") as errors:
        result = subprocess.run([
            program, "-p", "Complete this segment's motion, checks and final render using the supplied task context. Finish when verified; do not wait for user input.",
            *launch["args"], "--output-format", "json", "--max-turns", str(max_turns),
            "--permission-mode", "acceptEdits", "--tools", "Read,Write,Edit,Bash,Glob,Grep",
            "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep", "--setting-sources", "project,local",
            *(["--model", env["ANTHROPIC_MODEL"]] if env.get("ANTHROPIC_MODEL") else []),
        ], cwd=work, env=env, stdout=log, stderr=errors, text=True, timeout=1800)
    if result.returncode:
        raise ValueError(f"Claude Code failed for segment {index}. See {work / 'claude-session.json'}")
    # Check the result as well as the exit code: max-turns and tool failures can exit with zero.
    try:
        session = json.loads((work / "claude-session.json").read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"Claude Code returned an invalid result for segment {index}.") from exc
    if session.get("is_error") or not (work / "index.html").is_file():
        raise ValueError(f"Claude Code did not complete segment {index}. See {work / 'claude-session.json'}")
    from core.infra.motion.renderer import render_clip
    # Cached verified output is reused; unfinished or stale output is rendered and verified here.
    render_clip(work, payload, work / "video.mp4", fps=int(payload.get("fps", 30)))
    report = json.loads((work / "render-report.json").read_text(encoding="utf-8"))
    if not report.get("verified"):
        raise ValueError(f"Motion verification failed for segment {index}.")
    print(f"Motion segment {index} complete and verified.", flush=True)
    return index


def run(request, params, steps, call):
    from context import prepare
    from server import Studio
    project = Path(request["project"])
    script = json.loads((project / "text/script.json").read_text(encoding="utf-8"))
    targets = set(request["indices"])
    if request.get("visualizer") == "hf":
        script["segments"] = [{**s, "visualizer": "hf"} if s["index"] in targets else s for s in script["segments"]]
    images = [s["index"] for s in script["segments"] if s["index"] in targets and s.get("visualizer", "image") != "hf"]
    motions = [s["index"] for s in script["segments"] if s["index"] in targets and s.get("visualizer") == "hf"]
    launches = []
    if motions:
        program, env = claude_command(request.get("claude", {}))
        studio = Studio(Path(request["engine"]), Path(request["studio_data"]), Path(request["workspace"]))
        for index in motions:
            work = Path(request["motion_folders"][str(index)])
            launches.append((index, prepare(studio, project, {"step": "4", "indices": [index], "folders": {index: work}})))

    processed, failed, errors = [], [], []
    opening_done = False
    # One image pipeline owns the opening and the image pool, so the provider cap also covers the opening.
    if images or request.get("opening"):
        from runner import check_image_model
        check_image_model(params["image_server"], params["image_model"])
        print(f"Generating {len(images)} image segment(s)…", flush=True)
        try:
            result = call(steps.run_step_4, {**params, "project_output_dir": str(project), "target_segments": images,
                "regenerate_opening": bool(request.get("opening")), "opening_quote": bool(request.get("opening")),
                "visual_mode": "static_image", "llm_server": params["llm_server_step4"],
                "llm_model": params["llm_model_step4"], "llm_base_url": params["llm_base_url_step4"]})
            failed_images = set(result.get("failed_segments", []))
            if not result.get("success") and not failed_images:
                failed_images = set(images)
            processed.extend(i for i in images if i not in failed_images)
            failed.extend(sorted(failed_images))
            opening_done = bool(request.get("opening") and result.get("opening_image_path"))
            if not result.get("success"):
                errors.append(result.get("message", "Image generation failed."))
        except Exception as exc:
            failed.extend(images)
            errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=MOTION_CONCURRENCY) as pool:
        futures = {pool.submit(generate_motion, launch, program, env, max(1, int(params.get("motion_max_turns", 60)))): index
                   for index, launch in launches}
        for future in as_completed(futures):
            index = futures[future]
            try:
                processed.append(future.result())
            except Exception as exc:
                failed.append(index)
                errors.append(str(exc))
                print(f"Motion segment {index} failed: {exc}", flush=True)
    success = not failed and not errors and (not request.get("opening") or opening_done)
    return {"success": success, "processed_segments": sorted(processed), "failed_segments": sorted(failed),
            "opening_generated": opening_done,
            "message": f"Generated {len(processed)} segment(s)." if success else " ".join(errors) or "The opening could not be generated."}
