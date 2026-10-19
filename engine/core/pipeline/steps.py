"""流水线分步执行实现模块。

本文件承载步骤 1.5~6 的核心实现与跨步骤辅助函数（步骤 1 写作由 Studio 的 Claude Code 会话完成），主要包括：
1. 文本处理链路（分段脚本与描述小结生成）。
2. 目录/文件落盘（script/音视频资源）。
3. 多媒体生成链路（语音合成、开场视频、分段画面、最终视频合成、封面图生成）。
4. 运行时工具函数（路径解析、开场旁白生成、BGM 定位、HTML 动画开场渲染、失败兜底处理）。

设计上保持“单步可独立调用”，由 Studio 的任务进程分步运行，便于测试隔离。
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence, Union

from core.config import Config, config
from core.domain.composer import VideoComposer
from core.domain.metadata import (
    get_primary_cover_subtitle,
    get_primary_cover_title,
    get_primary_golden_quote,
    get_primary_video_title,
    parse_marked_focus_text,
)
from core.infra.ai.image_client import (
    generate_cover_images,
    generate_images_for_segments,
    synthesize_voice_for_segments,
    _generate_single_image,
)
from core.infra.ai.claude_agent import run_step1_5_segment_agent
from core.domain.summarizer import generate_description_summary, process_raw_to_script
from core.infra.ai.tts_client import text_to_audio_bytedance
from core.infra.project_paths import ProjectPaths
from core.prompts import OPENING_BG_PROMPT_TEMPLATE
from core.infra.motion.segment_renderer import render_motion_segments_with_agent
from core.shared import load_json_file, logger

SEGMENT_VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v")


def _get_project_root() -> str:
    """Return project root path regardless of package nesting depth."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _clean_redundant_agent_folders(project_output_dir: str) -> None:
    """自动清理 Claude 子代理因引号规范化差异而在 output 目录中额外生成的冗余文件夹。"""
    if not project_output_dir:
        return
    try:
        import shutil
        # 1. 清理直双引号文件夹
        normalized_dir = project_output_dir.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")
        if normalized_dir != project_output_dir and os.path.exists(normalized_dir) and os.path.isdir(normalized_dir):
            shutil.rmtree(normalized_dir, ignore_errors=True)
        # 2. 清理完全无引号的文件夹
        quotes_stripped_dir = project_output_dir.replace("“", "").replace("”", "").replace("‘", "").replace("’", "").replace("\"", "").replace("'", "")
        if quotes_stripped_dir != project_output_dir and os.path.exists(quotes_stripped_dir) and os.path.isdir(quotes_stripped_dir):
            shutil.rmtree(quotes_stripped_dir, ignore_errors=True)
    except Exception:
        pass


def _resolve_bgm_audio_path(bgm_filename: Optional[str], project_root: str) -> Optional[str]:
    """Locate BGM asset either via absolute path or music directory."""
    if not bgm_filename:
        return None
    if os.path.isabs(bgm_filename) and os.path.exists(bgm_filename):
        return bgm_filename
    candidate = os.path.join(project_root, "music", bgm_filename)
    if os.path.exists(candidate):
        return candidate
    return None


def _ensure_opening_narration(
    script_data: Optional[Dict[str, Any]],
    voice_dir: str,
    voice: str,
    tts_model: str,
    opening_quote: bool,
    announce: bool = False,
    force_regenerate: bool = False,
    speech_rate: int = 0,
    loudness_rate: int = 0,
    emotion: str = "neutral",
    emotion_scale: int = 4,
    mute_cut_threshold: int = 400,
    mute_cut_min_silence_ms: int = 200,
    mute_cut_remain_ms: int = 100,
) -> Optional[str]:
    """Generate or reuse opening narration audio when required."""
    opening_golden_quote = get_primary_golden_quote(script_data or {}, "")
    if not (opening_quote and isinstance(opening_golden_quote, str) and opening_golden_quote.strip()):
        return None

    opening_golden_quote, _ = parse_marked_focus_text(opening_golden_quote)
    if not opening_golden_quote:
        return None

    try:
        os.makedirs(voice_dir, exist_ok=True)
        opening_path = os.path.join(voice_dir, "opening.wav")
        if force_regenerate and os.path.exists(opening_path):
            try:
                os.remove(opening_path)
            except Exception:
                if announce:
                    print("⚠️ 开场音频删除失败，尝试直接覆盖")

        if os.path.exists(opening_path):
            if announce:
                print(f"✅ 开场音频已存在: {opening_path}")
            return opening_path

        ok = text_to_audio_bytedance(
            opening_golden_quote,
            opening_path,
            voice=voice,
            model=tts_model,
            speech_rate=speech_rate,
            loudness_rate=loudness_rate,
            emotion=emotion,
            emotion_scale=emotion_scale,
            mute_cut_threshold=mute_cut_threshold,
            mute_cut_min_silence_ms=mute_cut_min_silence_ms,
            mute_cut_remain_ms=mute_cut_remain_ms,
        )
        if ok:
            if announce:
                print(f"✅ 开场音频已生成: {opening_path}")
            return opening_path
        if announce:
            print("❌ 开场音频生成失败")
    except Exception:
        if announce:
            print("❌ 开场音频生成失败")
    return None


