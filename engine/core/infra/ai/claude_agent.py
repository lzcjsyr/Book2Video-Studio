from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    UserMessage,
    query,
)

from core.config import config
from core.infra.ai.task_context import agent_rules, write_task_context
from core.prompts import STEP1_5_SEGMENT_PROMPT_TEMPLATE

STEP1_5_AGENT_TOOLS = ["Read", "Write"]


_MAX_LOG_FIELD_CHARS = 12_000
_OMITTED_BASH_READ_CONTENT = "[omitted: Bash text-window output from extracted source text]"
_OMITTED_SKILL_REFERENCE_CONTENT = "[omitted: skill/reference file content]"
_OMITTED_PERSISTED_FILE_CONTENT = "[omitted: file content already persisted]"
_OMITTED_PERSISTED_READ_CONTENT = "[omitted: persisted draft/raw file read]"
_OMITTED_LEDGER_WRITE_COMMAND = "[omitted: Bash write command for _coverage_ledger.json]"
_BASH_READ_WINDOW_RE = re.compile(r"\b(sed\s+-n|head|tail)\b")


def _get_anthropic_compatible_config(server: str, context: str) -> tuple[str, str, str]:
    server = (server or "").strip().lower()
    if server == "mimo":
        return "https://api.xiaomimimo.com/anthropic", (config.MIMO_API_KEY or "").strip(), "MIMO_API_KEY"
    elif server == "kimi":
        return "https://api.moonshot.cn/anthropic", (config.KIMI_API_KEY or "").strip(), "KIMI_API_KEY 或 MOONSHOT_API_KEY"
    elif server == "deepseek":
        return "https://api.deepseek.com/anthropic", (config.DEEPSEEK_API_KEY or "").strip(), "DEEPSEEK_API_KEY"
    elif server == "siliconflow":
        return "https://api.siliconflow.cn/", (config.SILICONFLOW_KEY or "").strip(), "SILICONFLOW_KEY"
    elif server == "openrouter":
        return "https://openrouter.ai/api", (config.OPENROUTER_API_KEY or "").strip(), "OPENROUTER_API_KEY"
    elif server == "volcengine":
        return "https://ark.cn-beijing.volces.com/api/compatible", (config.VOLCENGINE_API_KEY or "").strip(), "VOLCENGINE_API_KEY"
    else:
        labels = {
            "step1": "步骤1",
            "step1_5": "步骤1.5",
            "step4": "步骤4 HTML Motion Agent",
        }
        label = labels.get(context, context)
        raise ValueError(f"不支持的{label} LLM服务商: {server}，支持的服务商: mimo, kimi, deepseek, siliconflow, openrouter, volcengine")


def _normalize_anthropic_agent_model(server: str, model: str | None) -> str:
    return (model or "").strip()


def _anthropic_agent_options_model(server: str, model: str | None) -> str | None:
    if (server or "").strip().lower() == "deepseek":
        return None
    return _normalize_anthropic_agent_model(server, model)


