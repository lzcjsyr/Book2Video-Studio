# Book2Video Studio — detailed behavior notes

Reference notes for how each part of the app behaves (English). For an overview see the [README](../README.md).


A standalone Tauri 2 desktop app that turns books and manuscripts into narrated videos. It ships everything production needs, so nothing else has to be installed:

- the production engine in `engine/`: code, prompts, skills, background music and default settings;
- Python 3.13 with the engine's packages, including the Claude Code CLI bundled by `claude-agent-sdk`;
- Node.js 22 with the pinned browser libraries (Puppeteer Core, GSAP, Three.js) and the Chrome Headless Shell build motion clips render with;
- static FFmpeg and FFprobe builds.

The release build is `src-tauri/target/release/bundle/macos/Book2Video Studio.app`. **System check / 系统检查** at the bottom of the sidebar lists every bundled component and opens the projects and library folders.

## Where your files live

- **Projects:** new projects go to `~/Movies/Book2Video Studio` (the Videos folder on Windows), or any location chosen when creating one. **Open project folder** adds an existing production folder (from a backup or another computer) to the list.
- **Skills and templates:** an editable copy in the app data folder (`~/Library/Application Support/com.book2video.studio/library`). App updates add new bundled files and refresh files you never changed; your edits and deletions are kept.
- **Logs:** the project service writes `logs/service.log` in the same app data folder.
- **Protected folders:** macOS asks once before the app reads projects in Desktop, Documents or Downloads; the app shows "Starting production service" until you answer. If access is denied, those projects are listed as "Cannot open this folder". Unsigned builds are a new app to macOS after every rebuild, so the question returns.

## Develop

```sh
npm install
npm run stage     # assemble build/runtime, build/engine and build/backend
npm run desktop   # stage, then run the app in development
```

`npm run stage` downloads pinned, checksum-verified copies of Python, Node.js, FFmpeg/FFprobe and Chrome into `build/downloads`, installs `runtime/requirements.lock` and `runtime/motion/package-lock.json`, copies the engine from `engine/`, and smoke-tests every component, including that the engine provides every `core` module and name the backend imports. The runtime is rebuilt only when a pin or lock file changes. Each staged folder is assembled beside its target and swapped in only when complete, so a failed stage leaves the previous build working. `engine/music` holds licensed tracks that stay out of git; whatever is there is bundled.

Use **Settings → Language / 设置 → 语言** to switch between **English** and **简体中文**. Both choices are visible above the Production/API keys tabs, even before opening a project. Switching applies immediately, preserves unsaved edits and active terminals, and remembers your choice on this computer. The first launch follows the system's preferred language, choosing Simplified Chinese for Chinese language preferences and English otherwise.

The interface language does not translate your manuscript, filenames, model IDs, or Claude Code terminal output. Production language follows the source, skills, and your agent instructions. Native file dialogs and media controls follow the operating system.

Open **Settings → API keys** to save provider keys on this computer. Keys are shared across projects and stored in macOS Keychain or Windows Credential Manager. Each provider has a masked entry, a saved-status indicator, and Save/Remove actions. Saved values are never returned to the interface; enter a new value to replace a key. Blank fields preserve existing keys.

New production jobs and new Claude Code sessions use the saved keys. Running jobs and terminal sessions keep their current credentials. Keys are not copied into project settings, history, or launch manifests. Google ADC continues to use the existing local Google Cloud login. API key management is available before opening a project and requires the Tauri desktop app.

Image and cover generation support Doubao, Google with an API key, and Google ADC. SiliconFlow is available for text agents only.

The application icon combines an open book and a play button in the workspace's charcoal and warm peach colors. Its editable vector source is `public/app-icon.svg`; native Mac `.icns` and Windows `.ico` assets are in `src-tauri/icons` and explicitly included in the Tauri bundle configuration. Generate platform assets after editing the source with `npm run tauri -- icon public/app-icon.svg`, then rebuild the app. The browser favicon and in-app book/play mark use the same identity.