def _resolve_description_source_text(
    project_output_dir: str,
    raw_data: Optional[Dict[str, Any]] = None,
    script_data: Optional[Dict[str, Any]] = None,
) -> str:
    """The manuscript the step-2 summary is written from."""
    if raw_data:
        content = (raw_data.get("content") or "").strip()
        if content:
            return content

    if script_data:
        segments = script_data.get("segments") or []
        merged = "\n".join(seg.get("content", "") for seg in segments).strip()
        if merged:
            return merged

    return ""


def _resolve_segment_media_path(paths: ProjectPaths, index: int) -> Optional[str]:
    """解析段落素材路径：优先图片，其次视频（兼容扩展名大小写）。"""
    png_path = paths.segment_image(index)
    if os.path.exists(png_path):
        return png_path

    base_name = f"segment_{index}"

    # 优先常见小写命名
    for ext in SEGMENT_VIDEO_EXTENSIONS:
        candidate = os.path.join(paths.images, f"{base_name}{ext}")
        if os.path.exists(candidate):
            return candidate

    # 回退：扫描目录，兼容 .MP4/.MOV 等大小写扩展名
    try:
        for filename in os.listdir(paths.images):
            file_path = os.path.join(paths.images, filename)
            if not os.path.isfile(file_path):
                continue
            stem, ext = os.path.splitext(filename)
            if stem.lower() == base_name and ext.lower() in SEGMENT_VIDEO_EXTENSIONS:
                return file_path
    except OSError:
        pass

    return None


def _normalize_segment_visualizer(segment: Dict[str, Any]) -> str:
    visualizer = str((segment or {}).get("visualizer") or "image").strip().lower()
    if visualizer not in {"image", "hf"}:
        raise ValueError(f"不支持的段落 visualizer: {visualizer}，支持 image / hf")
    return visualizer


def _split_mixed_visual_targets(
    segments: List[Dict[str, Any]],
    selected_segments: Optional[List[int]],
) -> Dict[str, List[int]]:
    selected_set = set(selected_segments) if selected_segments is not None else None
    targets = {"image": [], "hf": []}
    for fallback_idx, segment in enumerate(segments, 1):
        segment_index = int((segment or {}).get("index") or fallback_idx)
        if selected_set is not None and segment_index not in selected_set:
            continue
        targets[_normalize_segment_visualizer(segment)].append(segment_index)
    return targets


