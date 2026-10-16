"""Prepare the existing production prompts for interactive Claude Code CLI."""
import json
import sys
import uuid
from pathlib import Path


def motion_context(studio, style_id):
    from core.infra.motion.skill_loader import (STEP4_MAX_REFERENCE_READS,
        STEP4_MOTION_RECIPES, STEP4_OPTIONAL_REFERENCES)
    from core.prompts import _read_step4_style_section, _read_step4_palette
    root = studio.library_root / "skills/step4"
    skills = next(group["items"] for group in studio.library.catalog()["groups"] if group["id"] == "motion_skills")
    skill_context = studio.library.prompt("step4_skill_context.md", skills_root=str(root),
        reference_limit=STEP4_MAX_REFERENCE_READS,
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
    from core.infra.ai.task_context import write_task_context
    direct = False
    skill_directories = []
    if step == "1":
        options = studio.writing_options(state)
        direct = options["mode"] == "direct"
        source = options["source"]
        if not direct and (not source or not Path(source).exists()):
            raise ValueError("Choose a source document at the top of Writing, or switch to direct writing.")
        writing_skill = options["agent_skill"]
        input_context = "(none)" if direct else source
        if writing_skill:
            skill = Path(studio.library.item("writing_skills", writing_skill)["path"])
            prompt = (studio.library_root / "prompts/step1_agent.md").read_text()
            skill_directories.append(skill.parent)
        else:
            skill = ""
            prompt = studio.library.prompt("step1_no_skill.md")
        replacements = {"input_file": input_context, "output_json": str(project / "text/raw.json"), "text_dir": str(project / "text"), "skill_path": str(skill), "extra_requirements": body.get("instructions", "")}
        for key, value in replacements.items():
            prompt = prompt.replace("{" + key + "}", str(value))
        cwd = project
        scope = "Writing · manuscript"
        task = dict(objective='仅按来源和用户要求写作；不生成分段或素材。',
            inputs=[project/'text/raw.json', *([source] if not direct else []), *([skill] if writing_skill else [])], optional_inputs=[project/'text/raw.json'],
            writable=[project/'text'], protected=[project/'text/script.json', project/'text/mini_summary.json', project/'images', project/'voice', studio.engine, studio.library_root, *([source] if not direct else [])],
            done=['有效 raw.json，正文和候选元数据符合 Studio 契约；用户未要求时保留现有正文。'],
            facts={'mode': options['mode'], 'skill': writing_skill or None})
        if direct:
            prompt = studio.library.prompt("step1_studio.md", "direct") + prompt
        prompt += "\n\n" + studio.library.prompt("step1_studio.md", "shared", project_dir=str(project),
            text_dir=str(project / "text"), output_json=str(project / "text/raw.json"),
            opening_quote_state="已启用" if settings.get("global", {}).get("opening_quote", True) else "已禁用")
    elif step == "5":
        import editing
        if settings.get("step5", {}).get("assembly_method", "standard") != "agent":
            raise ValueError("Choose Claude Code editing in Assembly first.")
        assembly_edit, cwd, prompt = editing.prepare(studio, project, settings, session, revise=body.get("revise", False))
        scope = "Assembly · edit and render"
        skill_directories = [studio.library_root/'skills/step5'/assembly_edit['skill'],
                             studio.library_root/'skills/step4/motion-core']
        task = dict(objective='仅精剪当前项目，检查后通过 studio_edit.py publish 发布成片。',
            inputs=[cwd/'studio-payload.json', cwd/'plan.json', cwd/'EDITING.md'], writable=[cwd, project/'final_video.mp4', project/'final_video.srt', project/'final_video.qa.json', project/'final_video_3x4.mp4'],
            protected=[project/'text', project/'voice', project/'images', cwd/'studio-payload.json', cwd/'studio_edit.py', cwd/'vendor', studio.engine, studio.library_root],
            done=['完整主片及所需字幕/3:4 通过检查，candidate.qa.json verified，通过 publish 写入输出；报告未听审等限制。'],
            facts={'skill': assembly_edit['skill'], 'publishing': 'Only through python studio_edit.py publish; do not replace final exports by hand.'})
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
        summary = read_json(project / "text/mini_summary.json", {})
        manuscript = project / ("text/raw.json" if raw.get("content") else "text/script.json")
        payload = {"title": title, "subtitle": subtitle, "source": raw.get("source_name", ""),
                   "summary": summary.get("summary", ""), "manuscript_path": str(manuscript),
                   "style_id": style['id'], "style": style['value'], "entries": design["entries"]}
        (cwd / "studio-payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        prompt = studio.library.prompt("step6_cover_agent.md", "main", design_dir=str(cwd), entries_json=json.dumps(design['entries'], ensure_ascii=False, indent=2))
        if body.get("revise"):
            prompt += studio.library.prompt("step6_cover_agent.md", "revise")
        launch_design = design
        task = dict(objective='仅设计当前封面候选并检查预览；最终 PNG 由应用在会话结束后生成。',
            inputs=[cwd/'studio-payload.json', cwd/'design.json', manuscript], optional_inputs=[manuscript], writable=[cwd],
            protected=[project/'text', project/'voice', project/'images', cwd/'studio-payload.json', cwd/'design.json', cwd/'render.py', cwd/'vendor', studio.engine, studio.library_root],
            done=['每个指定 HTML 都存在，全部 previews PNG 已检查；保留原封面，告知用户结束会话以生成最终候选。'],
            facts={'entries': design['entries'], 'revise': bool(body.get('revise'))})
    else:
        from core.infra.motion.runtime import prepare as prepare_clip
        script = json.loads((project / "text/script.json").read_text())
        summary = json.loads((project / "text/mini_summary.json").read_text())
        indices = body.get("indices") or [s["index"] for s in script["segments"]]
        chosen = [s for s in script["segments"] if s["index"] in indices]
        if not chosen:
            raise ValueError("Select at least one segment.")
        size = str(settings.get("step4", {}).get("image_size", "1280x720")).replace("×", "x").lower()
        width, height = map(int, size.split("x"))
        style = settings.get("step4", {}).get("motion_style_preset", "data_driven")
        skill_context, style_context = motion_context(studio, style)
        prompt_parts, payloads = [], []
        for segment in chosen:
            from server import duration
            audio = studio.asset(project, "voice", f"voice_{segment['index']}", [".wav"])
            seconds = duration(audio)
            if not seconds:
                raise ValueError(f"Segment {segment['index']} needs a recording before animated visual production.")
            # The motion clip this session makes or revises; the segment's other clips and stills are not touched.
            work = body["folders"][segment["index"]]
            work.mkdir(parents=True, exist_ok=True)
            payload = {"segmentIndex": segment["index"], "index": segment["index"], "title": segment.get("title") or f"Segment {segment['index']}", "content": segment["content"], "durationSeconds": seconds, "width": width, "height": height, "stylePreset": style, "descriptionSummary": summary.get("summary", "")}
            from core.infra.motion.artifacts import stamp
            payload['fps']=int(settings.get('step4',{}).get('motion_render_fps',30))
            payload['sourceGuard']={'script':str(project/'text/script.json'),'audio':str(audio),'audioStamp':stamp(audio)}
            prepare_clip(work, payload)
            payloads.append(payload)
            part = studio.library.prompt("step4_studio.md", "segment", agent_prompt=f"先读取本段输入 `{work / 'studio-payload.json'}`，只按本段叙述和时长制作。上方共享规则适用于每个段落。",
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
        # Technical, style and reference instructions occur once per session,
        # rather than once per selected segment. The detailed payloads stay local.
        compact = [{k: p[k] for k in ('segmentIndex', 'durationSeconds', 'width', 'height', 'fps')} for p in payloads]
        shared = (studio.library_root / "prompts/step4_motion_agent.md").read_text().format(
            skill_path_context=skill_context, style_preset=style, style_context=style_context,
            payload_json=json.dumps({'descriptionSummary': summary.get('summary', ''), 'segments': compact}, ensure_ascii=False, indent=2),
            target_index_html_path='index.html')
        prompt = "共享规则只写一次；下方输入 JSON 是任务概览，不是单个 clipPayload。逐段先读取该工作目录的 studio-payload.json，以其中单段数据构建 HTML；不同段落不能混用内容或时长。\n\n" + shared + "\n\n" + prompt
        prompt += "\n\n" + studio.library.prompt("step4_studio.md", "finish")
        # One segment: work inside its folder. Several: start beside all of them; each section names its folder.
        cwd = targets[0].parent if len(chosen) == 1 else project / "images/takes"
        scope = f"Visuals · {len(chosen)} selected segment(s)"
        works = [target.parent for target in targets]
        skill_directories = [studio.library_root/'skills/step4']
        task = dict(objective='只制作所选段落的 HTML 动画；逐段预览、修复、渲染验证。',
            inputs=[work/'studio-payload.json' for work in works], writable=works,
            protected=[project/'text', project/'voice', studio.engine, studio.library_root,
                       *[work/name for work in works for name in ['studio-payload.json', 'render.py', 'vendor']]],
            done=['每段 video.mp4 与 HTML 一致且 render-report.json verified；不改其他分段或图片版本。'],
            facts={'selected_segments': [s['index'] for s in chosen], 'fps': payloads[0]['fps']})

    task_path = context_dir / 'task.json'
    prompt = write_task_context(task_path, stage=step, cwd=cwd, **task) + '\n' + prompt
    prompt_path = context_dir / "context.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    directories = [str(project), *map(str, skill_directories)]
    if step == "1" and not direct:
        directories.append(str(Path(source) if Path(source).is_dir() else Path(source).parent))
    # Projects can live anywhere; the launch names the project by its service id.
    launch = {"id": session, "project": studio.project_id(project), "step": step, "cwd": str(cwd), "context": str(prompt_path), "task": str(task_path), "context_chars": len(prompt), "scope": scope,
              "args": ["--session-id", session, "--append-system-prompt-file", str(prompt_path), "--add-dir", *dict.fromkeys(directories)]}
    if step == "5":
        launch["assembly_edit"] = assembly_edit
    if step == "6":
        launch["cover_design"] = launch_design
    # Keep the prompt in a file for history; no API credentials are written here.
    (context_dir / "launch.json").write_text(json.dumps(launch, ensure_ascii=False, indent=2))
    return launch
