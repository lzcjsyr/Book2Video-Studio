"""TTS orchestration: API request + local post-processing + file output."""

import os
import json
import base64
import struct

from typing import Any, Dict, List, Optional, Tuple

import requests

from core.config import config
from core.shared import logger, APIError, retry_on_failure, ensure_directory_exists


def silence_keep_ranges(
    pcm_data: bytes,
    sample_rate: int = 48000,
    threshold: int = 400,
    min_silence_ms: int = 200,
    remain_ms: int = 100,
) -> Optional[List[Tuple[int, int]]]:
    """
    静音切除要保留的采样区间 [(start, end), ...]，按顺序且互不重叠；无需切除时返回 None。

    规则：
    1. 只切除连续静音 > min_silence_ms 的段落
    2. 在真正的静音段前后各保留 remain_ms
    3. 短暂的弱音不会被切除
    """
    if not pcm_data or threshold <= 0:
        return None
    import numpy as np

    # 转为int16数组并计算音量
    audio = np.frombuffer(pcm_data, dtype=np.int16)
    volume = np.abs(audio.astype(np.int32))

    # 滑动窗口平滑（避免误切单个采样点）
    window = max(1, sample_rate // 100)  # 10ms窗口
    smoothed = np.convolve(volume, np.ones(window) / window, mode="same")

    # 检测所有静音段的起止位置
    is_silence = smoothed <= threshold
    silence_bounds = np.diff(np.concatenate([[0], is_silence.astype(int), [0]]))
    silence_starts = np.where(silence_bounds == 1)[0]
    silence_ends = np.where(silence_bounds == -1)[0]

    min_silence_samples = int(sample_rate * min_silence_ms / 1000)
    remain_samples = int(sample_rate * remain_ms / 1000)
    keep_ranges: List[Tuple[int, int]] = []
    last_end = 0
    for sil_start, sil_end in zip(silence_starts, silence_ends):
        if sil_end - sil_start > min_silence_samples:
            # 长静音：保留语音段 + 静音前后各remain_ms。静音短于两份 remain_ms 时两头会相接，
            # 下一段从上一段结尾接着取，避免重复播放同一段声音。
            cut = int(sil_start + remain_samples)
            keep_ranges.append((last_end, cut))
            last_end = max(int(sil_end - remain_samples), cut)
        # 短静音：不切除，自然包含在keep_ranges中
    if not keep_ranges:
        return None
    keep_ranges.append((last_end, len(audio)))
    return [(start, end) for start, end in keep_ranges if end > start]


def remove_silence_from_pcm(
    pcm_data: bytes,
    sample_rate: int = 48000,
    threshold: int = 400,
    min_silence_ms: int = 200,
    remain_ms: int = 100,
) -> bytes:
    """从PCM音频中移除过长的静音段（内存处理），规则见 silence_keep_ranges。"""
    try:
        ranges = silence_keep_ranges(pcm_data, sample_rate, threshold, min_silence_ms, remain_ms)
        return _keep(pcm_data, ranges)
    except Exception as e:
        logger.warning(f"静音切除失败: {e}")
        return pcm_data


def _keep(pcm_data: bytes, ranges: Optional[List[Tuple[int, int]]]) -> bytes:
    if not ranges:
        return pcm_data
    return b"".join(pcm_data[start * 2:end * 2] for start, end in ranges)


def _trimmed_time(seconds: float, ranges: Optional[List[Tuple[int, int]]], sample_rate: int) -> float:
    """原录音里的时间点在切除静音后的位置；落在被切掉的静音里时，取切口处。"""
    if not ranges:
        return seconds
    sample, kept = seconds * sample_rate, 0
    for start, end in ranges:
        if sample < start:
            break
        if sample <= end:
            return (kept + sample - start) / sample_rate
        kept += end - start
    return kept / sample_rate


def _speech_end(pcm_data: bytes, after: float, limit: float, sample_rate: int, threshold: int = 400) -> float:
    """after 之后声音第一次安静下来（至少 80ms）的时间，最晚到 limit。"""
    import numpy as np

    audio = np.frombuffer(pcm_data, dtype=np.int16)
    frame = sample_rate // 100  # 10ms
    first, last = int(after * sample_rate) // frame, min(len(audio), int(limit * sample_rate)) // frame
    quiet = 0
    for index in range(first, last):
        chunk = audio[index * frame:(index + 1) * frame]
        quiet = quiet + 1 if chunk.size and np.abs(chunk.astype(np.int32)).mean() <= threshold else 0
        if quiet >= 8:
            return (index - 7) * frame / sample_rate
    return limit


def build_timing(sentences, pcm_data: bytes, ranges, sample_rate: int = 48000) -> Dict[str, Any]:
    """火山返回的句子和字级时间戳 → 与最终录音对齐的时间轴。

    火山把标点后的停顿算进前一个字，所以同一句里字与字首尾相接；中间有空隙通常是后一个字
    （尤其数字、英文）被标晚了，这里把它接到前一个字的结尾。句末按实际声音结束的位置校正，
    因为低置信度的句尾常被标早。时间先换算到切除静音后的录音上。"""
    seconds = len(pcm_data) / 2 / sample_rate
    timed = []
    for sentence in sentences:
        words = []
        for word in sentence["words"]:
            start = _trimmed_time(float(word["startTime"]), ranges, sample_rate)
            end = max(start, _trimmed_time(float(word["endTime"]), ranges, sample_rate))
            if words:
                start = words[-1]["end"]
                end = max(start, end)
            words.append({"text": word["word"], "start": start, "end": end,
                          "confidence": round(float(word.get("confidence", 0)), 2)})
        timed.append({"text": sentence["text"], "words": words})
    for index, sentence in enumerate(timed):
        words = sentence["words"]
        limit = timed[index + 1]["words"][0]["start"] if index + 1 < len(timed) else seconds
        words[-1]["end"] = max(words[-1]["end"], _speech_end(pcm_data, words[-1]["end"], limit, sample_rate))
        for word in words:
            word["start"], word["end"] = round(word["start"], 3), round(word["end"], 3)
        sentence.update(start=words[0]["start"], end=words[-1]["end"])
    return {"seconds": round(seconds, 3), "sentences": [{key: s[key] for key in ("text", "start", "end", "words")} for s in timed]}


def _create_wav_header(pcm_data_size, sample_rate=48000, channels=1, bits_per_sample=16):
    """创建 WAV 文件头（44 字节）"""
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8

    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + pcm_data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        pcm_data_size,
    )


