"""Automatic production: after Writing, Segments through Covers run one after another in the background.

Each step runs the same jobs its page would, with the project's saved settings. What a step needs is
decided when it starts, from the state the steps before it left: with the "missing" scope only missing
and outdated outputs are made, so finished work is kept; "all" makes everything again (Revision history
keeps the previous outputs). A failed or stopped job ends the run, and the steps after it do not start.
Claude Code steps (motion clips, Claude Code editing, HTML covers) run without a terminal.
"""
from __future__ import annotations

import threading
import time
import uuid

STEPS = ["2", "3", "4", "5", "6"]
ACTIVE = {"running", "stopping"}
# Why a step that ran still is not ready for the steps after it.
INCOMPLETE = {
    "2": "Segmentation finished without segments.",
    "3": "Some recordings are still missing. Open Voice to see which.",
    "4": "Some visuals are still missing. Open Visuals to see which.",
    "5": "The video was not made.",
    "6": "No cover was made.",
}
# A step is planned with the steps before it that change its inputs.
INPUTS = {"3": {"2"}, "4": {"2"}, "5": {"2", "3", "4"}}


def active(studio, project_id) -> bool:
    return any(r["project"] == project_id and r["status"] in ACTIVE for r in studio.autoruns.values())


def public(run):
    return {k: v for k, v in run.items() if not k.startswith("_")}


def options_from(body):
    scope = body.get("scope", "missing")
    if scope not in {"missing", "all"}:
        raise ValueError("Choose whether to make missing and outdated outputs or everything again.")
    refresh, covers = body.get("refresh_summary", False), body.get("covers", True)
    if type(refresh) is not bool or type(covers) is not bool:
        raise ValueError("Invalid automatic production options.")
    return {"scope": scope, "refresh_summary": refresh, "covers": covers}


def blocked(studio, detail):
    """Why automatic production cannot start from this manuscript, or None."""
    from server import first_text
    if not detail["ready"]["1"]:
        return "Write and save the manuscript before automatic production."
    if detail["state"]["stale"].get("1"):
        return detail["state"]["stale"]["1"]["reason"]
    opening = studio.effective(detail["state"]).get("global", {}).get("opening_quote", True)
    if opening and not first_text(detail["raw"].get("golden_quotes")):
        return "Choose an opening quote in Writing, or turn the opening off."
    return None


def next_job(studio, project_id, step, options, done, d=None):
    """The next job this step needs, as (kind, job request), or None when the step has nothing more to do.
    `done` holds the kinds this run already ran for the step, so a kind never runs twice. `d` is the
    project's detail when the caller has it."""
    d = d or studio.detail(project_id)
    state, everything = d["state"], options["scope"] == "all"
    stale = state["stale"].get(step)
    settings = studio.effective(state)
    opening = settings.get("global", {}).get("opening_quote", True)
    if step == "2":
        if "segments" not in done and (everything or not d["ready"]["2"] or stale):
            return "segments", {"step": "2"}
        return None
    if step in {"3", "4"}:
        # A missing content summary is written by the visuals job itself; an existing one only on request,
        # since a new summary outdates every visual.
        if step == "4" and "summary" not in done and str(d["summary"].get("summary", "")).strip() \
                and (everything or (options["refresh_summary"] and state.get("summary_stale"))):
            return "summary", {"step": "4", "mode": "summary"}
        kind, made = ("voice", "audio") if step == "3" else ("visuals", "visual")
        if kind in done:
            return None
        whole = everything or (stale is not None and not stale["segments"])
        outdated = set(stale["segments"]) if stale else set()
        indices = [s["index"] for s in d["segments"] if whole or not s[made] or s["index"] in outdated]
        with_opening = bool(opening and (whole or not d["opening"][made] or 0 in outdated))
        if not indices and not with_opening:
            return None
        if step == "3":
            return kind, {"step": "3", "indices": indices, "opening": with_opening}
        return kind, {"step": "4", "mode": "segments", "batch": "selected", "indices": indices, "opening": with_opening}
    if step == "5":
        if "assembly" not in done and (everything or not d["ready"]["5"] or stale):
            return "assembly", {"step": "5", **({"mode": "agent"} if settings.get("step5", {}).get("assembly_method") == "agent" else {})}
        # Claude Code delivers only the 16:9 video; Studio draws the 3:4 copy around it.
        if settings.get("three_by_four", {}).get("enabled", True) is not False and "three_by_four" not in done and d["ready"]["5"] \
                and (state.get("three_by_four_stale") or "final_video_3x4.mp4" not in d["exports"]):
            return "three_by_four", {"step": "5", "mode": "three_by_four"}
        return None
    if settings.get("step6", {}).get("cover_method") == "agent":
        from server import RENDER_COVERS
        design = d.get("cover_design")
        # A design saved earlier that only waits for its render is rendered, not designed again.
        waiting = bool(stale and stale["reason"] == RENDER_COVERS and design and design["complete"] and design["current"] and not everything)
        if "design" not in done and not waiting and (everything or not d["ready"]["6"] or stale):
            return "design", {"step": "6", "mode": "agent"}
        if "covers" not in done and ("design" in done or waiting):
            return "covers", {"step": "6", "mode": "cover_render"}
        return None
    if "covers" not in done and (everything or not d["ready"]["6"] or stale):
        return "covers", {"step": "6"}
    return None