def _normalize_anthropic_agent_base_url(server: str, base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if (server or "").strip().lower() == "mimo" and normalized in {
        "https://api.xiaomimimo.com", "https://api.xiaomimimo.com/v1",
    }:
        return "https://api.xiaomimimo.com/anthropic"
    if (server or "").strip().lower() == "deepseek" and normalized in {
        "https://api.deepseek.com",
        "https://api.deepseek.com/v1",
    }:
        return "https://api.deepseek.com/anthropic"
    return normalized


def _build_anthropic_agent_env(*, server: str, model: str, context: str, config_name: str) -> dict[str, str]:
    server = (server or "").strip().lower()
    if not server:
        raise RuntimeError(f"{config_name} 不能为空（mimo/kimi/siliconflow/openrouter/volcengine/deepseek）")

    base_url, api_key, key_name = _get_anthropic_compatible_config(server, context)
    base_url = _normalize_anthropic_agent_base_url(server, base_url)

    if not api_key:
        raise RuntimeError(f"{config_name}={server} 需要 {key_name}（.env），用于驱动 Claude Agent SDK")

    env = {
        "ANTHROPIC_BASE_URL": base_url,
        # OpenRouter's Claude Code integration requires the API key to be
        # supplied only as the auth token. A non-empty ANTHROPIC_API_KEY can
        # conflict with ANTHROPIC_AUTH_TOKEN and produce misleading 401s.
        "ANTHROPIC_API_KEY": "" if server == "openrouter" else api_key,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_MODEL": _normalize_anthropic_agent_model(server, model),
    }
    if server == "deepseek":
        env.update(
            {
                "ANTHROPIC_DEFAULT_OPUS_MODEL": _normalize_anthropic_agent_model(server, model),
                "ANTHROPIC_DEFAULT_SONNET_MODEL": _normalize_anthropic_agent_model(server, model),
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash",
                "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-flash",
                "CLAUDE_CODE_EFFORT_LEVEL": "max",
            }
        )
    elif server == "openrouter":
        # Claude Code otherwise reserves up to 32k output tokens per turn.
        # Long tool-driven workflows can be rejected by OpenRouter budget
        # limits even though individual tool calls need far less output.
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "4096"
        # Trigger compaction before long reading workflows accumulate a
        # six-figure prompt that OpenRouter may reject on key budget limits.
        env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = "40"
    return env


def build_step1_agent_env() -> dict[str, str]:
    return _build_anthropic_agent_env(
        server=config.LLM_SERVER_STEP1,
        model=config.LLM_MODEL_STEP1,
        context="step1",
        config_name="LLM_SERVER_STEP1",
    )


def build_step1_5_agent_env(
    llm_server: str | None = None,
    llm_model: str | None = None,
) -> dict[str, str]:
    return _build_anthropic_agent_env(
        server=llm_server or config.LLM_SERVER_STEP1_5,
        model=llm_model or config.LLM_MODEL_STEP1_5,
        context="step1_5",
        config_name="LLM_SERVER_STEP1_5",
    )


def build_step4_motion_agent_env(
    llm_server: str | None = None,
    llm_model: str | None = None,
    llm_base_url: str | None = None,
) -> dict[str, str]:
    server = (llm_server or getattr(config, "LLM_SERVER_STEP4", "") or "").strip().lower()
    if not server:
        raise RuntimeError("步骤4 HTML Motion Agent 需要配置 LLM_SERVER_STEP4")

    base_url, api_key, key_name = _get_anthropic_compatible_config(server, "step4")

    base_url = (
        (llm_base_url or "").strip()
        or (getattr(config, "LLM_BASE_URL_STEP4_OVERRIDE", "") or "").strip()
        or base_url
    )
    base_url = _normalize_anthropic_agent_base_url(server, base_url)

    if not api_key:
        raise RuntimeError(f"步骤4 HTML Motion Agent 需要 {key_name}（.env）")

    env = {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_API_KEY": api_key,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_MODEL": _normalize_anthropic_agent_model(server, llm_model or config.LLM_MODEL_STEP4),
    }
    if server == "deepseek":
        env.update(
            {
                "ANTHROPIC_DEFAULT_OPUS_MODEL": _normalize_anthropic_agent_model(
                    server, llm_model or config.LLM_MODEL_STEP4
                ),
                "ANTHROPIC_DEFAULT_SONNET_MODEL": _normalize_anthropic_agent_model(
                    server, llm_model or config.LLM_MODEL_STEP4
                ),
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash",
                "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-flash",
                "CLAUDE_CODE_EFFORT_LEVEL": "max",
            }
        )
    return env


def _truncate_for_log(value: Any, *, max_chars: int = _MAX_LOG_FIELD_CHARS) -> Any:
    if isinstance(value, str) and len(value) > max_chars:
        return {
            "_truncated": True,
            "length": len(value),
            "preview": value[:max_chars],
        }
    if isinstance(value, dict):
        return {key: _truncate_for_log(item, max_chars=max_chars) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_for_log(item, max_chars=max_chars) for item in value]
    return value


def _serialize_sdk_message(message: object) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        payload = asdict(message)
        payload["kind"] = "UserMessage"
        return _truncate_for_log(payload)
    if isinstance(message, AssistantMessage):
        payload = asdict(message)
        payload["kind"] = "AssistantMessage"
        return _truncate_for_log(payload)
    if isinstance(message, SystemMessage):
        payload = asdict(message)
        payload["kind"] = "SystemMessage"
        return _truncate_for_log(payload)
    if isinstance(message, ResultMessage):
        payload = asdict(message)
        payload["kind"] = "ResultMessage"
        return payload
    if isinstance(message, StreamEvent):
        payload = asdict(message)
        payload["kind"] = "StreamEvent"
        return _truncate_for_log(payload)
    if is_dataclass(message):
        payload = asdict(message)
        payload["kind"] = type(message).__name__
        return _truncate_for_log(payload)
    return {"kind": type(message).__name__, "repr": repr(message)}


class AgentSessionLog:
    """Append-only JSONL trace for a single Claude Agent invocation."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._omitted_bash_read_tool_ids: set[str] = set()
        self._omitted_persisted_read_tool_ids: set[str] = set()

    @staticmethod
    def _is_extract_window_read_command(command: str) -> bool:
        return bool(re.search(r"(?:^|[/\\])_extract\.(?:txt|md)\b", command)) and bool(
            _BASH_READ_WINDOW_RE.search(command)
        )

    @staticmethod
    def _value_length(value: Any) -> int | None:
        if isinstance(value, str):
            return len(value)
        if isinstance(value, dict) and value.get("_truncated") is True and isinstance(value.get("length"), int):
            return value["length"]
        return None

    @staticmethod
    def _is_skill_reference_path(path: str) -> bool:
        return "/skills/" in path and (
            path.endswith("/SKILL.md") or "/references/" in path
        )

    @staticmethod
    def _is_large_persisted_file_write(item: dict[str, Any]) -> bool:
        if item.get("name") != "Write":
            return False
        input_payload = item.get("input")
        if not isinstance(input_payload, dict):
            return False
        content = input_payload.get("content")
        return isinstance(content, str) and len(content) > 1000

    @staticmethod
    def _is_coverage_ledger_write_command(command: str) -> bool:
        return "_coverage_ledger.json" in command and "cat >" in command

    @staticmethod
    def _is_persisted_read_path(path: str) -> bool:
        return Path(path).name in {
            "_draft_v1.txt",
            "_draft_v2_structure.txt",
            "_draft_final.txt",
            "_data_check_ledger.json",
            "_revision_audit.json",
            "raw.json",
        }

    def _compact_message_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = payload.get("message")
        if not isinstance(message, dict):
            return payload

        content = message.get("content")
        if not isinstance(content, list):
            return payload

        changed = False
        omitted_tool_result_ids: set[str] = set()
        omit_skill_reference_result = False
        tool_use_result = message.get("tool_use_result")
        if isinstance(tool_use_result, dict):
            file_result = tool_use_result.get("file")
            if isinstance(file_result, dict):
                file_path = file_result.get("filePath")
                omit_skill_reference_result = isinstance(file_path, str) and self._is_skill_reference_path(file_path)

        compacted_content: list[Any] = []
        for item in content:
            if not isinstance(item, dict):
                compacted_content.append(item)
                continue

            if self._is_large_persisted_file_write(item):
                compacted_item = dict(item)
                compacted_input = dict(item.get("input") or {})
                content_value = compacted_input.get("content")
                compacted_input["content"] = _OMITTED_PERSISTED_FILE_CONTENT
                compacted_input["content_length"] = len(content_value) if isinstance(content_value, str) else None
                compacted_input["log_omitted"] = "persisted_file_content"
                compacted_item["input"] = compacted_input
                compacted_content.append(compacted_item)
                changed = True
                continue

            if item.get("name") == "Read":
                input_payload = item.get("input")
                file_path = input_payload.get("file_path") if isinstance(input_payload, dict) else None
                if isinstance(file_path, str) and self._is_persisted_read_path(file_path):
                    tool_id = item.get("id")
                    if isinstance(tool_id, str):
                        self._omitted_persisted_read_tool_ids.add(tool_id)
                    compacted_item = dict(item)
                    compacted_item["log_omitted"] = "persisted_file_read"
                    compacted_content.append(compacted_item)
                    changed = True
                    continue

            if item.get("name") == "Bash":
                input_payload = item.get("input")
                command = input_payload.get("command") if isinstance(input_payload, dict) else None
                if isinstance(command, str) and self._is_extract_window_read_command(command):
                    tool_id = item.get("id")
                    if isinstance(tool_id, str):
                        self._omitted_bash_read_tool_ids.add(tool_id)
                    compacted_item = dict(item)
                    compacted_input = dict(input_payload or {})
                    compacted_input["command_length"] = len(command)
                    compacted_item["input"] = compacted_input
                    compacted_item["log_omitted"] = "bash_extract_window_read"
                    compacted_content.append(compacted_item)
                    changed = True
                    continue
                if isinstance(command, str) and self._is_coverage_ledger_write_command(command):
                    compacted_item = dict(item)
                    compacted_input = dict(input_payload or {})
                    compacted_input["command"] = _OMITTED_LEDGER_WRITE_COMMAND
                    compacted_input["command_length"] = len(command)
                    compacted_item["input"] = compacted_input
                    compacted_item["log_omitted"] = "coverage_ledger_write"
                    compacted_content.append(compacted_item)
                    changed = True
                    continue

            tool_use_id = item.get("tool_use_id")
            is_error = item.get("is_error") is True
            if isinstance(tool_use_id, str) and tool_use_id in self._omitted_bash_read_tool_ids and not is_error:
                content_value = item.get("content")
                compacted_item = dict(item)
                compacted_item["content"] = _OMITTED_BASH_READ_CONTENT
                compacted_item["content_length"] = self._value_length(content_value)
                compacted_item["log_omitted"] = "bash_extract_window_output"
                omitted_tool_result_ids.add(tool_use_id)
                compacted_content.append(compacted_item)
                changed = True
                continue

            if isinstance(tool_use_id, str) and tool_use_id in self._omitted_persisted_read_tool_ids and not is_error:
                content_value = item.get("content")
                compacted_item = dict(item)
                compacted_item["content"] = _OMITTED_PERSISTED_READ_CONTENT
                compacted_item["content_length"] = len(content_value) if isinstance(content_value, str) else None
                compacted_item["log_omitted"] = "persisted_file_read"
                compacted_content.append(compacted_item)
                changed = True
                continue

            if omit_skill_reference_result and not is_error:
                content_value = item.get("content")
                compacted_item = dict(item)
                compacted_item["content"] = _OMITTED_SKILL_REFERENCE_CONTENT
                compacted_item["content_length"] = len(content_value) if isinstance(content_value, str) else None
                compacted_item["log_omitted"] = "skill_reference_read"
                compacted_content.append(compacted_item)
                changed = True
                continue

            compacted_content.append(item)

        if not changed:
            return payload

        compacted_message = dict(message)
        compacted_message["content"] = compacted_content
        tool_use_result = compacted_message.get("tool_use_result")
        if isinstance(tool_use_result, dict):
            file_result = tool_use_result.get("file")
            if omit_skill_reference_result and isinstance(file_result, dict):
                compacted_file = dict(file_result)
                file_content = compacted_file.get("content")
                if isinstance(file_content, str):
                    compacted_file["content"] = _OMITTED_SKILL_REFERENCE_CONTENT
                    compacted_file["content_length"] = len(file_content)
                    compacted_file["log_omitted"] = "skill_reference_read"
                    compacted_result = dict(tool_use_result)
                    compacted_result["file"] = compacted_file
                    compacted_message["tool_use_result"] = compacted_result
                    tool_use_result = compacted_result
            if isinstance(file_result, dict):
                file_path = file_result.get("filePath")
                if isinstance(file_path, str) and self._is_persisted_read_path(file_path):
                    compacted_file = dict(file_result)
                    file_content = compacted_file.get("content")
                    if isinstance(file_content, str):
                        compacted_file["content"] = _OMITTED_PERSISTED_READ_CONTENT
                        compacted_file["content_length"] = len(file_content)
                        compacted_file["log_omitted"] = "persisted_file_read"
                        compacted_result = dict(tool_use_result)
                        compacted_result["file"] = compacted_file
                        compacted_message["tool_use_result"] = compacted_result
                        tool_use_result = compacted_result
            tool_use_id = tool_use_result.get("tool_use_id")
            is_error = tool_use_result.get("is_error") is True
            should_omit_result = (
                isinstance(tool_use_id, str) and tool_use_id in self._omitted_bash_read_tool_ids
            ) or (not isinstance(tool_use_id, str) and bool(omitted_tool_result_ids))
            if should_omit_result and not is_error:
                compacted_result = dict(tool_use_result)
                stdout = compacted_result.get("stdout")
                stdout_length = self._value_length(stdout)
                if stdout is not None:
                    compacted_result["stdout"] = _OMITTED_BASH_READ_CONTENT
                    compacted_result["stdout_length"] = stdout_length
                    compacted_result["log_omitted"] = "bash_extract_window_output"
                    compacted_message["tool_use_result"] = compacted_result
        return {**payload, "message": compacted_message}

    @staticmethod
    def _is_thinking_token_progress(payload: dict[str, Any]) -> bool:
        message = payload.get("message")
        if not isinstance(message, dict) or message.get("kind") != "SystemMessage":
            return False
        data = message.get("data")
        data_subtype = data.get("subtype") if isinstance(data, dict) else None
        return message.get("subtype") == "thinking_tokens" or data_subtype == "thinking_tokens"

    def append(self, event: str, payload: dict[str, Any]) -> None:
        if event == "message" and self._is_thinking_token_progress(payload):
            return
        self._seq += 1
        if event == "message":
            payload = self._compact_message_payload(payload)
        record = {
            "seq": self._seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str))
            handle.write("\n")


def _build_step1_5_segment_prompt(
    *,
    raw_json_path: str,
    output_json_path: str,
    target_segments: int,
) -> str:
    return (
        STEP1_5_SEGMENT_PROMPT_TEMPLATE
        .replace("{raw_json_path}", raw_json_path)
        .replace("{output_json_path}", output_json_path)
        .replace("{target_segments}", str(target_segments))
    )


async def _run_step1_5_segment_agent_async(
    *,
    raw_json_path: str,
    output_json_path: str,
    target_segments: int,
    session_log_path: str,
    repo_root: str,
    llm_server: str | None = None,
    llm_model: str | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_json_path)
    try:
        if output_path.exists():
            output_path.unlink()
    except Exception:
        pass

    prompt = _build_step1_5_segment_prompt(
        raw_json_path=raw_json_path,
        output_json_path=output_json_path,
        target_segments=target_segments,
    )
    task = write_task_context(Path(session_log_path).with_suffix('.task.json'), stage='2', cwd=output_path.parent,
        inputs=[raw_json_path], writable=[output_path], protected=[raw_json_path], facts={'target_segments': target_segments})
    prompt = agent_rules('rules', task_file=task) + '\n\n' + prompt
    tools = list(STEP1_5_AGENT_TOOLS)
    effective_llm_server = llm_server or config.LLM_SERVER_STEP1_5
    effective_llm_model = llm_model or config.LLM_MODEL_STEP1_5
    options = ClaudeAgentOptions(
        cwd=str(output_path.parent.resolve()),
        model=_anthropic_agent_options_model(effective_llm_server, effective_llm_model),
        tools=tools,
        allowed_tools=tools,
        permission_mode="acceptEdits",
        max_turns=80,
        add_dirs=[str(Path(raw_json_path).parent)],
        env=build_step1_5_agent_env(llm_server=llm_server, llm_model=llm_model),
        setting_sources=[],
    )
    session_log = AgentSessionLog(session_log_path)
    session_log.append(
        "session_start",
        {
            "step": "step_1_5_segment_visualizer",
            "raw_json_path": raw_json_path,
            "output_json_path": output_json_path,
            "target_segments": target_segments,
            "repo_root": repo_root,
            "llm_server": llm_server,
            "llm_model": llm_model,
            "tools": tools,
            "prompt": prompt,
        },
    )

    result_message: ResultMessage | None = None
    try:
        async for message in query(prompt=prompt, options=options):
            session_log.append("message", {"message": _serialize_sdk_message(message)})
            if isinstance(message, ResultMessage):
                result_message = message
                if message.is_error:
                    raise RuntimeError(message.result or "Claude Agent Step 1.5 failed")
    except Exception as exc:
        session_log.append("session_error", {"error_type": type(exc).__name__, "error": str(exc)})
        raise
    finally:
        session_log.append(
            "session_end",
            {
                "success": bool(result_message and not result_message.is_error),
                "result": _serialize_sdk_message(result_message) if result_message else None,
            },
        )

    if not output_path.exists():
        raise FileNotFoundError(f"Claude Agent未生成分段文件: {output_json_path}")

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(segments, list):
        raise ValueError("Claude Agent分段文件缺少 segments 数组")
    return segments


def run_step1_5_segment_agent(
    *,
    raw_json_path: str,
    output_json_path: str,
    target_segments: int,
    session_log_path: str,
    repo_root: str,
    llm_server: str | None = None,
    llm_model: str | None = None,
) -> list[dict[str, Any]]:
    runner = partial(
        _run_step1_5_segment_agent_async,
        raw_json_path=raw_json_path,
        output_json_path=output_json_path,
        target_segments=target_segments,
        session_log_path=session_log_path,
        repo_root=repo_root,
        llm_server=llm_server,
        llm_model=llm_model,
    )
    return anyio.run(runner)
