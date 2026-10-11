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


def run(request):
    engine = Path(request["engine"])
    project = Path(request["project"])
    sys.path.insert(0, str(engine))
    # The engine may be read-only; anything written relative to the working folder stays in the project.
    os.chdir(project)
    import yaml
    from core.config import apply_yaml_config, get_generation_params, config
    config_path = engine / "config.yaml" if (engine / "config.yaml").exists() else engine / "config.example.yaml"
    merged = yaml.safe_load(config_path.read_text())
    from server import engine_settings
    for section, fields in engine_settings(request.get("settings", {})).items():
        merged.setdefault(section, {}).update(fields)
    runtime = project / ".studio/job-config.yaml"
    runtime.write_text(yaml.safe_dump(merged, allow_unicode=True), encoding="utf-8")
    apply_yaml_config(runtime)
    params = get_generation_params(runtime)
    from core.pipeline import steps
    step = request["step"]
    indices = request.get("indices")
    # The desktop's JSON editor is authoritative; keep the legacy DOCX bridge aligned.
    if step == "1.5" or request.get("summary"):
        from core.domain.docx_transform import export_raw_to_docx
        raw = json.loads((project / "text/raw.json").read_text())
        export_raw_to_docx(raw, str(project / "text/raw.docx"))
    if request.get("summary"):
        # Every visual follows a shared direction written from the manuscript (engine step 2).
        print("Generating the visual direction…", flush=True)
        result = call(steps.run_step_2, {"project_output_dir": str(project), "llm_server": params["llm_server_step2"],
                                         "llm_model": params["llm_model_step2"], "llm_base_url": params["llm_base_url_step2"]})
        if not result.get("success"):
            return result
        if request.get("mode") == "summary":
            return {"success": True, "message": "Visual direction generated."}
    if step == "4" and request.get("mode") == "render":
        from core.infra.hyperframes.runtime import hyperframes_command, subprocess_env
        script = json.loads((project / "text/script.json").read_text())
        selected = indices or [s["index"] for s in script["segments"] if s.get("visualizer") == "hf"]
        if not selected:
            raise ValueError("Select an animated segment to render.")
        for idx in selected:
            work = project / "images/hyperframes" / f"segment_{idx}"
            if not (work / "index.html").exists() or not (work / "studio-payload.json").exists():
                raise ValueError(f"Segment {idx} has no saved composition. Activate Claude Code first.")
            payload = json.loads((work / "studio-payload.json").read_text())
            from server import duration
            segment = next(s for s in script["segments"] if s["index"] == idx)
            recording = next((project / "voice" / f"voice_{idx}{ext}" for ext in [".wav", ".mp3"] if (project / "voice" / f"voice_{idx}{ext}").exists()), None)
            seconds = duration(recording)
            if not seconds:
                raise ValueError(f"Segment {idx} needs its recording before rendering.")
            dimensions = str(params.get("image_size", "1280x720")).replace("×", "x").split("x")
            payload.update(content=segment["content"], durationSeconds=seconds, width=int(dimensions[0]), height=int(dimensions[1]))
            (work / "studio-payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Rendering segment {idx} from its saved composition…", flush=True)
            rendered = project / ".studio" / f"render-{uuid.uuid4().hex}.mp4"
            try:
                subprocess.run([*hyperframes_command(), "render", "--output", str(rendered), "--fps", str(params.get("hyperframes_render_fps", 30)), "--variables", json.dumps(payload, ensure_ascii=False)], cwd=work, check=True, env=subprocess_env())
                if not rendered.is_file() or not duration(rendered):
                    raise ValueError(f"Segment {idx} renderer did not produce a playable video. Check FFprobe and the render log.")
                rendered.replace(project / "images" / f"segment_{idx}.mp4")
            finally:
                rendered.unlink(missing_ok=True)
            # Assembly would prefer a still over the video; it waits in images/stills/ in case
            # the segment goes back to a still image.
            from server import Studio
            Studio.park_still(project, idx)
        return {"success": True, "message": f"Rendered {len(selected)} saved composition(s)."}
    functions = {"1.5": steps.run_step_1_5, "3": steps.run_step_3, "4": steps.run_step_4, "5": steps.run_step_5, "6": steps.run_step_6}
    fn = functions[step]
    values = {**params, "project_output_dir": str(project), "target_segments": indices, "regenerate_opening": request.get("opening", False)}
    if step == "1.5":
        values["split_mode"] = params.get("step1_5_split_mode", "auto")
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
    if step == "6":
        cover = merged.get("step6", {})
        cover_size = cover.get("cover_image_size_presets", {}).get(cover.get("default_cover_image_size_key", ""))
        values.update(cover_image_size=cover_size or params["image_size"],
                      cover_image_server=params.get("cover_image_server") or params["image_server"])
        if params.get("cover_image_model"):
            check_image_model(values["cover_image_server"], params["cover_image_model"])
    if step == "3":
        # Projects saved before the engine had a default voice carry an empty one.
        default_voice = (yaml.safe_load(config_path.read_text()).get("step3") or {}).get("voice", "")
        values.update(tts_server=config.SUPPORTED_TTS_SERVERS[0], voice=params.get("voice") or default_voice)
    if step in {"3", "5"}:
        values["mute_cut_min_silence_ms"] = params.get("mute_cut_min_silence_ms", 200)
    result = call(fn, values)
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