def plan(studio, project_id, body):
    """What a run with these options would do now, step by step, for the setup page."""
    options = options_from(body)
    with studio.lock:
        d = studio.detail(project_id)
        settings = studio.effective(d["state"])
        steps, running = [], set()
        for step in STEPS:
            if step == "6" and not options["covers"]:
                steps.append({"step": step, "run": False, "off": True})
                continue
            after = sorted(INPUTS.get(step, set()) & running)
            if after:
                # Its work depends on what the steps before it make.
                steps.append({"step": step, "run": True, "after": after})
                running.add(step)
                continue
            jobs, done = [], set()
            while (found := next_job(studio, project_id, step, options, done, d)) and len(jobs) < 3:
                done.add(found[0])
                jobs.append(found)
            entry = {"step": step, "run": bool(jobs), "jobs": [kind for kind, _ in jobs]}
            work = dict(jobs)
            if step in {"3", "4"}:
                kinds = {s["index"]: s.get("visualizer", "image") for s in d["segments"]}
                job = work.get("voice") or work.get("visuals")
                # A new content summary outdates every visual, so all of them follow it.
                if "summary" in work:
                    indices, opening = list(kinds), settings.get("global", {}).get("opening_quote", True)
                else:
                    indices, opening = (job["indices"], job["opening"]) if job else ([], False)
                entry.update(segments=len(indices), opening=bool(opening))
                if step == "4":
                    entry.update(images=sum(kinds[i] != "hf" for i in indices), motion=sum(kinds[i] == "hf" for i in indices),
                                 summary="rewrite" if "summary" in work else "write" if job and not str(d["summary"].get("summary", "")).strip() else None)
            steps.append(entry)
            if jobs:
                running.add(step)
        return {"steps": steps, "blocked": blocked(studio, d), "segments": len(d["segments"])}


def start(studio, project_id, body):
    from server import Conflict
    options = options_from(body)
    with studio.lock:
        studio.project(project_id)
        if studio.busy(project_id):
            raise Conflict("Finish the running job or Claude Code session first.")
        problem = blocked(studio, studio.detail(project_id))
        if problem:
            raise ValueError(problem)
        # Only the latest run of a project is listed.
        for old in [r["id"] for r in studio.autoruns.values() if r["project"] == project_id]:
            del studio.autoruns[old]
        run = {"id": uuid.uuid4().hex, "project": project_id, "status": "running", "options": options,
               "steps": [{"step": s, "status": "pending", "jobs": [], "message": "", "note": ""} for s in STEPS if s != "6" or options["covers"]],
               "current": None, "job": None, "started": time.time(), "ended": None, "message": ""}
        studio.autoruns[run["id"]] = run
    threading.Thread(target=drive, args=(studio, run), daemon=True).start()
    return public(run)


def stop(studio, run_id):
    with studio.lock:
        run = studio.autoruns[run_id]
        if run["status"] == "running":
            run["status"] = "stopping"
            # The job it waits for stops too; between jobs, the run stops before the next one.
            if run["job"] and studio.jobs.get(run["job"], {}).get("status") == "running":
                studio.cancel_job(run["job"])
        return public(run)


def finish(run, status, entry=None, message=""):
    run.update(status=status, ended=time.time(), job=None, message=message)
    if entry:
        entry.update(status="stopped" if status == "stopped" else "failed", message=message)


def wait(studio, job_id):
    while studio.jobs[job_id]["status"] in {"running", "cancelling"}:
        time.sleep(0.5)
    return studio.jobs[job_id]


def drive(studio, run):
    project, entry = run["project"], None
    try:
        for entry in run["steps"]:
            step, done = entry["step"], set()
            while True:
                with studio.lock:
                    if run["status"] == "stopping":
                        return finish(run, "stopped", entry)
                    found = next_job(studio, project, step, run["options"], done)
                    if not found:
                        break
                    kind, body = found
                    done.add(kind)
                    entry["status"], run["current"] = "running", step
                    job = studio.run_job(project, body, own=True)
                    entry["jobs"].append(job["id"])
                    run["job"] = job["id"]
                job = wait(studio, job["id"])
                with studio.lock:
                    run["job"] = None
                    if job["status"] != "succeeded":
                        stopped = job["status"] == "cancelled" or run["status"] == "stopping"
                        message = "" if stopped else (job.get("result") or {}).get("message") or (job["log"][-1] if job["log"] else "")
                        return finish(run, "stopped" if stopped else "failed", entry, message)
            with studio.lock:
                if not entry["jobs"]:
                    entry["status"] = "skipped"
                    continue
                d = studio.detail(project)
                if not d["ready"][step]:
                    return finish(run, "failed", entry, INCOMPLETE[step])
                entry["status"] = "done"
                # Finished, though some outputs are still marked outdated (for example by a new content summary).
                if stale := d["state"]["stale"].get(step):
                    entry["note"] = stale["reason"]
        with studio.lock:
            finish(run, "succeeded")
    except Exception as exc:
        with studio.lock:
            finish(run, "failed", entry, str(exc))
    finally:
        # Images queued while the run held the project start now.
        studio.start_queued(project)
