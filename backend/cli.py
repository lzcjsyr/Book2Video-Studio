"""Grist's command-line application. All project changes go through the shared service."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

from production_cli import STEPS
from service import discover, request

NAMES = {step["name"]: number for number, step in STEPS.items()}
RUNNING = {"running", "queued", "cancelling", "stopping"}
API = "/api/"


class CommandError(Exception):
    pass


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def load(path):
    return json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text(encoding="utf-8"))


def text_file(path):
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def number(name):
    if name in STEPS:
        return name
    try:
        return NAMES[name]
    except KeyError:
        raise argparse.ArgumentTypeError(f"Choose a step number or name: {', '.join(NAMES)}.") from None


def names(value):
    return [number(item.strip()) for item in value.split(",")]


def indices(value):
    try:
        return [int(item) for item in value.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError("Enter comma-separated segment numbers, such as 1,3.") from None


def positive(value):
    try:
        result = int(value)
        if result > 0:
            return result
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Enter a positive integer.")


class Client:
    def __init__(self, *, start=True):
        self.data = Path(os.environ["GRIST_RESOLVED_DATA_DIR"])
        self.endpoint = discover(self.data)
        if self.endpoint is None and start:
            run = subprocess.run([os.environ["GRIST_HOST_EXECUTABLE"], "__grist_service"],
                                 capture_output=True, text=True, timeout=40)
            if run.returncode:
                raise CommandError(run.stderr.strip() or "Could not start the Grist service.")
            self.endpoint = json.loads(run.stdout)

    def call(self, path, body=None):
        payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") if body is not None else None
        return self.send(path, payload)

    def send(self, path, payload, *, timeout=30):
        if not self.endpoint:
            raise CommandError("The Grist service is stopped.")
        try:
            return request(self.endpoint, API + path, payload, timeout=timeout)
        except HTTPError as exc:
            with exc:
                try:
                    message = json.load(exc)["error"]
                except (ValueError, KeyError):
                    message = exc.reason
            raise CommandError(f"{message} (HTTP {exc.code})") from None
        except (OSError, URLError) as exc:
            raise CommandError(f"Could not reach the Grist service: {exc}") from None


def install(target_dir):
    if os.name == "nt":
        raise CommandError("Command installation is available on macOS and Unix.")
    host = Path(os.environ["GRIST_HOST_EXECUTABLE"])
    folder = Path(target_dir or Path.home() / ".local/bin").expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    entry = folder / "grist"
    script = f'#!/bin/sh\n# Grist command-line launcher\nexec {shlex.quote(str(host))} cli "$@"\n'
    if entry.exists() or entry.is_symlink():
        if entry.is_symlink() or not entry.is_file() or entry.read_text(encoding="utf-8") != script:
            raise CommandError(f"A different command already exists at {entry}. Choose another --bin-dir.")
    else:
        fd = os.open(entry, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(script)
    on_path = any(Path(folder_name).expanduser().resolve() == folder
                  for folder_name in os.get_exec_path() if folder_name)
    return {"command": str(entry), "on_path": on_path, **({} if on_path else {"add_to_path": str(folder)})}


def parser():
    root = argparse.ArgumentParser(prog="grist",
        description="Drive the six-step Grist video pipeline without opening the desktop window.",
        epilog="Use 'grist guide' for Agent workflow rules and 'grist schema [STEP]' for live fields and choices.")
    root.add_argument("--json", action="store_true", help="Machine-readable JSON (the default).")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("guide", help="Read the workflow, configuration, state and artifact conventions.")
    schema = commands.add_parser("schema", help="Inspect supported fields, types, defaults and current library choices.")
    schema.add_argument("step", nargs="?", type=number, help="Step number or name; omit for all six.")
    install_cmd = commands.add_parser("install", help="Add a grist launcher for this app bundle.")
    install_cmd.add_argument("--bin-dir", help="Defaults to ~/.local/bin.")
    service = commands.add_parser("service", help="Inspect or stop the local service.")
    service.add_argument("action", choices=["status", "stop"])
    project = commands.add_parser("project", help="Create, locate, or inspect a video project.")
    actions = project.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    status = actions.add_parser("status")
    status.add_argument("project")
    create = actions.add_parser("create")
    create.add_argument("name")
    create.add_argument("--source", help="Existing source file or folder; selects source-based writing.")
    create.add_argument("--location", help="Parent folder for the new project.")
    create.add_argument("--params", help="Saved project settings (section-keyed JSON file or - for stdin).")
    opening = actions.add_parser("open")
    opening.add_argument("folder", help="Register an existing project directory.")
    config = commands.add_parser("config", help="Inspect or persist project-specific production settings.")
    config.add_argument("project")
    config.add_argument("--set", dest="params", help="Save a section-keyed JSON patch.")
    config.add_argument("--unset", action="append", default=[], metavar="SECTION.FIELD",
                        help="Remove a project override and inherit its default. Repeat for multiple fields.")
    configure = commands.add_parser("writing", help="Set the source or import a structured manuscript.")
    actions = configure.add_subparsers(dest="action", required=True)
    write_import = actions.add_parser("import")
    write_import.add_argument("project")
    write_import.add_argument("--file", required=True, help="UTF-8 JSON file or - for stdin.")
    setup = actions.add_parser("setup")
    setup.add_argument("project")
    setup.add_argument("--mode", choices=["direct", "source"], required=True)
    setup.add_argument("--source", help="Existing file or folder; required in source mode.")
    edit = commands.add_parser("content", help="Read or edit the manuscript, segments, or visual direction.")
    edit.add_argument("project")
    edit.add_argument("kind", choices=["raw", "script", "summary"])
    edit.add_argument("--file", help="Save JSON from a file or -. Omit to read.")
    edit.add_argument("--expected-hash", help="Use the hash from the read you based the edit on.")
    edit.add_argument("--selections", help="File mapping manuscript metadata fields to zero-based candidate indices.")
    library = commands.add_parser("library", help="Read styles, prompts, skills, and templates from the local catalog.")
    library.add_argument("group", nargs="?")
    library.add_argument("item", nargs="?")
    library.add_argument("--file", help="Read a specific file in a skill.")
    voice = commands.add_parser("voice", help="List saved voices or manage the local voice runtime.")
    voice.add_argument("action", choices=["list", "status", "install"])
    music = commands.add_parser("music", help="List available background tracks or import one.")
    music.add_argument("--import", dest="path", help="Local MP3, WAV, M4A, AAC or FLAC file.")
    agent = commands.add_parser("agent", help="List providers or check a local Agent's availability.")
    agent.add_argument("name", nargs="?")
    assets = commands.add_parser("assets", help="List or select image, motion, and cover candidates, or import a voice reference.")
    actions = assets.add_subparsers(dest="action", required=True)
    for action in ["list", "cover", "take", "voice-reference"]:
        sub = actions.add_parser(action)
        sub.add_argument("project")
        if action == "cover":
            sub.add_argument("path", help="Project-relative path from assets list.")
        if action == "take":
            sub.add_argument("index", type=int, help="Segment index, or 0 for the opening.")
            sub.add_argument("take", help="Take ID from assets list.")
        if action == "voice-reference":
            sub.add_argument("path", help="File containing the reference recording.")
    review = commands.add_parser("review", help="Mark a ready and current step as reviewed.")
    review.add_argument("project")
    review.add_argument("step", type=number)
    step = commands.add_parser("step", help="Run one of the six production steps.")
    actions = step.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run", epilog=(
        "Use grist schema STEP for setting fields, execution modes and job inputs. "
        "Order of overrides: saved settings < --params; --job < explicit flags."))
    run.add_argument("project")
    run.add_argument("step", type=number, help="1–6 or writing, segments, voice, visuals, assembly, covers.")
    run.add_argument("--params", help="Section-keyed settings to use for this run only.")
    run.add_argument("--job", help="Step job inputs as a JSON file or -; see schema STEP. Explicit job flags win.")
    run.add_argument("--mode", help="Execution mode listed by schema STEP.")
    run.add_argument("--segments", type=indices, help="Comma-separated 1-based segment indices; voice and visuals only.")
    run.add_argument("--opening", action=argparse.BooleanOptionalAction, default=None,
                     help="Refresh enabled opening audio, or include it in selected visuals. --no-opening skips it for voice.")
    run.add_argument("--batch", choices=["all", "missing", "selected"], help="Visual generation scope; selected uses --segments or job indices.")
    run.add_argument("--agent", help="Override the selected local Agent for this step.")
    run.add_argument("--agent-model", help="Select Codex CLI and override its model for this run.")
    run.add_argument("--revise", action="store_true", default=None, help="Continue a saved Agent assembly or HTML cover design.")
    run.add_argument("--missing", action="store_true", help="Plan missing/outdated work. Combine with settings or a writing brief, without explicit job scope or mode.")
    instructions(run)
    waiting(run)
    production = commands.add_parser("production", help="Plan, run, or inspect multiple steps.")
    actions = production.add_subparsers(dest="action", required=True)
    for action in ["plan", "run"]:
        sub = actions.add_parser(action)
        sub.add_argument("project")
        sub.add_argument("--steps", type=names, help="Comma-separated step numbers or names. Defaults to 2–6.")
        sub.add_argument("--scope", choices=["all", "missing"], default="missing", help="missing resumes unfinished/outdated work; all regenerates selected steps.")
        sub.add_argument("--no-covers", action="store_true", help="Exclude step 6 from this run.")
        sub.add_argument("--refresh-summary", action="store_true", help="Refresh a stale saved summary when step 4 runs.")
        sub.add_argument("--params", help="Section-keyed settings override for the entire run.")
        instructions(sub)
        if action == "run":
            waiting(sub)
    for action in ["status", "wait", "stop"]:
        sub = actions.add_parser(action)
        sub.add_argument("id")
        if action == "wait":
            waiting(sub, flag=False)
    actions.add_parser("list")
    job = commands.add_parser("job", help="List, inspect, wait for, or cancel background jobs.")
    actions = job.add_subparsers(dest="action", required=True)
    for action in ["status", "wait", "cancel"]:
        sub = actions.add_parser(action)
        sub.add_argument("id")
        if action == "wait":
            waiting(sub, flag=False)
    actions.add_parser("list")
    return root


def instructions(command):
    group = command.add_mutually_exclusive_group()
    group.add_argument("--instructions", help="Step 1 writing brief; required for direct writing.")
    group.add_argument("--instructions-file", help="Read the writing assignment from a file or -.")


def waiting(command, *, flag=True):
    if flag:
        command.add_argument("--wait", action="store_true", help="Wait for a final status.")
    command.add_argument("--timeout", type=positive, help="Positive seconds to wait. With run, requires --wait to take effect. Timeout leaves the job running.")


def resolve(path):
    return str(Path(path).expanduser().resolve())


def project_path(id):
    return "projects/" + quote(id, safe="")


def when_finished(client, group, result, timeout):
    id = result["id"]
    deadline = time.monotonic() + timeout if timeout else None
    while result["status"] in RUNNING:
        if deadline and time.monotonic() >= deadline:
            return {**result, "wait_timed_out": True}, 124
        time.sleep(min(0.5, max(0, deadline - time.monotonic())) if deadline else 0.5)
        result = client.call(group + "/" + quote(id, safe=""))
    return result, 0 if result["status"] == "succeeded" else 1


def finish_if_requested(client, group, args, result):
    return when_finished(client, group, result, args.timeout) if args.wait else (result, 0)


def edit_content(client, path, kind, value, expected=None, selections=None):
    if expected is None:
        expected = client.call(path)["hashes"][kind]
    body = {"kind": kind, "value": value, "expected_hash": expected}
    if selections is not None:
        body["selections"] = selections
    result = client.call(path + "/save", body)
    return {"id": result["id"], "kind": kind, "hash": result["hashes"][kind], "path": result["path"]}


def run_cli(args):
    cmd = args.command
    if cmd == "guide":
        return {"guide": Path(__file__).with_name("cli-guide.md").read_text(encoding="utf-8")}, 0
    if cmd == "install":
        return install(args.bin_dir), 0
    action = getattr(args, "action", None)
    client = Client(start=cmd != "service")
    path = project_path(args.project) if hasattr(args, "project") else None
    if cmd == "service":
        if not client.endpoint:
            return {"status": "stopped"}, 0
        status = client.call("service" if action == "status" else "service/stop", {} if action == "stop" else None)
        return {**status, "status": "running" if action == "status" else "stopping"}, 0
    if cmd == "schema":
        return client.call("schema" + (f"?step={args.step}" if args.step else "")), 0
    if cmd == "project":
        if action == "list":
            return client.call("projects"), 0
        if action == "status":
            return client.call(path + "?cli=1"), 0
        if action == "open":
            return client.call("projects/open", {"path": resolve(args.folder)}), 0
        body = {"name": args.name}
        for name in ["source", "location"]:
            if value := getattr(args, name):
                body[name] = resolve(value)
        if args.params:
            body["settings"] = load(args.params)
        return client.call("projects?cli=1", body), 0
    if cmd == "config":
        if args.params or args.unset:
            return client.call(path + "/config", {"settings": load(args.params) if args.params else {}, "unset": args.unset}), 0
        return client.call(path + "/config"), 0
    if cmd == "writing":
        if action == "import":
            return edit_content(client, path, "raw", load(args.file)), 0
        body = {"mode": args.mode, "source": resolve(args.source) if args.source else ""}
        return client.call(path + "/writing-source", body), 0
    if cmd == "content":
        if args.file:
            return edit_content(client, path, args.kind, load(args.file), args.expected_hash,
                                load(args.selections) if args.selections else None), 0
        result = client.call(path)
        return {"value": result[args.kind], "hash": result["hashes"][args.kind]}, 0
    if cmd == "library":
        if args.group and args.item:
            values = {"group": args.group, "id": args.item}
            if args.file:
                values["file"] = args.file
            return client.call("library/item?" + urlencode(values)), 0
        catalog = client.call("library")
        if args.group:
            group = next((g for g in catalog["groups"] if g["id"] == args.group), None)
            if group is None:
                raise CommandError(f"Unknown library group: {args.group}.")
            return group, 0
        return catalog, 0
    if cmd == "agent":
        if args.name:
            return client.call("local-agents/" + quote(args.name, safe="")), 0
        return client.call("schema")["agents"], 0
    if cmd == "voice":
        if action == "list":
            return client.call("voices"), 0
        return client.call("local-voice", {} if action == "install" else None), 0
    if cmd == "music":
        if args.path:
            return client.call("music/import", {"path": resolve(args.path)}), 0
        result = client.call("bootstrap")
        return {key: result[key] for key in ("music", "user_music")}, 0
    if cmd == "assets":
        if action == "cover":
            return client.call(path + "/select-cover", {"path": args.path}), 0
        if action == "take":
            return client.call(path + "/take", {"index": args.index, "take": args.take}), 0
        if action == "voice-reference":
            source = Path(args.path)
            if source.stat().st_size > 64 * 1024 * 1024:
                raise CommandError("Choose a voice reference audio file up to 64 MB.")
            return client.send(path + "/voice-reference?" + urlencode({"name": source.name}), source.read_bytes(), timeout=90), 0
        result = client.call(path)
        assets = {key: result[key] for key in ("path", "opening", "segments", "covers", "cover_design", "exports", "voice_reference")}
        selected = result["state"].get("selected_cover")
        assets["selected_cover"] = selected if selected in assets["covers"] else next(iter(assets["covers"]), None)
        return assets, 0
    if cmd == "review":
        return client.call(path + "/review", {"step": args.step}), 0
    if cmd == "step":
        job = load(args.job) if args.job else {}
        if not isinstance(job, dict):
            raise CommandError("--job must contain a JSON object.")
        for key, value in {"mode": args.mode, "indices": args.segments, "opening": args.opening,
                           "batch": args.batch, "revise": args.revise,
                           "instructions": writing_instructions(args)}.items():
            if value is not None:
                job[key] = value
        if args.missing and job.keys() - {"instructions"}:
            raise CommandError("--missing uses the step planner. Use explicit job inputs for a custom action.")
        settings = load(args.params) if args.params else {}
        if not isinstance(settings, dict):
            raise CommandError("--params must contain a section-keyed JSON object.")
        if args.agent or args.agent_model:
            if args.step not in {"1", "4", "5", "6"}:
                raise CommandError("This step does not use a local Agent.")
            section = settings.setdefault(f"step{args.step}", {})
            if args.agent_model and args.agent not in {None, "codex"}:
                raise CommandError("--agent-model is supported by Codex CLI.")
            section["agent_cli"] = args.agent or "codex"
            if args.agent_model:
                section["agent_model"] = args.agent_model
        result = client.call(path + "/step", {"step": args.step, "job": job, "settings": settings,
                                            "pipeline": args.missing})
        return finish_if_requested(client, "autoruns" if "steps" in result else "jobs", args, result)
    if cmd == "production" and action in {"plan", "run"}:
        body = {"steps": args.steps or list(STEPS)[1:], "scope": args.scope, "covers": not args.no_covers,
                "refresh_summary": args.refresh_summary, "instructions": writing_instructions(args) or "",
                "settings": load(args.params) if args.params else {}}
        if action == "plan":
            result = client.call(path + "/autorun-plan", body)
            return result, 1 if result["blocked"] else 0
        return finish_if_requested(client, "autoruns", args, client.call(path + "/autorun", body))
    group = "jobs" if cmd == "job" else "autoruns"
    if action == "list":
        return client.call(group), 0
    task = group + "/" + quote(args.id, safe="")
    if action in {"cancel", "stop"}:
        return client.call(task + "/" + action, {}), 0
    result = client.call(task)
    return when_finished(client, group, result, args.timeout) if action == "wait" else (result, 0)


def writing_instructions(args):
    return text_file(args.instructions_file) if args.instructions_file else args.instructions


def main():
    # JSON is the default. Accept the explicit output flag on any subcommand.
    arguments = [arg for arg in sys.argv[1:] if arg != "--json"]
    try:
        value, code = run_cli(parser().parse_args(arguments))
        emit(value)
        return code
    except (CommandError, ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Wait interrupted. Submitted jobs remain active.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