**Removing projects.** The trash icon beside **Projects** opens **Manage projects**. Each row has its own remove button, and **Remove all** clears the whole list. Removing only takes a project off the list; its folder and files stay on disk (the list is remembered in `hidden-projects.json` in the app data folder, and **Open project folder** brings a project back). A checkbox in the confirmation step, **Also permanently delete the project files**, deletes the folders instead, which cannot be undone. A project with a running job or agent cannot be removed.

**Project location.** **New project** asks for a name and a location, which starts at the last location used (the projects folder the first time) and has a **Browse** button. The project is created as `<location>/<name>` (a number is added if that folder exists). Projects in the projects folder are listed directly; a project saved anywhere else is remembered in the app data folder's `projects.json` and appears in the project list with its folder name. **Show project folder** (bottom of the sidebar) opens the current project's folder. The Step 1 Claude Code terminal starts in the project folder and is given access to the skills library and the source.

In Step 1, **Writing mode** and **Opening quote** collapse to a one-line summary; their open/closed state is remembered. The manuscript box is taller and can be resized.

Open **Settings → Skills & templates / 设置 → Skills 与模板** to edit the actual generation rules. The same editor is available from the document icon in the Step 1, 2, 4, and 6 headers and **Edit editing skills** in Step 5. Writing, motion and assembly Skills offer a file selector for `SKILL.md` and text references/scripts, a source-folder button, and local imports from Markdown, a folder, or a ZIP package. Import the whole folder or ZIP when the Skill needs accompanying references or assets; a Markdown import contains only that file.

Edit Step 4's visual direction instructions, add/edit/remove image styles, image templates and motion styles, or manage Step 6 cover styles. New entries immediately appear in the production-setting options. **Use in this project** selects the current entry; when using the editor inside Settings, save the production settings afterward. The template groups include agent, segmentation, opening, image refinement and cover prompt files. Required template variables are shown and checked before saving. Advanced **Edit source file** allows YAML editing and repairing externally malformed source files.

The library is ordered by production step, with visible numbers on categories and headings. Writing templates appear in Step 1, segmentation templates in Step 2, content summary and visual templates in Step 4, assembly skills and templates in Step 5, and cover styles/templates in Step 6. Step 3 has no editable library items. This arrangement preserves existing files, saved selections and revision history. Verified on 2026-10-13 with 185 Python tests, 19 JavaScript checks, and browser checks for all 13 categories and seven new instruction templates. Coverage includes saving/undo, new and revised sessions consuming saved instructions, English/Chinese labels and a 1000 × 720 window.

Every instruction supplied to Studio's Claude Code sessions is editable in **Settings → Skills & templates**, under the numbered template group for its step:

| Step | Editable instructions |
|---|---|
| 1 · Writing | Writing agent template, direct-writing guidance, output/file-location contracts and Studio session guidance |
| 2 · Segments | Existing segmentation template |
| 4 · Visuals | Motion agent template, per-segment/new/single/multiple-task guidance, finish/render instructions, motion-reference and style guidance |
| 5 · Assembly | One Claude Code assembly template: the materials list, your choices (style direction, size, narration speed, subtitles, music, opening) as variables Studio fills in, where to deliver, and the GPU rendering guide. Style directions stay in the Assembly skills. |
| 6 · Covers | Claude Code cover template for new/revised HTML designs; separate Cover image template for Image API generation |

Saved instructions apply when a new or revised session starts, without restarting the service. Sessions already open retain their context. Skills and their text references remain editable in the skill groups. Step 3 uses the voice provider API and has no Claude Code instructions.

Keep the required variables shown by the editor. In formatted templates, double literal braces as `{{` and `}}`. Some Studio templates use `<!-- section: name -->` headings to select direct/document writing, single/multiple visual tasks or new/revised covers. Edit the text inside these sections; required section names and variables are validated when saving. Templates retain conflict detection, revision history and undo.