def _request_bytedance_tts_pcm(
    *,
    api_key: str,
    resource_id: str,
    text: str,
    voice: str,
    model: str,
    speech_rate: int,
    loudness_rate: int,
    emotion: str,
    emotion_scale: int,
    subtitles: bool = False,
) -> Tuple[bytes, List[Dict[str, Any]]]:
    """调用火山引擎TTS API，返回PCM音频数据和字级时间戳（subtitles 开启且音色支持时）。"""
    url = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "Content-Type": "application/json",
    }
    payload = {
        "user": {"uid": "aigc_video_user"},
        "req_params": {
            "text": text,
            "speaker": voice,
            "audio_params": {
                "format": "pcm",
                "sample_rate": 48000,
                "emotion": emotion,
                "emotion_scale": emotion_scale,
                "speech_rate": speech_rate,
                "loudness_rate": loudness_rate,
                **({"enable_subtitle": True} if subtitles else {}),
            },
            "additions": json.dumps({"silence_duration": 0}),
        },
    }
    if model:
        payload["req_params"]["model"] = model

    session = requests.Session()
    response = None
    try:
        response = session.post(url, headers=headers, json=payload, stream=True, timeout=30)
        if response.status_code != 200:
            error_text = response.text
            logger.error(f"TTS API请求失败 - 状态码: {response.status_code}, 错误: {error_text}")
            raise APIError(f"TTS API请求失败（{response.status_code}）: {error_text}",
                           retryable=response.status_code in (408, 429) or response.status_code >= 500)

        logid = response.headers.get("X-Tt-Logid", "N/A")
        logger.debug(f"TTS 请求成功 - X-Tt-Logid: {logid}")

        audio_data = bytearray()
        sentences = []
        for chunk in response.iter_lines(decode_unicode=True):
            if not chunk:
                continue
            try:
                data = json.loads(chunk)
            except json.JSONDecodeError:
                logger.warning(f"无法解析响应数据: {chunk[:100]}")
                continue

            sentence = data.get("sentence")
            # One extra entry repeats the whole text without words; only sentences with words are timed.
            if data.get("code", 0) == 0 and isinstance(sentence, dict) and sentence.get("words"):
                sentences.append(sentence)
            if data.get("code", 0) == 0 and "data" in data and data["data"]:
                audio_data.extend(base64.b64decode(data["data"]))
                continue
            if data.get("code", 0) == 20000000:
                break
            if data.get("code", 0) > 0:
                error_msg = data.get("message", "未知错误")
                logger.error(f"TTS API返回错误: code={data.get('code')}, message={error_msg}")
                raise APIError(f"TTS API错误: {error_msg}")

        if not audio_data:
            raise APIError("未接收到音频数据")
        return bytes(audio_data), sentences
    finally:
        if response is not None:
            response.close()
        session.close()


