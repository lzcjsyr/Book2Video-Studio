"""Locations of the engine's bundled resources.

Studio keeps the engine read-only and sets AIGC_VIDEO_LIBRARY_DIR to the user's
editable copy of ``prompts/`` and ``skills/``; without it (tests, tools) the
engine's own copy is used. Music and default settings always come from the
engine itself.
"""

import os
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ENV = "AIGC_VIDEO_LIBRARY_DIR"


def library_root() -> Path:
    override = os.environ.get(LIBRARY_ENV, "").strip()
    return Path(override).resolve() if override else ENGINE_ROOT


def prompts_dir() -> Path:
    return library_root() / "prompts"


def skills_dir() -> Path:
    return library_root() / "skills"
