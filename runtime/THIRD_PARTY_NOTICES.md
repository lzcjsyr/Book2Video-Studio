# Third-party components bundled with Grist

Grist runs these programs as separate processes. Each keeps its own
license; review them before distributing the application.

| Component | Version | License | Source |
|---|---|---|---|
| CPython (python-build-standalone) | 3.13.15 (20260924) | Python Software Foundation License | https://github.com/astral-sh/python-build-standalone |
| Python packages | see `requirements.lock` | various (MIT, BSD, Apache-2.0, AGPL-3.0 for PyMuPDF) | https://pypi.org |
| Node.js and npm | 22.23.3 | MIT; npm under Artistic-2.0 | https://nodejs.org |
| Puppeteer Core and browser installer | 25.13.0 / 3.2.4 | Apache-2.0; dependencies MIT, ISC or BSD | https://github.com/puppeteer/puppeteer |
| GSAP (agent video editing) | 3.14.2 | Standard License | https://gsap.com/standard-license/ |
| Three.js (HTML scenes and covers) | 0.160.0 | MIT | https://github.com/mrdoob/three.js |
| Chrome Headless Shell | 152.0.7977.30 | BSD-3-Clause and Chromium third-party licenses | https://developer.chrome.com/blog/chrome-headless-shell |
| FFmpeg and FFprobe | 9.0.2 | GPL-3.0 | https://ffmpeg.org. macOS uses Martin Riedl's static build (`--enable-gpl --enable-version3`, https://ffmpeg.martin-riedl.de). Windows uses Gyan Doshi's essentials build (https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2). |

GPL-3.0 and AGPL-3.0 components require offering their corresponding source
code with the distribution.
