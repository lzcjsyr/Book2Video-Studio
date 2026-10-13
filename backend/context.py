"""Prepare the existing production prompts for interactive Claude Code CLI."""
import json
import sys
import uuid
from pathlib import Path


def motion_context(studio, style_id):
    from core.infra.hyperframes.skill_loader import (HYPERFRAMES_CLI_VERSION, STEP4_MAX_REFERENCE_READS,
        STEP4_MOTION_RECIPES, STEP4_OPTIONAL_REFERENCES)
    from core.prompts import _read_step4_style_section, _read_step4_palette
    root = studio.library_root / "skills/step4"
    skills = next(group["items"] for group in studio.library.catalog()["groups"] if group["id"] == "motion_skills")
    skill_context = studio.library.prompt("step4_skill_context.md", skills_root=str(root),
        hyperframes_version=HYPERFRAMES_CLI_VERSION, reference_limit=STEP4_MAX_REFERENCE_READS,
        recipe_paths="\n".join(f"  - {name}: {root / path}" for name, path in STEP4_MOTION_RECIPES),
        reference_paths="\n".join(f"- {name}: {root / path}" for name, path in STEP4_OPTIONAL_REFERENCES),
        skill_entries="\n".join(f"- name: {item['label']}\n  entry_path: {root / item['id'] / 'SKILL.md'}" for item in skills))
    style = studio.library.item("motion_styles", style_id)["value"]
    official = style.get("official_style", "Swiss Pulse");palette = style.get("palette", "dark-premium")
    inlined = ("references/visual-styles.md", f"palettes/{palette}.md")
    references = [item for item in style.get("references", []) if not item.split("#", 1)[0].endswith(inlined)]
    style_context = studio.library.prompt("step4_style_context.md", style_id=style_id,
        label=style.get("label", style_id), official_style=official, palette=palette, best_for=style.get("best_for", ""),
        style_definition=_read_step4_style_section(root, official), palette_definition=_read_step4_palette(root, palette),
        references="\n".join(f"- {item} -> {root / item.split('#', 1)[0]}" for item in references))
    return skill_context, style_context


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
    direct = False
    if step == "1":
        options = studio.writing_options(state)
        direct = options["mode"] == "direct"
        source = options["source"]
        if not direct and (not source or not Path(source).exists()):
            raise ValueError("Choose a source document at the top of Writing, or switch to direct writing.")
        skill = Path(studio.library.item("writing_skills", settings.get("step1", {}).get("agent_skill", "sample_writer"))["path"])
        prompt = (studio.library_root / "prompts/step1_agent.md").read_text()
        input_context = "(none)" if direct else source
        replacements = {"input_file": input_context, "output_json": str(project / "text/raw.json"), "text_dir": str(project / "text"), "skill_path": str(skill), "extra_requirements": body.get("instructions", "")}
        for key, value in replacements.items():
            prompt = prompt.replace("{" + key + "}", str(value))
        cwd = project
        scope = "Writing · manuscript"
        if direct:
            prompt = studio.library.prompt("step1_studio.md", "direct") + prompt
        prompt += "\n\n" + studio.library.prompt("step1_studio.md", "shared", project_dir=str(project),
            text_dir=str(project / "text"), output_json=str(project / "text/raw.json"),
            opening_quote_state="enabled" if settings.get("global", {}).get("opening_quote", True) else "disabled")
    elif step == "5":
        import editing
        if settings.get("step5", {}).get("assembly_method", "standard") != "agent":
            raise ValueError("Choose Claude Code editing in Assembly first.")
        assembly_edit, cwd, prompt = editing.prepare(studio, project, settings, session, revise=body.get("revise", False))
        scope = "Assembly · edit and render"
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
        style = studio.library.item("cover_styles", settings.get("step6", {}).get("cover_image_style", "cover01"))
        design = covers.prepare(project, settings, session, state.get("cover_design") if body.get("revise") else None, copy=(title, subtitle))
        cwd = project / design["folder"]
        scope = "Covers · HTML / Three.js"
        payload = {"title": title, "subtitle": subtitle, "source": raw.get("source_name", ""),
                   "manuscript": raw.get("content", ""), "style": style, "entries": design["entries"]}
        (cwd / "studio-payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        prompt = studio.library.prompt("step6_cover_agent.md", "main", design_dir=str(cwd), entries_json=json.dumps(design['entries'], ensure_ascii=False, indent=2))
        if body.get("revise"):
            prompt += studio.library.prompt("step6_cover_agent.md", "revise")
        launch_design = design
    else:
        from core.infra.hyperframes.runtime import HYPERFRAMES_CLI_VERSION
        script = json.loads((project / "text/script.json").read_text())
        summary = json.loads((project / "text/mini_summary.json").read_text())
        indices = body.get("indices") or [s["index"] for s in script["segments"]]
        chosen = [s for s in script["segments"] if s["index"] in indices]
        if not chosen:
            raise ValueError("Select at least one segment.")
        size = str(settings.get("step4", {}).get("image_size", "1280x720")).replace("×", "x").lower()
        width, height = map(int, size.split("x"))
        style = settings.get("step4", {}).get("hyperframes_style_preset", "data_driven")
        skill_context, style_context = motion_context(studio, style)
        prompt_parts = []
        for segment in chosen:
            from server import duration
            audio = studio.asset(project, "voice", f"voice_{segment['index']}", [".wav", ".mp3"])
            seconds = duration(audio)
            if not seconds:
                raise ValueError(f"Segment {segment['index']} needs a recording before animated visual production.")
            # The motion clip this session makes or revises; the segment's other clips and stills are not touched.
            work = body["folders"][segment["index"]]
            work.mkdir(parents=True, exist_ok=True)
            payload = {"segmentIndex": segment["index"], "index": segment["index"], "title": segment.get("title") or f"Segment {segment['index']}", "content": segment["content"], "durationSeconds": seconds, "width": width, "height": height, "stylePreset": style, "descriptionSummary": summary.get("summary", "")}
            (work / "studio-payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            template = (studio.library_root / "prompts/step4_hyperframes_agent.md").read_text()
            part = template.format(skill_path_context=skill_context, style_preset=style, style_context=style_context, payload_json=json.dumps(payload, ensure_ascii=False, indent=2), target_index_html_path=str(work / "index.html"))
            part = studio.library.prompt("step4_studio.md", "segment", agent_prompt=part,
                segment_index=segment["index"], work_dir=str(work), snapshots_dir=str(work / "snapshots"))
            if not (work / "index.html").is_file():
                part += studio.library.prompt("step4_studio.md", "new")
            prompt_parts.append(part)
        targets = [body["folders"][s["index"]] / "index.html" for s in chosen]
        if len(chosen) == 1:
            prompt = studio.library.prompt("step4_studio.md", "single", segments=prompt_parts[0])
        else:
            tasks = "\n".join(studio.library.prompt("step4_studio.md", "target", task_number=n,
                segment_index=segment["index"], target_path=str(target)).strip()
                for n, (segment, target) in enumerate(zip(chosen, targets), 1))
            parts = "\n\n".join(studio.library.prompt("step4_studio.md", "task", segment_index=segment["index"],
                task_number=n, segment_count=len(chosen), agent_prompt=part)
                for n, (segment, part) in enumerate(zip(chosen, prompt_parts), 1))
            prompt = studio.library.prompt("step4_studio.md", "multi", segment_count=len(chosen), tasks=tasks, segments=parts)
        prompt += "\n\n" + studio.library.prompt("step4_studio.md", "finish", hyperframes_version=HYPERFRAMES_CLI_VERSION)
        # One segment: work inside its folder. Several: start beside all of them; each section names its folder.
        cwd = targets[0].parent if len(chosen) == 1 else project / "images/takes"
        scope = f"Visuals · {len(chosen)} selected segment(s)"
    prompt_path = context_dir / "context.md"
    context_dir.mkdir(parents=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    directories = [str(project), str(studio.library_root / "skills")]
    if step == "1" and not direct:
        directories.append(str(Path(source) if Path(source).is_dir() else Path(source).parent))
    # Projects can live anywhere; the launch names the project by its service id.
    launch = {"id": session, "project": studio.project_id(project), "step": step, "cwd": str(cwd), "context": str(prompt_path), "scope": scope,
              "args": ["--session-id", session, "--append-system-prompt-file", str(prompt_path), "--add-dir", *dict.fromkeys(directories)]}
    if step == "5":
        launch["assembly_edit"] = assembly_edit
    if step == "6":
        launch["cover_design"] = launch_design
    # Keep the prompt in a file for history; no API credentials are written here.
    (context_dir / "launch.json").write_text(json.dumps(launch, ensure_ascii=False, indent=2))
    return launch
