"""One isolated process per generation job; uses the CLI's actual pipeline."""
from __future__ import annotations
import inspect
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path


def call(fn, values):
    return fn(**{key: values[key] for key in inspect.signature(fn).parameters if key in values})


def check_image_model(server, model):
    """A model left over from another provider would only fail at the provider (Google answers 404)."""
    from core.config import Config
    try:
        Config.validate_model_provider_pair("image", server, model)
    except ValueError:
        raise ValueError(f"The image model {model} does not work with {server}. Choose a matching model in Settings → Production.") from None


def read_script(project):
    for name in ["script.json", "raw.json"]:
        if (project / "text" / name).is_file():
            return json.loads((project / "text" / name).read_text())
    return {}


def panel_preview(project, params, output):
    """A still of the 3:4 copy: a frame of the landscape video, else of the first segment's visual."""
    import shutil
    import tempfile
    from PIL import Image
    from core.domain.composer import VideoComposer, three_by_four_panel_options
    from core.infra.media.three_by_four import render_layout_preview
    from core.config import config
    width, height = (int(n) for n in str(params.get("video_size") or params.get("image_size") or "1280x720").lower().replace("×", "x").split("x"))
    with tempfile.TemporaryDirectory(prefix="studio-3x4-") as folder:
        frame = Path(folder) / "frame.png"
        master = project / "final_video.mp4"
        segment_visuals = sorted((f for f in (project / "images").glob("segment_*.*") if f.stem.split("_")[-1].isdigit()),
                                 key=lambda f: int(f.stem.split("_")[-1])) if (project / "images").is_dir() else []
        source = None
        ffmpeg = shutil.which("ffmpeg")
        if master.is_file() and ffmpeg:
            from server import duration
            moment = max(0.0, (duration(master) or 0) * 0.35)
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{moment:.2f}", "-i", str(master), "-frames:v", "1", str(frame)], check=True)
            source = "video"
        else:
            for visual in segment_visuals:
                if visual.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    image = Image.open(visual).convert("RGB")
                elif visual.suffix.lower() in {".mp4", ".mov", ".webm"} and ffmpeg:
                    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(visual), "-frames:v", "1", str(frame)], check=True)
                    image = Image.open(frame).convert("RGB")
                else:
                    continue
                # Fill the export frame the way assembly does: cover, then centre-crop.
                scale = max(width / image.width, height / image.height)
                image = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))))
                left, top = (image.width - width) // 2, (image.height - height) // 2
                image.crop((left, top, left + width, top + height)).save(frame)
                source = "segment"
                break
            if not source:
                Image.new("RGB", (width, height), (40, 44, 50)).save(frame)
                source = "placeholder"
        font, index = VideoComposer().resolve_subtitle_font(getattr(config, "SUBTITLE_FONT_FAMILY", "auto"), int(getattr(config, "SUBTITLE_FONT_TTC_INDEX", 0) or 0))
        render_layout_preview(frame, project / output, read_script(project), panel=three_by_four_panel_options(),
                              font_path=font, font_ttc_index=index)
    return {"success": True, "source": source}


