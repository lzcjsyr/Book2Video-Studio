# core Architecture

## Goal

`core` is Book2Video Studio's production engine. The desktop backend runs its
steps 1.5–6 in job processes (writing, step 1, happens in Studio's Claude Code
sessions). Orchestration lives in `pipeline`, business logic in `domain`, and
external systems in `infra`. Packages export nothing themselves: import the
module that defines a name (`from core.pipeline import steps`).

## Layout

```text
core/
├── config.py              # YAML settings, API keys, validation
├── prompts.py             # Loads prompts/ templates and style presets
├── resources.py           # Where prompts/ and skills/ live (Studio's editable library)
├── shared.py              # Logging, errors, retry, JSON and file helpers
│
├── pipeline/
│   └── steps.py           # run_step_1_5 … run_step_6
│
├── domain/                # Business logic
│   ├── summarizer.py      # Step 1.5 segmentation, step 2 summary
│   ├── subtitles.py       # Subtitle line breaking and timing
│   ├── composer.py        # Step 5 video composition (MoviePy)
│   └── metadata.py        # Title, quote and focus-marker helpers
│
└── infra/                 # External systems
    ├── project_paths.py
    ├── ai/
    │   ├── llm_client.py      # OpenAI-compatible text, Doubao/Google image calls
    │   ├── image_providers.py
    │   ├── image_client.py    # Step 4 images, step 6 covers, step 3 voice batch + SRT
    │   ├── tts_client.py      # ByteDance TTS and silence trimming
    │   ├── task_context.py    # Task manifests for Claude Code sessions
    │   └── claude_agent.py    # Agent environments; Agent SDK runs for steps 1.5 and 4
    ├── motion/                # Shared Chrome capture, capabilities, opening and agent clips
    └── media/
        ├── ffmpeg.py
        ├── exporter.py
        └── three_by_four.py   # 3:4 distribution copy
```

## Dependency Rules

1. `pipeline/` may import `domain/`, `infra/`, and the top-level modules.
2. `domain/` holds the logic; it calls `infra/` for providers and media tools.
3. `infra/` never imports `pipeline/`.
4. The desktop backend calls `core.pipeline` as its entry.
