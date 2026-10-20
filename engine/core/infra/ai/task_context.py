"""Small, source-bound task manifests for Studio's Claude Code sessions."""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.resources import prompts_dir

TASK_CONTEXT_VERSION = 2


def write_task_context(path, *, stage, cwd, inputs=(), optional_inputs=(), writable=(), protected=(), facts=None):
    """Write the session's paths and revision metadata, never file bodies or provider configuration.

    What the agent is asked to do lives in the templates; this manifest only says where."""
    path = Path(path).resolve()
    observed = []
    optional = {str(Path(item).resolve()) for item in optional_inputs}
    for value in dict.fromkeys(str(Path(item).resolve()) for item in inputs if item):
        source = Path(value)
        stat = source.stat() if source.exists() else None
        observed.append({"path": value, "exists": stat is not None, "required": value not in optional,
                         "revision": f"{stat.st_size}:{stat.st_mtime_ns}" if source.is_file() else None})
    task = {"version": TASK_CONTEXT_VERSION, "stage": str(stage), "cwd": str(Path(cwd).resolve()), "inputs": observed,
            "writable": list(dict.fromkeys(str(Path(p).resolve()) for p in writable)),
            "protected": list(dict.fromkeys(str(Path(p).resolve()) for p in [*protected, path])),
            "facts": facts or {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def agent_rules(section, **values) -> str:
    """One section of agent_rules.md, for sessions the engine and job runner start: "rules" (with task_file),
    or a session's first message ("start", "automatic"). Studio's service reads it through its library."""
    text = (prompts_dir() / "agent_rules.md").read_text(encoding="utf-8")
    pieces = re.split(r"(?m)^<!-- section: ([a-z_]+) -->\s*\n", text)
    chosen = dict(zip(pieces[1::2], pieces[2::2]))[section]
    return re.sub(r"(?m)^[ \t]*<!--.*-->[ \t]*\n?", "", chosen).strip().format(**values)