These files are shared across projects. Saved edits apply to new jobs and newly activated agent sessions; existing completed media remain available until you choose to generate. Editing a named YAML entry preserves other entries and their order, but rewrites YAML formatting/comments. Use the source editor to keep intentional source formatting. The **Open source folder** button uses Finder on Mac or Explorer on Windows; after external edits, click **Refresh library**. External changes are checked before saving so they cannot silently overwrite an editor draft.

Library saves, imports and removals retain recoverable versions in the library's `.studio-library/` folder. **Undo last change** and **Library history** restore them. Restore newer edits first. Deleted styles are not silently replaced in a project's settings: select another available style before generating. Each style/template group must retain at least one item.

The Mac app is not yet signed or notarized; see **Before public distribution** below.

## Workflow

- Open an existing production, or create a named project without choosing a source. Configure the writing mode, source, writing Skill, and agent provider/model at the **top of Step 1**. Other production settings remain in **Settings → Production**.
- **Writing modes:** **Write from a document / 基于文稿／资料写作** uses a manuscript, book, or source folder. **Write directly with Claude Code / Claude Code 直接写作** requires no source; give the topic and requirements in the terminal. Both use the selected writing Skill and produce the same editable `raw.json` contract. Direct mode ignores any previously selected source. Save the setup explicitly, or activate Claude Code to save it automatically. The choices persist per project. Setup changes preserve completed outputs and review markers; actually rewriting the manuscript marks affected later steps for rebuilding.
- **Writing:** activate Claude Code in the right terminal. The app prepares the selected source (document mode), Skill, output contract, and configured provider. Type your instructions in the terminal. Review and edit the manuscript in the center. Video titles, cover titles, cover subtitles, and opening quotes each have separate editable candidate boxes. Select one per group, then **Save manuscript** before continuing or generating. Add/remove candidates as needed; a newline within a box stays part of that candidate.
- **Opening quote / 开场金句:** the candidate editor sits before the manuscript, matching playback order. **Use opening quote / 使用开场金句** saves immediately and controls opening narration, visuals, sequence timing, and assembly. Quote edits/selections use **Save manuscript**. Turning the opening off retains candidates and existing media and lets the final video start with the body; generate Assembly to update completed exports. The switch marks assembly for rebuilding without invalidating unrelated segment recordings or visuals. Opening-only stale markers are suspended while excluded and restored if re-enabled.
- **Steps:** Writing → Segments → Voice → Visuals → Assembly → Covers, numbered 1–6. Segments is the engine's step 1.5 and the visual direction is its step 2; the engine and its settings keep those names.
- **Segments:** edit each narration directly in the list. Each row has an **Image / Motion** toggle: Image is generated by the image API in Visuals; Motion is built by Claude Code as an HTML clip in Visuals. **Save changes** in the header saves every edited row together; a visual-type-only change marks just Visuals for rebuilding. Generate through the existing pipeline.
- **Visual direction / 画面基调:** a collapsible card at the top of Visuals holds the short story summary every image and motion clip follows. Visuals jobs generate it first when it is missing or outdated (the manuscript changed, or its provider/model changed in **Settings → Production → Visuals**). Edit and **Save direction**, or generate it alone; either marks the visuals for rebuilding but not the recordings. Motion with Claude Code needs a current direction. **Edit instructions** opens its prompt.
- **Voice / Visuals:** select a segment or opening in the sequence. Play recordings, inspect images/video, and edit narration. Generate one item, missing items, or the whole step.
- **Visuals with Claude Code:** activation selects motion for the requested segments and prepares the motion skills, narration, actual recording duration, style, dimensions, and saved composition paths. Give the agent instructions interactively. After it ends, use **Render saved motion compositions**. Static images use the existing image provider.
- **Opening background:** in Visuals the opening is only its background picture. Its versions are backgrounds; choosing one uses it at once, **New background** generates another, and every version is kept. Assembly draws the opening quote and source name over the background in use.
- **Batch images:** Step 4 has direct **Generate all images / 生成全部图片** and **Fill missing images / 补全缺失图片** actions. All generates every static-image segment; missing preserves existing PNG/JPEG/WebP files. Both include the opening when enabled (missing only when absent), skip motion segments, and require current segments. Prior outputs are preserved in project history.
- **Assembly:** the 16:9 video is always made; the **3:4 copy** card above the preview has its own switch. Selecting a segment seeks the video. Assembly renders the opening clip (the quote over the background chosen in Visuals, at the top of the video) and reuses it until the quote, source name, background or opening timing changes; the first render takes a few minutes. In **Standard assembly** the panel on the right saves as you change it:
  - **Video:** export size, narration speed and burned-in subtitles. The codec stays in Settings → Production.
  - **Background music:** a bundled track or one added with **Add music…** (copied into the app data folder's `music/`, so every project can use it), a preview button, volume, lowering the music under narration, and loudness levelling.
  - **Pictures & transitions:** slow zoom on stills, and transitions.
  - **3:4 panels:** style (ink, paper, or the video itself blurred), the video's position between the panels, the top panel (title and subtitle, title only, or empty; either text can be overridden), the bottom panel (ornament, the opening quote, custom text, or empty), and the accent colour. The 3:4 view shows a **Layout** preview drawn by the engine from a frame of the video (or the first segment's picture before anything is assembled).
  - Music and video changes mark Assembly for rebuilding. Panel changes only mark the 3:4 copy (**Panels changed**): **Build 3:4 copy** redraws it around the current 16:9 video without assembling again. With the 3:4 copy switched off, the next assembly removes the old copy (Revision history keeps it).