def run(request):
    engine = Path(request["engine"])
    project = Path(request["project"])
    if request["step"] == "6" and request.get("mode") == "cover_render":
        from covers import render
        return render(project, request["cover_design"])
    sys.path.insert(0, str(engine))
    # The engine may be read-only; anything written relative to the working folder stays in the project.
    os.chdir(project)
    import yaml
    from core.config import apply_yaml_config, get_generation_params, config
    merged = yaml.safe_load((engine / "config.example.yaml").read_text())
    from server import engine_settings
    for section, fields in engine_settings(request.get("settings", {})).items():
        merged.setdefault(section, {}).update(fields)
    # A layout preview or a cover job may run beside another job; each keeps its own configuration.
    runtime = project / (".studio/preview-config.yaml" if request.get("mode") == "preview" else
                         ".studio/cover-config.yaml" if request["step"] == "6" else ".studio/job-config.yaml")
    runtime.write_text(yaml.safe_dump(merged, allow_unicode=True), encoding="utf-8")
    apply_yaml_config(runtime)
    params = get_generation_params(runtime)
    from core.pipeline import steps
    step = request["step"]
    indices = request.get("indices")
    if request.get("summary"):
        # Every visual follows a shared direction written from the manuscript (engine step 2).
        print("Generating the content summary…", flush=True)
        result = call(steps.run_step_2, {"project_output_dir": str(project), "llm_server": params["llm_server_step2"],
                                         "llm_model": params["llm_model_step2"], "llm_base_url": params["llm_base_url_step2"]})
        if not result.get("success"):
            return result
        if request.get("mode") == "summary":
            return {"success": True, "message": "Content summary generated."}
    if step == "4" and request.get("mode") == "segments":
        from visual_generation import run as generate_visuals
        return generate_visuals(request, params, steps, call)
    if step == "4" and request.get("mode") == "render":
        from core.infra.motion.renderer import render_clip
        script = json.loads((project / "text/script.json").read_text())
        selected = indices or [s["index"] for s in script["segments"] if s.get("visualizer") == "hf"]
        if not selected:
            raise ValueError("Select an animated segment to render.")
        folders = {int(k): Path(v) for k, v in request.get("folders", {}).items()}
        for idx in selected:
            # The motion clip to render: Studio names its folder (images/takes/segment_N/motion-…).
            work = folders.get(idx)
            if not work or not (work / "index.html").exists() or not (work / "studio-payload.json").exists():
                raise ValueError(f"Segment {idx} has no saved composition. Activate Claude Code first.")
            payload = json.loads((work / "studio-payload.json").read_text())
            from server import duration
            segment = next(s for s in script["segments"] if s["index"] == idx)
            recording = project / "voice" / f"voice_{idx}.wav"
            recording = recording if recording.exists() else None
            seconds = duration(recording)
            if not seconds:
                raise ValueError(f"Segment {idx} needs its recording before rendering.")
            dimensions = str(params.get("image_size", "1280x720")).replace("×", "x").split("x")
            payload.update(content=segment["content"], durationSeconds=seconds, width=int(dimensions[0]), height=int(dimensions[1]))
            (work / "studio-payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Rendering segment {idx} from its saved composition…", flush=True)
            rendered = project / ".studio" / f"render-{uuid.uuid4().hex}.mp4"
            try:
                render_clip(work, payload, rendered, fps=int(params.get("motion_render_fps", 30)))
                if not rendered.is_file() or not duration(rendered):
                    raise ValueError(f"Segment {idx} renderer did not produce a playable video. Check FFprobe and the render log.")
                # The clip keeps its own video; Studio puts it in the engine's place when the clip is selected.
                for old in [".mp4", ".mov", ".webm"]:
                    (work / ("video" + old)).unlink(missing_ok=True)
                rendered.replace(work / "video.mp4")
            finally:
                rendered.unlink(missing_ok=True)
        return {"success": True, "message": f"Rendered {len(selected)} saved composition(s)."}
    if step == "5" and request.get("mode") in {"edit_render", "edit_preview"}:
        import editing
        from edit_render import render
        work = editing.folder(project, request["assembly_edit"])
        preview = request["mode"] == "edit_preview"
        candidate, report = render(work, preview=preview, start=float(request.get("start", 0)), seconds=float(request.get("seconds", 8)))
        if preview:
            return {"success": True, "message": "Editing preview rendered.", "qa": report}
        from edit_render import publish
        publish(work)
        return {"success": True, "message": "Polished video rendered and verified.", "qa": report}
    if step == "5" and request.get("mode") == "three_by_four":
        from core.domain.composer import VideoComposer
        master = project / "final_video.mp4"
        if not master.is_file():
            raise ValueError("Assemble the video first. The 3:4 copy is made from the current landscape video.")
        print("Building the 3:4 copy from the landscape video…", flush=True)
        VideoComposer().export_three_by_four(str(master), read_script(project))
        return {"success": True, "message": "3:4 copy rebuilt from the landscape video."}
    if step == "5" and request.get("mode") == "preview":
        return panel_preview(project, params, request["output"])
    # Studio's step 2 (Segments) is the engine's step 1.5.
    if step == "4" and request.get("mode") == "opening_render":
        # The engine draws the opening quote over its current background; that background is now the chosen one.
        import shutil
        from core.infra.motion.opening_renderer import render_opening_video
        background = project / "images/opening_bg.png"
        if Path(request["background"]).resolve() != background.resolve():
            shutil.copy2(request["background"], background)
        print("Rendering the opening with the chosen background…", flush=True)
        script = json.loads((project / "text/script.json").read_text())
        video = render_opening_video(image_size=str(params.get("image_size", "1280x720")), output_dir=str(project / "images"),
                                     script_data=script, opening_quote=True, opening_bg_path=str(background))
        if not video:
            raise ValueError("The opening could not be rendered. Choose and save an opening quote in Writing first.")
        return {"success": True, "message": "Opening rendered with the chosen background."}
    functions = {"2": steps.run_step_1_5, "3": steps.run_step_3, "4": steps.run_step_4, "5": steps.run_step_5, "6": steps.run_step_6}
    fn = functions[step]
    values = {**params, "project_output_dir": str(project), "target_segments": indices, "regenerate_opening": request.get("opening", False)}
    if step == "2":
        values["split_mode"] = params.get("step1_5_split_mode", "punctuation")
    if step == "4":
        values.update(visual_mode="static_image", llm_server=params["llm_server_step4"], llm_model=params["llm_model_step4"], llm_base_url=params["llm_base_url_step4"])
        check_image_model(params["image_server"], params["image_model"])
        if request.get("mode") == "opening":
            values.update(target_segments=[], regenerate_opening=True)
        elif indices is None:
            script = json.loads((project / "text/script.json").read_text())
            values["target_segments"] = [s["index"] for s in script["segments"] if s.get("visualizer") != "hf"]
    if step == "5":
        values["image_size"] = params.get("video_size") or params["image_size"]
        # Music the user added lives in the app's data folder, not beside the bundled tracks.
        track = str(values.get("bgm_filename") or "")
        added = Path(request.get("music_dir") or "") / track
        if track and Path(track).name == track and not (engine / "music" / track).is_file() and added.is_file():
            values["bgm_filename"] = str(added)
    if step == "6":
        # One run makes every chosen size, each with its own candidates.
        cover = merged.get("step6", {})
        presets = cover.get("cover_image_size_presets", {})
        cover_sizes = [presets[key] for key in cover.get("cover_image_size_keys", []) if key in presets]
        values.update(cover_image_size=cover_sizes or params["image_size"],
                      cover_image_server=params.get("cover_image_server") or params["image_server"])
        if params.get("cover_image_model"):
            check_image_model(values["cover_image_server"], params["cover_image_model"])
    if step == "3":
        # A voice left empty in Settings means the default voice.
        default_voice = (yaml.safe_load((engine / "config.example.yaml").read_text()).get("step3") or {}).get("voice", "")
        values.update(tts_server=config.SUPPORTED_TTS_SERVERS[0], voice=params.get("voice") or default_voice)
    if step in {"3", "5"}:
        values["mute_cut_min_silence_ms"] = params.get("mute_cut_min_silence_ms", 200)
    result = call(fn, values)
    if step == "5" and result.get("success") and not getattr(config, "THREE_BY_FOUR_ENABLED", True):
        # Revision history keeps the earlier copy; left here it would no longer match the video.
        (project / "final_video_3x4.mp4").unlink(missing_ok=True)
    if step == "3" and result.get("missing_segments"):
        result["message"] = result.get("message", "") + " · Other recordings are still missing."
    return result


if __name__ == "__main__":
    try:
        result = run(json.loads(sys.argv[1]))
        print("STUDIO_RESULT:" + json.dumps(result, ensure_ascii=False, default=str), flush=True)
        sys.exit(0 if result.get("success") else 1)
    except Exception as exc:
        print("STUDIO_RESULT:" + json.dumps({"success": False, "message": str(exc)}, ensure_ascii=False), flush=True)
        sys.exit(1)