TIMED_RESOURCES = {"seed-tts-2.0", "seed-icl-2.0"}


def resource_for_voice(voice: str, configured: str) -> str:
    """音色所属的资源 ID。音色与资源不匹配时火山会拒绝（code=55000000），因此按音色类型选择。"""
    voice, configured = (voice or "").strip(), (configured or "").strip()
    if voice.startswith("S_"):  # 声音复刻音色
        return configured if configured.startswith("seed-icl") else "seed-icl-2.0"
    if "_uranus_" in voice or voice.startswith("saturn_"):  # 语音合成 2.0 音色
        return "seed-tts-2.0"
    if voice.endswith(("_moon_bigtts", "_mars_bigtts")):  # 语音合成 1.0 音色
        return "seed-tts-1.0"
    return configured


@retry_on_failure(max_retries=1, delay=1.0)
def text_to_audio_bytedance(
    text,
    output_filename,
    voice="zh_male_yuanboxiaoshu_moon_bigtts",
    model: str = "",
    speech_rate: int = 0,
    loudness_rate: int = 0,
    emotion: str = "neutral",
    emotion_scale: int = 4,
    mute_cut_threshold: int = 400,
    mute_cut_min_silence_ms: int = 200,
    mute_cut_remain_ms: int = 100,
):
    """
    使用火山引擎TTS API进行语音合成（HTTP 单向流式接口）

    Args:
        text: 要合成的文本
        output_filename: 输出文件路径（.wav格式）
        voice: 音色ID
        model: 复刻2.0效果模型（如 seed-tts-2.0-expressive / seed-tts-2.0-standard）
        speech_rate: 语速 (-50到100, 0=正常, 100=2倍速, -50=0.5倍速)
        loudness_rate: 音量 (-50到100, 0=正常, 100=2倍音量, -50=0.5倍音量)
        emotion: 情感类型 (neutral, happy, sad等)
        emotion_scale: 情感强度 (1-5)
        mute_cut_remain_ms: 静音切除后保留的静音时长 (毫秒, 默认400)
        mute_cut_threshold: 静音切除阈值 (默认100)

    Returns:
        bool: 成功返回True
    """
    # ============ 1. 验证配置 ============
    api_key = (getattr(config, "BYTEDANCE_TTS_API_KEY", "") or "").strip()
    if not api_key:
        raise APIError("豆包语音配置不完整，请检查 BYTEDANCE_TTS_API_KEY", retryable=False)

    voice = (voice or "").strip()
    if not voice:
        raise APIError("未设置配音音色，请在设置中填写音色 ID", retryable=False)
    RESOURCE_ID = resource_for_voice(voice, config.RESOURCE_ID)
    if RESOURCE_ID != config.RESOURCE_ID:
        logger.info(f"音色 {voice} 属于资源 {RESOURCE_ID}，已替换配置的 {config.RESOURCE_ID}")
    MODEL = str(model or getattr(config, "TTS_MODEL", "") or "").strip()

    # ============ 2. 参数验证和规范化 ============
    speech_rate = max(-50, min(100, int(speech_rate or 0)))
    loudness_rate = max(-50, min(100, int(loudness_rate or 0)))
    emotion = str(emotion or "neutral")
    emotion_scale = max(1, min(5, int(emotion_scale or 4)))

    logger.info(
        f"调用火山引擎TTS API - 资源: {RESOURCE_ID}, 音色: {voice}, "
        f"模型: {MODEL or '<default>'}, 文本长度: {len(text)}字符, 语速: {speech_rate}, 音量: {loudness_rate}, "
        f"情感: {emotion}({emotion_scale})"
    )

    try:
        # ============ 3. 请求TTS API，获取PCM ============
        # 字级时间戳只有语音合成 2.0 和声音复刻 2.0 的音色支持。
        audio_data, sentences = _request_bytedance_tts_pcm(
            api_key=api_key,
            resource_id=RESOURCE_ID,
            text=text,
            voice=voice,
            model=MODEL,
            speech_rate=speech_rate,
            loudness_rate=loudness_rate,
            emotion=emotion,
            emotion_scale=emotion_scale,
            subtitles=RESOURCE_ID in TIMED_RESOURCES,
        )

        # ============ 4. 本地静音切除（记下保留的区间，时间戳跟着换算） ============
        original_size = len(audio_data)
        ranges = None
        if mute_cut_threshold > 0:
            try:
                ranges = silence_keep_ranges(audio_data, 48000, mute_cut_threshold, mute_cut_min_silence_ms, mute_cut_remain_ms)
            except Exception as e:
                logger.warning(f"静音切除失败: {e}")
            audio_data = _keep(audio_data, ranges)
            reduction = (1 - len(audio_data) / original_size) * 100 if original_size > 0 else 0
            logger.info(
                f"静音切除: {original_size/1024:.1f}KB → {len(audio_data)/1024:.1f}KB (-{reduction:.1f}%)"
            )

        # ============ 5. 保存音频文件 ============
        ensure_directory_exists(os.path.dirname(output_filename))

        # 添加 WAV header 并保存为 WAV 文件
        wav_header = _create_wav_header(len(audio_data), sample_rate=48000)
        with open(output_filename, "wb") as f:
            f.write(wav_header)
            f.write(audio_data)

        # ============ 6. 时间轴：与录音同名的 .json；没有时间戳时删掉旧的，免得对不上新录音 ============
        timing_path = os.path.splitext(output_filename)[0] + ".json"
        timing = None
        if sentences:
            try:
                timing = {"text": text, "trimmed_pauses": bool(ranges), **build_timing(sentences, audio_data, ranges)}
            except Exception as e:
                logger.warning(f"时间轴生成失败: {e}")
        if timing:
            with open(timing_path, "w", encoding="utf-8") as f:
                json.dump(timing, f, ensure_ascii=False, indent=1)
        elif os.path.exists(timing_path):
            os.remove(timing_path)

        logger.info(f"语音合成成功 - 文件: {(len(audio_data) + 44)/1024:.1f} KB, 已保存: {output_filename}")

        return True

    except APIError:
        raise
    except Exception as e:
        logger.error(f"语音合成失败: {str(e)}")
        raise APIError(f"语音合成失败: {str(e)}") from e


__all__ = ["resource_for_voice", "text_to_audio_bytedance"]