- **Agent editing in Assembly:** optional Claude Code mode in which the agent is the editor. It receives only the materials (`materials.json`: each segment's visual, narration text and recording, the opening's quote, source name, background and recording, and the music it may use), the **Editing brief** choices (export size, narration speed, subtitles on or off, suggested music, **Let Claude Code choose and mix tracks**, and a style direction from the Assembly skills; **Free design** sets none and lets the agent choose a style that fits the content) and one GPU rendering tool, `studio_render.py` (capability check, HTML page → video on the GPU with the hardware encoder, snapshots). It designs structure, pacing, motion, captions, transitions, the opening and sound, and delivers `final_video.mp4` (optional `.srt`) itself; the previous video goes to revision history first, and ending without a delivery leaves Assembly marked for rebuilding. **New video edit** starts it at once; **Revise last edit** continues in a copy of the last session's folder. The session starts in the project folder, so Claude Code asks once per project whether to trust it. The 3:4 panels under the brief still shape the 3:4 copy Studio draws around the delivered video.
- **Covers:** choose **Image API** or **Claude Code + HTML / Three.js** at the top of the Covers page, alongside the selected cover copy, one or more sizes, and the candidate count per size. The style selector expands to show full descriptions. Choices save automatically. API generation makes every selected size and can run beside recordings, visuals or assembly; Settings → Production → Covers holds its provider and model. HTML mode uses Writing’s Claude Code login, provider and model, with the terminal on the right. **New cover design** prepares independent HTML compositions for each size; review snapshots, then **Finish & render covers** to add PNG candidates. **Revise HTML design** copies the latest source for editing, and **Render saved HTML** retries rendering without an agent call. Compare candidates from either method and select a cover; existing candidates and the selection persist. Each candidate has a trash icon with confirmation. Deleting the chosen cover selects a remaining candidate; deleting the last returns to the empty preview. Revision history keeps deleted covers for recovery.
- **Steps are free to visit:** choose any step in the sidebar; there is no Continue or review approval button. **Show in Finder or Explorer** beside the preview selects the shown file (recording, image, clip, video or cover) in its folder.

**Generate** preserves a revision and optionally queues later steps for rebuilding, pausing for your review and interactive agents. The rebuild guide lasts for the current app session; generated outputs, review markers, settings, and revision history persist.

Text edits and generation create history under each production's `.studio/history`. The manuscript and script JSON files are the only sources production reads. Unsaved text is preserved on refresh, and conflicting saves are rejected. Production and a stage agent cannot concurrently write the same project.

