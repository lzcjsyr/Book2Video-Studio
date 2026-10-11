"""Prepare the existing production prompts for interactive Claude Code CLI."""
import json
import importlib
import sys
import uuid
from pathlib import Path


def prepare(studio, project, body):
    step = body["step"]
    if step not in {"1", "4"}:
        raise ValueError("Interactive agents are available for Writing and Visuals.")
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
        input_context = "No input document (direct writing mode; the user supplies the topic in this terminal)" if direct else source
        replacements = {"input_file": input_context, "output_json": str(project / "text/raw.json"), "text_dir": str(project / "text"), "skill_path": str(skill), "extra_requirements": body.get("instructions", "Follow the user's instructions in this interactive terminal.")}
        for key, value in replacements.items():
            prompt = prompt.replace("{" + key + "}", str(value))
        cwd = project
        scope = "Writing · manuscript"
        if direct:
            prompt = "DIRECT WRITING MODE — no source document is selected. Wait for the user to give their topic and writing requirements in the interactive terminal before drafting. Do not look for an input manuscript, use any previously selected source, or treat the input placeholder below as a file path. Apply the selected writing skill and template where relevant, adapting source-reading phases to the user's brief. Ask for missing topic information in the terminal.\n\n" + prompt
        prompt += "\n\nRequired Studio output contract (also applies in direct writing mode): save valid UTF-8 JSON to " + str(project / "text/raw.json") + ". Include source_name (string: source title or user's topic), content (nonempty spoken manuscript string), total_length (integer character count), video_titles, cover_titles, cover_subtitles, and golden_quotes (arrays of separate strings). Do not combine candidate titles or quotes into one newline-delimited string. Preserve the existing manuscript until the user requests a revision. The application must be able to review and edit this JSON and use it for the later generation steps.\n"
        prompt += "\nStudio file locations (these override any other path in the skill, its references or scripts): your working directory is the project folder " + str(project) + ". Save every intermediate file (_extract.txt/.md, _coverage_ledger.json, _angle_brief.json, _draft_v1.txt, _draft_final.txt, _revision_audit.json, _title_quote_candidates.json and similar) in " + str(project / "text") + ". Wherever the skill or a reference says output/<project>/text/..., use " + str(project / "text") + "/... instead, and never create an output/ folder. The only place for the final manuscript is " + str(project / "text/raw.json") + ". Use the `python` command for text extraction and for any validation script: it is bundled with PyMuPDF, pdfplumber, ebooklib, mobi, python-docx and PyYAML. Do not install packages or use other Python installations.\n"
        opening_enabled = settings.get("global", {}).get("opening_quote", True)
        prompt += "\nOpening quote playback is " + ("enabled" if opening_enabled else "disabled") + ". Store any opening quote candidates separately in golden_quotes; do not prepend them to content. When enabled, the app plays the selected quote before the body. When disabled, the app starts with the body and may retain unused candidates. The GUI controls this project setting.\n"
    else:
        from core.infra.hyperframes.skill_loader import build_step4_hyperframes_skill_path_context
        import core.prompts as production_prompts
        # The service survives library edits; each new agent must get current styles.
        production_prompts = importlib.reload(production_prompts)
        build_step4_hyperframes_style_context = production_prompts.build_step4_hyperframes_style_context
        script = json.loads((project / "text/script.json").read_text())
        summary = json.loads((project / "text/mini_summary.json").read_text())
        indices = body.get("indices") or [s["index"] for s in script["segments"]]
        chosen = [s for s in script["segments"] if s["index"] in indices]
        if not chosen:
            raise ValueError("Select at least one segment.")
        size = str(settings.get("step4", {}).get("image_size", "1280x720")).replace("×", "x").lower()
        width, height = map(int, size.split("x"))
        style = settings.get("step4", {}).get("hyperframes_style_preset", "data_driven")
        studio.library.item("motion_styles", style)
        prompt_parts = []
        for segment in chosen:
            from server import duration
            audio = studio.asset(project, "voice", f"voice_{segment['index']}", [".wav", ".mp3"])
            seconds = duration(audio)
            if not seconds:
                raise ValueError(f"Segment {segment['index']} needs a recording before animated visual production.")
            work = project / "images/hyperframes" / f"segment_{segment['index']}"
            work.mkdir(parents=True, exist_ok=True)
            payload = {"segmentIndex": segment["index"], "index": segment["index"], "title": segment.get("title") or f"Segment {segment['index']}", "content": segment["content"], "durationSeconds": seconds, "width": width, "height": height, "stylePreset": style, "descriptionSummary": summary.get("summary", "")}
            (work / "studio-payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            template = (studio.library_root / "prompts/step4_hyperframes_agent.md").read_text()
            prompt_parts.append(template.format(skill_path_context=build_step4_hyperframes_skill_path_context(studio.library_root / "skills/step4/hyperframes"), style_preset=style, style_context=build_step4_hyperframes_style_context(style, skills_root=studio.library_root / "skills/step4"), payload_json=json.dumps(payload, ensure_ascii=False, indent=2), target_index_html_path=str(work / "index.html")))
        targets = [project / "images/hyperframes" / f"segment_{s['index']}" / "index.html" for s in chosen]
        if len(chosen) == 1:
            prompt = "The selected segment is your task. Preserve its existing composition until you have revised it. The application will render your saved HTML.\n\n" + prompt_parts[0]
        else:
            # Each part says "the current single segment"; without a task list a model treats the last part as the whole job.
            tasks = "\n".join(f"{n}. Segment {s['index']} → `{t}`" for n, (s, t) in enumerate(zip(chosen, targets), 1))
            prompt = (f"You have {len(chosen)} segments to produce, one after another. Each is an independent task with its own section below; "
                      "inside a section, \"the current segment\" means that section's segment. Finish each segment completely (write, check, fix) "
                      "before starting the next, and you are done only when every file in this list exists:\n\n" + tasks +
                      "\n\nPreserve existing compositions until you have revised them. The application will render your saved HTML.\n\n" +
                      "\n\n".join(f"# Segment {s['index']} (task {n} of {len(chosen)})\n\n{part}\n\n# End of segment {s['index']}"
                                    for n, (s, part) in enumerate(zip(chosen, prompt_parts), 1)))
        prompt += "\n\nThis application bundles the pinned HyperFrames CLI (0.7.10) as the `hyperframes` command, with its Chrome and FFmpeg. Wherever these instructions or skills say `npx hyperframes ...` or `npx --yes hyperframes@0.7.10 ...`, run `hyperframes ...` instead.\n"
        cwd = project / "images/hyperframes" / f"segment_{chosen[0]['index']}"
        scope = f"Visuals · {len(chosen)} selected segment(s)"
    prompt += "\n\nYou are this step's main production agent. The application manages the project and later steps. Work interactively with the user. The GUI is the primary text editor; raw.json and script.json are canonical. Do not read an older DOCX over newer JSON. Follow the existing skill output contracts.\n"
    prompt_path = context_dir / "context.md"
    context_dir.mkdir(parents=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    directories = [str(project), str(studio.library_root / "skills")]
    if step == "1" and not direct:
        directories.append(str(Path(source) if Path(source).is_dir() else Path(source).parent))
    # Projects can live anywhere; the launch names the project by its service id.
    launch = {"id": session, "project": studio.project_id(project), "step": step, "cwd": str(cwd), "context": str(prompt_path), "scope": scope,
              "args": ["--session-id", session, "--append-system-prompt-file", str(prompt_path), "--add-dir", *dict.fromkeys(directories)]}
    # Keep the prompt in a file for history; no API credentials are written here.
    (context_dir / "launch.json").write_text(json.dumps(launch, ensure_ascii=False, indent=2))
    return launch