def _merge_visual_generation_results(
    paths: ProjectPaths,
    total_segments: int,
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    image_paths = [_resolve_segment_media_path(paths, idx) or "" for idx in range(1, total_segments + 1)]
    failed_segments: set[int] = set()
    processed_segments: set[int] = set()
    missing_audio_segments: set[int] = set()

    for result in results:
        for idx, media_path in enumerate(result.get("image_paths", []) or [], 1):
            if idx <= total_segments and media_path:
                image_paths[idx - 1] = media_path
        failed_segments.update(int(idx) for idx in result.get("failed_segments", []) or [])
        processed_segments.update(int(idx) for idx in result.get("processed_segments", []) or [])
        missing_audio_segments.update(int(idx) for idx in result.get("missing_audio_segments", []) or [])

    return {
        "image_paths": image_paths,
        "failed_segments": sorted(failed_segments),
        "processed_segments": sorted(processed_segments),
        "missing_audio_segments": sorted(missing_audio_segments),
    }


def run_step_1_5(
    project_output_dir: str,
    num_segments: int,
    split_mode: str = "punctuation",
    llm_server_step1_5: Optional[str] = None,
    llm_model_step1_5: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        print("正在处理原始内容为脚本...")

        paths = ProjectPaths(project_output_dir)
        raw_json_path = paths.raw_json()
        script_path = paths.script_json()
        raw_data = load_json_file(raw_json_path)
        if not raw_data:
            return {"success": False, "message": f"无法加载 raw.json 文件: {raw_json_path}"}
        if split_mode not in {"punctuation", "manual", "agent"}:
            split_mode = "punctuation"
        if split_mode == "agent":
            print(f"目标分段数: {num_segments}")

        agent_segments = None
        if split_mode == "agent":
            print("正在调用Agent进行智能分段与画面模式选择...")
            agent_segments = run_step1_5_segment_agent(
                raw_json_path=raw_json_path,
                output_json_path=os.path.join(paths.text, "_step1_5_segments.json"),
                target_segments=num_segments,
                session_log_path=os.path.join(paths.text, "_step1_5_agent_session.jsonl"),
                repo_root=_get_project_root(),
                llm_server=llm_server_step1_5,
                llm_model=llm_model_step1_5,
            )

        script_data = process_raw_to_script(
            raw_data,
            num_segments,
            split_mode,
            agent_segments=agent_segments,
        )

        with open(script_path, "w", encoding="utf-8") as handle:
            json.dump(script_data, handle, ensure_ascii=False, indent=2)
        print(f"分段脚本已保存到: {script_path}")

        logger.info(f"步骤1.5处理完成: {script_path}")
        return {
            "success": True,
            "script_data": script_data,
            "script_path": script_path,
            "message": "步骤1.5处理完成",
        }
    except Exception as exc:
        logger.error(f"步骤1.5处理失败: {str(exc)}")
        return {"success": False, "message": f"步骤1.5处理失败: {str(exc)}"}


def run_step_2(
    llm_server: str,
    llm_model: str,
    llm_base_url: str,
    project_output_dir: str,
    script_path: Optional[str] = None,
) -> Dict[str, Any]:
    paths = ProjectPaths(project_output_dir)
    script_data = load_json_file(script_path) if script_path else load_json_file(paths.script_json())
    if script_data is None:
        return {"success": False, "message": "未找到脚本数据，请先完成步骤1.5"}

    raw_data = load_json_file(paths.raw_json()) if os.path.exists(paths.raw_json()) else None
    description_source = _resolve_description_source_text(
        project_output_dir, raw_data=raw_data, script_data=script_data
    )
    description_data = generate_description_summary(
        llm_server, llm_model, llm_base_url, description_source or "", max_chars=200
    )
    description_path = paths.mini_summary_json()
    with open(description_path, "w", encoding="utf-8") as handle:
        json.dump(description_data, handle, ensure_ascii=False, indent=2)
    return {"success": True, "mini_summary_path": description_path}


def run_step_3(
    tts_server: str,
    voice: str,
    tts_model: str,
    project_output_dir: str,
    opening_quote: bool = True,
    target_segments: Optional[List[int]] = None,
    regenerate_opening: bool = True,
    speech_rate: int = 0,
    loudness_rate: int = 0,
    emotion: str = "neutral",
    emotion_scale: int = 4,
    mute_cut_threshold: int = 400,
    mute_cut_min_silence_ms: int = 200,
    mute_cut_remain_ms: int = 100,
) -> Dict[str, Any]:
    paths = ProjectPaths(project_output_dir)
    paths.ensure_dirs_exist()

    script_data = load_json_file(paths.script_json())
    if script_data is None:
        return {"success": False, "message": "未找到脚本数据，请先完成步骤1.5"}

    segments = script_data.get("segments", [])
    total_segments = len(segments)
    if total_segments == 0:
        return {"success": False, "message": "脚本中缺少段落内容"}

    selected_segments: Optional[List[int]] = None
    if target_segments is not None:
        raw_targets = list(target_segments)
        parsed_targets: List[int] = []
        for value in raw_targets:
            try:
                parsed_targets.append(int(value))
            except (TypeError, ValueError):
                continue
        selected_segments = sorted({idx for idx in parsed_targets if 1 <= idx <= total_segments})
        if raw_targets and not selected_segments:
            return {"success": False, "message": f"段落选择无效，请输入 1-{total_segments} 之间的数字"}

    generation_targets = None if selected_segments is None else selected_segments
    voice_result = synthesize_voice_for_segments(
        tts_server,
        voice,
        tts_model,
        script_data,
        paths.voice,
        target_segments=generation_targets,
        speech_rate=speech_rate,
        loudness_rate=loudness_rate,
        emotion=emotion,
        emotion_scale=emotion_scale,
        mute_cut_threshold=mute_cut_threshold,
        mute_cut_min_silence_ms=mute_cut_min_silence_ms,
        mute_cut_remain_ms=mute_cut_remain_ms,
    )
    audio_paths = voice_result.get("audio_paths", [])
    missing_segments = voice_result.get("missing_segments", [])

    opening_audio_file = paths.opening_audio()
    opening_previously_exists = os.path.exists(opening_audio_file)
    narration_path = _ensure_opening_narration(
        script_data,
        paths.voice,
        voice,
        tts_model,
        opening_quote,
        announce=True,
        force_regenerate=regenerate_opening,
        speech_rate=speech_rate,
        loudness_rate=loudness_rate,
        emotion=emotion,
        emotion_scale=emotion_scale,
        mute_cut_threshold=mute_cut_threshold,
        mute_cut_min_silence_ms=mute_cut_min_silence_ms,
        mute_cut_remain_ms=mute_cut_remain_ms,
    )

    opening_refreshed = bool(opening_quote and narration_path and (regenerate_opening or not opening_previously_exists))
    processed_segments = list(range(1, total_segments + 1)) if selected_segments is None else list(selected_segments)

    if selected_segments is None:
        message = "段落语音生成完成"
        if opening_refreshed:
            message += "，开场金句音频已更新"
    elif processed_segments:
        seg_text = "、".join(str(idx) for idx in processed_segments)
        message = f"已生成第 {seg_text} 段语音"
        if opening_refreshed:
            message += " 并刷新开场金句音频"
    else:
        message = "未生成新的段落语音"
        if opening_refreshed:
            message = "已重新生成开场金句音频"

    return {
        "success": True,
        "audio_paths": audio_paths,
        "processed_segments": processed_segments,
        "missing_segments": missing_segments,
        "message": message,
    }


def run_step_4(
    image_server: str,
    image_model: str,
    image_size: str,
    image_style_preset: str,
    project_output_dir: str,
    opening_quote: bool = True,
    target_segments: Optional[List[int]] = None,
    regenerate_opening: bool = True,
    llm_model: Optional[str] = None,
    llm_server: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    visual_mode: Optional[str] = None,
    motion_style_preset: Optional[str] = None,
    motion_max_turns: Optional[int] = None,
    motion_render_fps: Optional[int] = None,
    motion_concurrency: Optional[int] = None,
    image_prompt_template: Optional[str] = None,
) -> Dict[str, Any]:
    paths = ProjectPaths(project_output_dir)
    paths.ensure_dirs_exist()

    script_data = load_json_file(paths.script_json())
    if script_data is None:
        return {"success": False, "message": "未找到脚本数据，请先完成步骤1.5"}

    segments = script_data.get("segments", [])
    total_segments = len(segments)
    if total_segments == 0:
        return {"success": False, "message": "脚本中缺少段落内容"}

    selected_segments: Optional[List[int]] = None
    if target_segments is not None:
        raw_targets = list(target_segments)
        parsed_targets: List[int] = []
        for value in raw_targets:
            try:
                parsed_targets.append(int(value))
            except (TypeError, ValueError):
                continue
        selected_segments = sorted({idx for idx in parsed_targets if 1 <= idx <= total_segments})
        if raw_targets and not selected_segments:
            return {"success": False, "message": f"段落选择无效，请输入 1-{total_segments} 之间的数字"}

    description_data = load_json_file(paths.mini_summary_json())
    if description_data is None:
        return {"success": False, "message": "未找到描述小结，请先执行步骤2生成描述"}

    # Visuals make only the opening's background; assembly renders the quote over it.
    opening_image_path = None
    opening_regenerated = False

    # 1. 尝试生成/刷新开场背景图 (opening_bg.png)
    opening_bg_file = os.path.join(paths.images, "opening_bg.png")
    if opening_quote:
        need_bg_refresh = regenerate_opening or not os.path.exists(opening_bg_file)
        if need_bg_refresh:
            # 1.1 获取或生成描述小结
            summary_text = ""
            desc_data = load_json_file(paths.mini_summary_json())
            if desc_data and desc_data.get("summary"):
                summary_text = desc_data["summary"].strip()
            else:
                # 动态生成描述小结
                raw_data = load_json_file(paths.raw_json()) if os.path.exists(paths.raw_json()) else None
                description_source = _resolve_description_source_text(
                    project_output_dir, raw_data=raw_data, script_data=script_data
                )
                if description_source:
                    try:
                        logger.info("🎬 开场背景图所需描述小结未找到，正在生成...")
                        generated_desc = generate_description_summary(
                            llm_server or config.llm_server_step2,
                            llm_model or config.llm_model_step2,
                            llm_base_url or config.llm_base_url_step2,
                            description_source,
                            max_chars=200
                        )
                        summary_text = generated_desc.get("summary", "").strip()
                        with open(paths.mini_summary_json(), "w", encoding="utf-8") as handle:
                            json.dump(generated_desc, handle, ensure_ascii=False, indent=2)
                    except Exception as e:
                        logger.warning(f"生成开场背景图描述小结失败: {e}")

            if not summary_text:
                summary_text = get_primary_video_title(script_data or {}, "视频开场")

            # 1.2 解析风格设置
            from core.infra.ai.image_client import IMAGE_STYLE_PRESETS
            try:
                image_style = IMAGE_STYLE_PRESETS.get(
                    image_style_preset,
                    next(iter(IMAGE_STYLE_PRESETS.values()))
                )
                import re
                image_style = re.sub(r'[；，]?字体为[^；。，]*[；。，]?', '', image_style).strip()
            except Exception:
                image_style = ""
            style_block = image_style or "画面需保持信息清晰、构图稳定、色彩和谐。"

            # 1.3 组合提示词并调用 API 生成
            bg_prompt = OPENING_BG_PROMPT_TEMPLATE.format(
                summary=summary_text,
                style_block=style_block
            )

            safety_options = None
            if llm_model and llm_server and llm_base_url:
                safety_options = {
                    "llm_model": llm_model,
                    "llm_server": llm_server,
                    "llm_base_url": llm_base_url,
                    "max_attempts": 3,
                    "max_tokens": 800,
                    "temperature": 0.2,
                }

            logger.info("🎨 正在生成开场背景图 (opening_bg.png)...")
            bg_args = (
                "opening_bg",
                bg_prompt,
                image_model,
                image_size,
                paths.images,
                image_server,
                safety_options or {}
            )
            bg_res = _generate_single_image(bg_args)
            if bg_res.get("success") and bg_res.get("image_path"):
                temp_path = bg_res["image_path"]
                if os.path.exists(temp_path):
                    if os.path.exists(opening_bg_file):
                        try:
                            os.remove(opening_bg_file)
                        except Exception:
                            pass
                    try:
                        os.rename(temp_path, opening_bg_file)
                        opening_regenerated = True
                        logger.info(f"开场背景图生成并保存至: {opening_bg_file}")
                    except Exception as e:
                        logger.warning(f"重命名开场背景图失败: {e}")
            else:
                logger.warning("开场背景图生成失败")
        if os.path.exists(opening_bg_file):
            opening_image_path = opening_bg_file

    # 2. 并行调度任务：开场视频渲染与段落画面生成
    mode = (visual_mode or getattr(config, "VISUAL_MODE", "static_image") or "static_image").strip().lower()
    if mode not in {"static_image", "motion_agent", "mixed"}:
        return {"success": False, "message": f"不支持的画面生成模式: {visual_mode}"}

    def run_static(targets: Optional[List[int]]) -> Dict[str, Any]:
        return generate_images_for_segments(
            image_server,
            image_model,
            script_data,
            image_style_preset,
            image_size,
            paths.images,
            description_data=description_data,
            target_segments=targets,
            llm_model=llm_model,
            llm_server=llm_server,
            llm_base_url=llm_base_url,
            image_prompt_template=image_prompt_template,
        )

    def run_motion(targets: Optional[List[int]]) -> Dict[str, Any]:
        return render_motion_segments_with_agent(
            project_output_dir=project_output_dir,
            script_data=script_data,
            image_size=image_size,
            output_dir=paths.images,
            target_segments=targets,
            description_data=description_data,
            style_preset=motion_style_preset or getattr(config, "MOTION_STYLE_PRESET", "data_driven"),
            max_turns=int(motion_max_turns or getattr(config, "MOTION_MAX_TURNS", 60)),
            render_fps=int(motion_render_fps or getattr(config, "MOTION_RENDER_FPS", 30)),
            concurrency=int(motion_concurrency or getattr(config, "MOTION_CONCURRENCY", 1)),
            session_log_path=os.path.join(paths.images, "motion", "_step4_motion_agent_session.jsonl"),
            repo_root=_get_project_root(),
            llm_server=llm_server,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
        )

    def generate_segments_task() -> Dict[str, Any]:
        should_generate_segments = selected_segments is None or len(selected_segments) > 0
        generation_targets = None if selected_segments is None else selected_segments
        if not should_generate_segments:
            image_paths = []
            for idx in range(1, total_segments + 1):
                media_path = _resolve_segment_media_path(paths, idx)
                image_paths.append(media_path or "")
            return {"image_paths": image_paths, "failed_segments": [], "processed_segments": []}

        if mode == "static_image":
            return run_static(generation_targets)
        elif mode == "motion_agent":
            return run_motion(generation_targets)
        else:
            try:
                mixed_targets = _split_mixed_visual_targets(segments, selected_segments)
            except ValueError as exc:
                return {"success": False, "message": str(exc)}

            jobs: List[tuple[str, List[int]]] = []
            if mixed_targets["image"]:
                jobs.append(("image", mixed_targets["image"]))
            if mixed_targets["hf"]:
                jobs.append(("hf", mixed_targets["hf"]))

            results: List[Dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as executor:
                futures = {
                    executor.submit(run_static if kind == "image" else run_motion, targets): kind
                    for kind, targets in jobs
                }
                for future in as_completed(futures):
                    results.append(future.result())

            return _merge_visual_generation_results(paths, total_segments, results)

    image_result = generate_segments_task()

    failed_segments = image_result.get("failed_segments", [])
    if failed_segments:
        failed_str = "、".join(str(idx) for idx in failed_segments)
        missing_audio_segments = image_result.get("missing_audio_segments", [])
        if missing_audio_segments:
            missing_str = "、".join(str(idx) for idx in missing_audio_segments)
            return {
                "success": False,
                "message": f"第 {missing_str} 段缺少音频，请先完成步骤3语音合成。",
                "failed_segments": failed_segments,
                "missing_audio_segments": missing_audio_segments,
                "image_paths": image_result.get("image_paths", []),
                "opening_image_path": opening_image_path,
            }
        return {
            "success": False,
            "message": f"第 {failed_str} 段画面生成失败，请调整提示或稍后重试。",
            "failed_segments": failed_segments,
            "image_paths": image_result.get("image_paths", []),
            "opening_image_path": opening_image_path,
        }

    processed_segments = image_result.get("processed_segments", [])
    if selected_segments is None:
        message = "段落画面生成完成"
        if opening_regenerated:
            message += "，开场背景已更新"
    elif processed_segments:
        seg_text = "、".join(str(idx) for idx in processed_segments)
        message = f"已生成第 {seg_text} 段画面"
        if opening_regenerated:
            message += " 并刷新开场背景"
    else:
        message = "未生成新的段落画面"
        if opening_regenerated:
            message = "已重新生成开场背景"

    payload = {
        "success": True,
        "opening_image_path": opening_image_path,
        "opening_generated": opening_regenerated,
        "processed_segments": processed_segments,
        "message": message,
    }
    for key, value in image_result.items():
        if key != "processed_segments":
            payload[key] = value
    _clean_redundant_agent_folders(project_output_dir)

    return payload


def run_step_5(
    project_output_dir: str,
    image_size: str,
    enable_subtitles: bool,
    bgm_filename: str,
    voice: str,
    tts_model: str,
    opening_quote: bool = True,
    speech_rate: int = 0,
    loudness_rate: int = 0,
    emotion: str = "neutral",
    emotion_scale: int = 4,
    mute_cut_threshold: int = 400,
    mute_cut_min_silence_ms: int = 200,
    mute_cut_remain_ms: int = 100,
    opening_image_path: Optional[str] = None,
) -> Dict[str, Any]:
    project_root = _get_project_root()
    paths = ProjectPaths(project_output_dir)

    if not os.path.exists(paths.script_json()):
        return {"success": False, "message": "脚本文件不存在，请先完成步骤1.5"}

    script_data = load_json_file(paths.script_json())
    if not script_data:
        return {"success": False, "message": "脚本文件加载失败"}

    expected_segments = script_data.get("actual_segments", 0)
    image_paths = []
    for idx in range(1, expected_segments + 1):
        media_path = _resolve_segment_media_path(paths, idx)
        if media_path:
            image_paths.append(media_path)
    image_count = len(image_paths)

    audio_count = 0
    for idx in range(1, expected_segments + 1):
        if paths.segment_audio_exists(idx):
            audio_count += 1

    if image_count == 0:
        return {"success": False, "message": "未找到画面素材，请先完成步骤4"}
    if audio_count == 0:
        return {"success": False, "message": "未找到音频文件，请先完成步骤3"}
    if image_count != expected_segments:
        return {"success": False, "message": f"画面素材不完整，需要{expected_segments}个，找到{image_count}个"}
    if audio_count != expected_segments:
        return {"success": False, "message": f"音频文件不完整，需要{expected_segments}个，找到{audio_count}个"}

    audio_paths = []
    for idx in range(1, script_data.get("actual_segments", 0) + 1):
        audio_path = paths.segment_audio_exists(idx)
        if audio_path:
            audio_paths.append(audio_path)

    bgm_audio_path = _resolve_bgm_audio_path(bgm_filename, project_root)
    # The opening clip assembly rendered for this video, else whatever sits in the opening's place.
    opening_image_candidate = opening_image_path if opening_image_path and os.path.exists(opening_image_path) else (
        paths.opening_image() if os.path.exists(paths.opening_image()) else None)
    opening_narration_audio_path = _ensure_opening_narration(
        script_data,
        paths.voice,
        voice,
        tts_model,
        opening_quote,
        speech_rate=speech_rate,
        loudness_rate=loudness_rate,
        emotion=emotion,
        emotion_scale=emotion_scale,
        mute_cut_threshold=mute_cut_threshold,
        mute_cut_min_silence_ms=mute_cut_min_silence_ms,
        mute_cut_remain_ms=mute_cut_remain_ms,
    )

    composer = VideoComposer()
    final_video_path = composer.compose_video(
        image_paths,
        audio_paths,
        paths.final_video(),
        script_data=script_data,
        enable_subtitles=enable_subtitles,
        bgm_audio_path=bgm_audio_path,
        opening_image_path=opening_image_candidate,
        opening_narration_audio_path=opening_narration_audio_path,
        bgm_volume=float(getattr(config, "BGM_DEFAULT_VOLUME", 0.2)),
        narration_volume=float(getattr(config, "NARRATION_DEFAULT_VOLUME", 1.0)),
        image_size=image_size,
        opening_quote=opening_quote,
        project_root=project_output_dir,
    )

    return {"success": True, "final_video": final_video_path}


def _run_cover_generation(
    project_output_dir: str,
    cover_image_size: Optional[str],
    cover_image_server: str,
    cover_image_model: Optional[str],
    cover_image_style: Optional[str],
    cover_image_count: int,
    script_data: Optional[Dict[str, Any]],
    raw_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not script_data and not raw_data:
        raise ValueError("缺少脚本或原始数据")

    base = script_data or raw_data or {}
    title = get_primary_video_title(base, "未命名视频")
    cover_title = get_primary_cover_title(base, title)
    cover_subtitle = get_primary_cover_subtitle(base, title)

    if cover_image_size:
        from core.config import COVER_IMAGE_SIZE_PRESETS

        if cover_image_size in COVER_IMAGE_SIZE_PRESETS:
            cover_image_size = COVER_IMAGE_SIZE_PRESETS[cover_image_size]
    else:
        from core.config import COVER_IMAGE_SIZE_KEYS, COVER_IMAGE_SIZE_PRESETS

        default_key = next(iter(COVER_IMAGE_SIZE_KEYS or []), "")
        cover_image_size = COVER_IMAGE_SIZE_PRESETS.get(default_key, "2048x2048")

    cover_image_model = cover_image_model or config.RECOMMENDED_MODELS["image"].get(
        cover_image_server, ["doubao-seedream-4-0-250828"]
    )[0]
    cover_image_style = cover_image_style or "cover01"
    if not cover_image_server:
        raise ValueError("封面参数错误: cover_image_server 不能为空")
    try:
        Config.validate_parameters(
            num_segments=config.MIN_NUM_SEGMENTS,
            llm_server=config.SUPPORTED_LLM_SERVERS[0],
            image_server=cover_image_server,
            tts_server=config.SUPPORTED_TTS_SERVERS[0],
            image_model=cover_image_model,
            image_size=cover_image_size,
            llm_model=config.RECOMMENDED_MODELS["llm"][config.SUPPORTED_LLM_SERVERS[0]][0],
        )
    except Exception as exc:
        raise ValueError(f"封面参数校验失败: {exc}") from exc

    return generate_cover_images(
        project_output_dir,
        cover_image_server,
        cover_image_model,
        cover_image_size,
        cover_image_style,
        max(1, int(cover_image_count or 1)),
        cover_title,
        cover_subtitle,
    )


def run_step_6(
    project_output_dir: str,
    cover_image_size: Union[str, Sequence[str]],
    cover_image_server: Optional[str] = None,
    cover_image_model: Optional[str] = None,
    cover_image_style: str = "cover01",
    cover_image_count: int = 1,
) -> Dict[str, Any]:
    paths = ProjectPaths(project_output_dir)
    if not os.path.exists(paths.raw_json()):
        return {"success": False, "message": "缺少 raw.json，请先完成步骤1"}

    raw_data = load_json_file(paths.raw_json())
    if raw_data is None:
        return {"success": False, "message": "raw.json 加载失败"}

    script_data = load_json_file(paths.script_json()) if os.path.exists(paths.script_json()) else None

    # 可一次生成多个尺寸（预设 key 或 WxH），每个尺寸各生成 cover_image_count 张。
    sizes = [cover_image_size] if isinstance(cover_image_size, str) or not cover_image_size else list(dict.fromkeys(cover_image_size))
    cover_image_server = cover_image_server or getattr(config, "COVER_IMAGE_SERVER", "")
    cover_paths: List[str] = []
    failures: List[str] = []
    style_id = cover_image_style
    for size in sizes:
        try:
            cover_result = _run_cover_generation(
                project_output_dir,
                size,
                cover_image_server,
                cover_image_model,
                cover_image_style,
                cover_image_count,
                script_data,
                raw_data,
            )
        except Exception as exc:
            failures.append(f"{size}: {exc}" if len(sizes) > 1 else str(exc))
            continue
        cover_paths += cover_result.get("cover_paths", [])
        failures += [f"{size}: {f}" if len(sizes) > 1 else f for f in cover_result.get("failures", [])]
        style_id = cover_result.get("style_id", style_id)
    if not cover_paths:
        return {"success": False, "message": "；".join(failures) or "封面生成失败", "failures": failures}
    return {"success": True, "cover_paths": cover_paths, "failures": failures, "style_id": style_id}


__all__ = [
    "run_step_1_5",
    "run_step_2",
    "run_step_3",
    "run_step_4",
    "run_step_5",
    "run_step_6",
    "_get_project_root",
    "_resolve_bgm_audio_path",
    "_ensure_opening_narration",
    "_resolve_description_source_text",
    "_run_cover_generation",
]