Saved metadata selections appear first in each candidate group; the other candidates remain available. Generating Segments carries those choices into the script, opening narration/visuals, cover generation, and 3:4 export text. Changing a choice marks dependent outputs for generation; it does not silently replace completed media.

## Architecture

- `src/`: React/TypeScript editing workspace; xterm.js terminal display.
- `src-tauri/`: native Rust host, OS credential storage, portable-pty terminal, the bundled runtime's environment, and local service lifetime. Every child process gets the bundled programs first on `PATH`, the library location, and the browser and FFmpeg paths; the user's Python settings are cleared.
- `backend/server.py`: authenticated loopback project/artifact service, revisions, invalidation, job supervision, and range-based media streaming.
- `backend/runner.py`: isolated process invoking the engine's real stage functions.
- `backend/library.py`: the editable skills and templates, and their seeding from the bundled engine.
- `scripts/stage.mjs`, `runtime/`: assembles the bundled runtime from pinned versions and lock files.
- `backend/context.py`: interactive agent context and output contracts.
- `backend/agent_env.py`: resolves the existing stage's provider credentials directly into the native child process; saved credentials never return to the browser/UI or enter launch manifests.

Generation jobs are cancellable. On normal app exit, the host stops terminals and child production jobs. Interrupted agent leases recover on startup. HTML compositions are rendered outside the application WebView; the app previews their media output.

## Verify and package

```sh
npm run stage
npm test
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
npm run tauri -- build --bundles app
```

To make the installer, run `npm run desktop:build` on a Mac with Apple silicon. It stages the runtime, builds the app and writes the `.app` and the `.dmg` under `src-tauri/target/release/bundle/`. The disk image is large (about a gigabyte) because it carries Python, Node.js, Chrome, FFmpeg and Claude Code, so nothing needs to be installed on the other computer.

**Writing files.** The Step 1 agent works in the project folder. Its prompt tells it to keep every intermediate file in the project's `text` folder (even where a skill still says `output/<project>/text`) and to save the manuscript only as `text/raw.json`. When the session ends, the service checks `raw.json`: text fields must be non-empty, the title, cover and quote candidates must be lists of separate strings, and `total_length` is corrected to the real character count. A manuscript left in a legacy `output/` folder is moved to `text/raw.json`; otherwise the project shows a warning. The agent's shell finds the bundled `python` (with PyMuPDF, pdfplumber, ebooklib, mobi and python-docx), so it does not depend on anything installed on the computer.

Tests run on the staged Python and engine, so they exercise what ships. `npm run dev` starts the browser development interface with its own data in `build/dev-data`. The interactive terminal and native export dialog require the Tauri app.

The opt-in native credential-store test uses a disposable random account and removes it afterward: `cargo test --manifest-path src-tauri/Cargo.toml native_store_roundtrip -- --ignored`.

## Before public distribution

- **Platforms:** `npm run stage` packages macOS on Apple silicon. Intel Macs and Windows need their entries in `PLATFORMS` (Python, Node.js and FFmpeg builds); the host code is platform-neutral but untested there.
- **Signing:** sign every bundled executable and library (Python extensions, Node.js, Chrome, FFmpeg, Claude Code) with a Developer ID and hardened runtime, then notarize. Node.js and Chrome need the JIT entitlement.
- **Licenses:** `runtime/THIRD_PARTY_NOTICES.md` lists the bundled components. FFmpeg (GPL-3.0) and PyMuPDF (AGPL-3.0) require offering their source.

HEVC playback varies by system; H.264 is the compatibility choice in project settings.

Step 5 supports standard assembly and optional Claude Code editing, where the agent designs and delivers the whole video from the materials with GPU-accelerated rendering. Narration segments keep their order.

See [GUI_DESIGN_REVIEW.md](../GUI_DESIGN_REVIEW.md) for the research and confirmed product decisions, and [IMPLEMENTATION_REVIEW.md](../IMPLEMENTATION_REVIEW.md) for validation and remaining release work.

A two-segment `_Studio_Verification` project was created for GUI testing. It contains fictional text, simple images, silent recordings, and short offline exports; it can be used to explore the interface without editing the existing production.
