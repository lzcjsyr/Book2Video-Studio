# Third-party components bundled with Book2Video Studio

Book2Video Studio runs these programs as separate processes. Each keeps its own
license; review them before distributing the application.

| Component | Version | License | Source |
|---|---|---|---|
| CPython (python-build-standalone) | 3.13.15 (20260924) | Python Software Foundation License | https://github.com/astral-sh/python-build-standalone |
| Python packages | see `requirements.lock` | various (MIT, BSD, Apache-2.0, AGPL-3.0 for PyMuPDF) | https://pypi.org |
| Claude Code CLI (bundled by claude-agent-sdk) | per claude-agent-sdk 0.2.93 | Anthropic Commercial Terms | https://github.com/anthropics/claude-agent-sdk-python |
| Node.js and npm | 22.23.3 | MIT; npm under Artistic-2.0 | https://nodejs.org |
| HyperFrames CLI and its npm dependencies | 0.7.10 | not declared by the package; confirm with HeyGen before public distribution | https://github.com/heygen-com/hyperframes |
| Chrome Headless Shell | pinned by HyperFrames | BSD-3-Clause and Chromium third-party licenses | https://developer.chrome.com/blog/chrome-headless-shell |
| FFmpeg and FFprobe (static build by Martin Riedl) | 9.0.2 | GPL-3.0 (built with --enable-gpl --enable-version3) | https://ffmpeg.org, https://ffmpeg.martin-riedl.de |

GPL-3.0 and AGPL-3.0 components require offering their corresponding source
code with the distribution.
