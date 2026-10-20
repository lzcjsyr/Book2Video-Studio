"""Prepare a Claude Code session: what the agent reads comes from the library templates.

Each session's prompt is agent_rules.md's rules followed by its step's template (step1_agent.md,
step4_motion_agent.md, step5_editing_agent.md or step6_cover_agent.md); the code only fills in values.
"""
import json
import sys
import uuid
from pathlib import Path


def prepare(studio, project, body):
    step = body["step"]
    if step not in {"1", "4", "5", "6"}:
        raise ValueError("Interactive agents are available for Writing, Visuals, Assembly and Covers.")
    state = studio.state(project)
    settings = studio.defaults()
    for key, value in state.get("settings", {}).items():
        settings.setdefault(key, {}).update(value)
    session = str(uuid.uuid4())
    context_dir = project / ".studio/agent" / session
    sys.path.insert(0, str(studio.engine)) if str(studio.engine) not in sys.path else None
    from core.infra.ai.task_context import write_task_context
    library = studio.library
    part = lambda name, section, **values: library.prompt(name, section, **values).strip()
    direct = False
    skill_directories = []
    if step == "1":
        options = studio.writing_options(state)
        direct = options["mode"] == "direct"
        source = options["source"]
        if not direct and (not source or not Path(source).exists()):
            raise ValueError("Choose a source document at the top of Writing, or switch to direct writing.")
        writing_skill = options["agent_skill"]
        skill = Path(library.item("writing_skills", writing_skill)["path"]) if writing_skill else None
        if skill:
            skill_directories.append(skill.parent)
        name = "step1_agent.md"
        prompt = library.prompt(name, "main",
            source=part(name, "direct") if direct else part(name, "source", input_file=source),
            skill=part(name, "skill", skill_path=skill) if skill else part(name, "no_skill"),
            extra_requirements=body.get("instructions", "").strip() or "无",
            output_json=project / "text/raw.json", project_dir=project, text_dir=project / "text",
            opening_quote_state="已启用" if settings.get("global", {}).get("opening_quote", True) else "已禁用")
        cwd = project
        scope = "Writing · manuscript"
        task = dict(inputs=[project/'text/raw.json', *([source] if not direct else []), *([skill] if skill else [])], optional_inputs=[project/'text/raw.json'],
            writable=[project/'text'], protected=[project/'text/script.json', project/'text/mini_summary.json', project/'images', project/'voice', studio.engine, studio.library_root, *([source] if not direct else [])],
            facts={'mode': options['mode'], 'skill': writing_skill or None})
    elif step == "5":
        import editing
        if settings.get("step5", {}).get("assembly_method", "standard") != "agent":
            raise ValueError("Choose Claude Code editing in Assembly first.")
        assembly_edit, work, prompt = editing.prepare(studio, project, settings, session, revise=body.get("revise", False))
        scope = "Assembly · edit and render"
        skill_directories = [studio.library_root/'skills/step5'/assembly_edit['skill']]
        task = dict(inputs=[work/'materials.json'], writable=[work, project/'final_video.mp4', project/'final_video.srt'],
            protected=[project/'text', project/'voice', project/'images', work/'materials.json', work/'studio_render.py', studio.engine, studio.library_root],
            facts={'style': assembly_edit['skill']})
        # Claude Code asks once per folder whether to trust it; starting in the project folder (as Writing
        # does) means one answer covers every edit, instead of a question for each new edit folder.
        cwd = project
    elif step == "6":
        import covers
        from server import cover_copy, read_json
        if settings.get("step6", {}).get("cover_method", "api") != "agent":
            raise ValueError("Choose Claude Code + HTML / Three.js in Cover setup first.")
        raw = read_json(project / "text/raw.json", {})
        script = read_json(project / "text/script.json", {})
        # The manuscript is authoritative; segmentation may not exist yet.
        title, subtitle = cover_copy(raw if raw.get("content") else script)
        if not title:
            raise ValueError("Choose a cover title in Writing before designing covers.")
        style = library.item("cover_styles", settings.get("step6", {}).get("cover_image_style", "cover01"))
        design = covers.prepare(project, settings, session, state.get("cover_design") if body.get("revise") else None, copy=(title, subtitle))
        cwd = project / design["folder"]
        scope = "Covers · HTML / Three.js"
        summary = read_json(project / "text/mini_summary.json", {})
        manuscript = project / ("text/raw.json" if raw.get("content") else "text/script.json")
        payload = {"title": title, "subtitle": subtitle, "source": raw.get("source_name", ""),
                   "summary": summary.get("summary", ""), "manuscript_path": str(manuscript),
                   "style_id": style['id'], "style": style['value'], "entries": design["entries"]}
        (cwd / "studio-payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        prompt = library.prompt("step6_cover_agent.md", "main", title=title, subtitle=subtitle, design_dir=cwd,
            revise=part("step6_cover_agent.md", "revise") + "\n\n" if body.get("revise") else "",
            covers="\n".join(f"- {e['html']}：{e['width']}×{e['height']}" for e in design["entries"]))
        launch_design = design
        task = dict(inputs=[cwd/'studio-payload.json', cwd/'design.json', manuscript], optional_inputs=[manuscript], writable=[cwd],
            protected=[project/'text', project/'voice', project/'images', cwd/'studio-payload.json', cwd/'design.json', cwd/'render.py', cwd/'vendor', studio.engine, studio.library_root],
            facts={'entries': design['entries'], 'revise': bool(body.get('revise'))})
    else:
        from core.infra.motion.runtime import prepare as prepare_clip
        from core.infra.motion.skill_loader import STEP4_MAX_REFERENCE_READS, STEP4_MOTION_RECIPES, STEP4_OPTIONAL_REFERENCES
        from core.prompts import _read_step4_style_section, _read_step4_palette
        from server import duration
        script = json.loads((project / "text/script.json").read_text())
        summary = json.loads((project / "text/mini_summary.json").read_text())
        indices = body.get("indices") or [s["index"] for s in script["segments"]]
        chosen = [s for s in script["segments"] if s["index"] in indices]
        if not chosen:
            raise ValueError("Select at least one segment.")
        size = str(settings.get("step4", {}).get("image_size", "1280x720")).replace("×", "x").lower()
        width, height = map(int, size.split("x"))
        fps = int(settings.get("step4", {}).get("motion_render_fps", 30))
        name = "step4_motion_agent.md"
        segments, works = [], []
        for segment in chosen:
            audio = studio.asset(project, "voice", f"voice_{segment['index']}", [".wav"])
            seconds = duration(audio)
            if not seconds:
                raise ValueError(f"Segment {segment['index']} needs a recording before animated visual production.")
            # The motion clip this session makes or revises; the segment's other clips and stills are not touched.
            work = body["folders"][segment["index"]]
            work.mkdir(parents=True, exist_ok=True)
            from core.infra.motion.artifacts import stamp
            payload = {"segmentIndex": segment["index"], "index": segment["index"], "title": segment.get("title") or f"Segment {segment['index']}",
                       "content": segment["content"], "durationSeconds": seconds, "width": width, "height": height, "fps": fps,
                       "stylePreset": settings.get("step4", {}).get("motion_style_preset", "data_driven"), "descriptionSummary": summary.get("summary", ""),
                       "sourceGuard": {"script": str(project/"text/script.json"), "audio": str(audio), "audioStamp": stamp(audio)}}
            prepare_clip(work, payload)
            segments.append(part(name, "segment", segment_index=segment["index"], duration=f"{seconds:g}", work_dir=work,
                                 new="" if (work / "index.html").is_file() else part(name, "new")))
            works.append(work)
        # The chosen style, its palette and the reference files, as the template shows them.
        root = studio.library_root / "skills/step4"
        style_id = settings.get("step4", {}).get("motion_style_preset", "data_driven")
        style = library.item("motion_styles", style_id)["value"]
        palette = style.get("palette", "dark-premium")
        inlined = ("references/visual-styles.md", f"palettes/{palette}.md")
        references = [item for item in style.get("references", []) if not item.split("#", 1)[0].endswith(inlined)]
        skills = next(group["items"] for group in library.catalog()["groups"] if group["id"] == "motion_skills")
        prompt = library.prompt(name, "main", segment_count=len(chosen), segments="\n\n".join(segments),
            style_label=style.get("label", style_id), style_best_for=style.get("best_for", ""),
            style_definition=_read_step4_style_section(root, style.get("official_style", "Swiss Pulse")),
            palette=palette, palette_definition=_read_step4_palette(root, palette),
            style_references="\n".join(f"- {root / item.split('#', 1)[0]}" for item in references) or "- 无",
            reference_limit=STEP4_MAX_REFERENCE_READS,
            recipe_paths="\n".join(f"- {label}: {root / path}" for label, path in STEP4_MOTION_RECIPES),
            reference_paths="\n".join(f"- {label}: {root / path}" for label, path in STEP4_OPTIONAL_REFERENCES),
            skill_entries="\n".join(f"- {item['label']}: {root / item['id'] / 'SKILL.md'}" for item in skills))
        # One segment: work inside its folder. Several: start beside all of them; each section names its folder.
        cwd = works[0] if len(works) == 1 else project / "images/takes"
        scope = f"Visuals · {len(chosen)} selected segment(s)"
        skill_directories = [studio.library_root/'skills/step4']
        task = dict(inputs=[work/'studio-payload.json' for work in works], writable=works,
            protected=[project/'text', project/'voice', studio.engine, studio.library_root,
                       *[work/name for work in works for name in ['studio-payload.json', 'render.py', 'vendor']]],
            facts={'selected_segments': [s['index'] for s in chosen], 'fps': fps})

    task_path = write_task_context(context_dir / 'task.json', stage=step, cwd=cwd, **task)
    prompt = part("agent_rules.md", "rules", task_file=task_path) + "\n\n" + prompt
    prompt_path = context_dir / "context.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    directories = [str(project), *map(str, skill_directories)]
    if step == "1" and not direct:
        directories.append(str(Path(source) if Path(source).is_dir() else Path(source).parent))
    # In a terminal, Assembly starts working at once: its permissions, then its first message between single-value
    # options and the variadic --add-dir. A session without a terminal gets its own tools and message (headless.py).
    terminal = step == "5" and not body.get("headless")
    # Projects can live anywhere; the launch names the project by its service id.
    launch = {"id": session, "project": studio.project_id(project), "step": step, "cwd": str(cwd), "context": str(prompt_path), "task": str(task_path), "context_chars": len(prompt), "scope": scope,
              "args": [*(["--allowedTools", *editing.ALLOWED_TOOLS] if terminal else []),
                       "--session-id", session, "--append-system-prompt-file", str(prompt_path),
                       *([part("agent_rules.md", "start")] if terminal else []),
                       "--add-dir", *dict.fromkeys(directories)]}
    if step == "5":
        launch["assembly_edit"] = assembly_edit
    if step == "6":
        launch["cover_design"] = launch_design
    # Keep the prompt in a file for history; no API credentials are written here.
    (context_dir / "launch.json").write_text(json.dumps(launch, ensure_ascii=False, indent=2))
    return launch
