# Video production GUI: project review and design proposal

Date: 10 October 2026

Status: design research updated with your confirmed requirements. A first implementation now exists in this directory; see IMPLEMENTATION_REVIEW.md for implemented behavior and validation. The original CLI source has been preserved.

**Confirmed platform requirement: Tauri, with macOS as the development and first-validation priority and Windows-compatible source.** Keep platform differences behind explicit adapters across the production engine, terminal, media previews, and dependency discovery. Windows runtime and package support must be validated on Windows before being declared supported; simultaneous delivery of both installers is not a confirmed first-release requirement.

## 1. What I understand you want

You currently use an IDE to inspect files and a CLI to run the video pipeline. The new application should bring those activities into one workspace: review the output of each production step, make corrections, regenerate the relevant material, and continue producing the video. The central content is the production artifact: a script, segment, recording, image, animation, assembled video, or cover.

You confirmed that the agent is **Claude Code CLI**, running the **main production agents interactively in Steps 1 and 4** in the right-hand terminal. The terminal is how you activate and interact with those agents. Preserve the existing steps, skills, and production contracts while adapting their execution to this interface. A terminal is a first-class requirement, including typing, interactive prompts, scrolling, resizing, and interrupting commands. In the future, **Step 5 should gain an additional editing workflow operated by an agent and skills**.

You confirmed that the normal workflow should let you **review, edit, and regenerate individual outputs, then continue when you choose**. The application should preserve that control without imposing a separate approval ritual after every stage.

The application will be a Tauri desktop production workspace with three areas: navigation on the left, artifact review in the center, and an agent terminal on the right. The existing Python engine should remain the production engine.

The GUI becomes the main text editor. The app owns project discovery, files, settings, progress, job supervision, and regeneration. Claude Code is responsible for the particular step you activate, with its instructions and skills. You prefer an **editing-style interface**, with a media preview, segment/timeline navigation, and stage-specific editing tools.

## 2. What the repository already provides

The [architecture document](engine/core/ARCHITECTURE.md) explicitly identifies `core` as shared runtime logic for the CLI and a future UI. The separation into pipeline, domain, and infrastructure makes this a reasonable foundation. Individual [step functions](engine/core/pipeline/steps.py:402) return structured results, while the interactive menus live in the CLI adapter.

| Production stage | Existing outputs and behavior | Proposed review experience |
| --- | --- | --- |
| Source and setup | Input documents or folders; writing skill and additional requirements | Source list, document preview where available, writing instructions, project settings |
| 1 — Writing | `text/raw.json`, editable `raw.docx`; title, cover-text, and opening-quote candidates; agent extraction and coverage paths | Run the main writing agent in the right-side terminal; read and edit narration in the GUI; choose candidate titles and quote; inspect available evidence |
| 1.5 — Segmentation | `script.json`, reading-copy `script.docx`, `字幕.txt`; segment text, index, estimated duration, `image`/`hf` visualizer | Segment list and GUI narration editor; choose image or animation; compare actual count with requested count; rerun segmentation when needed |
| 2 — Context summary | `mini_summary.json`, a short shared summary derived preferentially from the raw manuscript | Review and edit the shared content context used for visuals |
| 3 — Voice | Per-segment WAV/MP3, optional opening narration, exported SRT | Play individual recordings; show actual duration; compare narration text; regenerate selected recordings |
| 4 — Visuals | Static images, HyperFrames segment MP4s, opening material; animation HTML and logs in per-segment work folders | Run the main visual agent interactively in the right-side terminal; review visual with narration in the center; render edited animation or regenerate completed work |
| 5 — Assembly | `final_video.mp4` and a derived `final_video_3x4.mp4`; subtitles, music, transitions, and narration processing | Review both deliverables, seek to segment boundaries, inspect final subtitle placement and audio mix; later add an alternative agent-and-skills editing workflow |
| 6 — Covers | Cover image candidates, using title and subtitle metadata | Compare candidates, inspect at full resolution, select the cover for export |

Several useful capabilities already exist:

- Steps 3 and 4 accept selected segment indices and a separate opening-regeneration flag. “Regenerate these three segments” can use existing functions.
- Visual production supports static images, HyperFrames animations, and a mixed mode that follows each segment’s visualizer.
- HyperFrames reads the actual recording duration before generating and rendering an animation.
- Project discovery and legacy progress detection exist in the [scanner](engine/core/pipeline/scanner.py:144).
- Automated agents already run in writing, optional segmentation, and HyperFrames generation. Their JSONL logs can supply a separate activity view.

I examined an existing project with **151 script segments, 151 segment images, and 151 WAV recordings**, plus an SRT file. I also inspected one generated image. That scale calls for searchable, filterable segment navigation, numeric ordering, lazy-loaded thumbnails, and keyboard next/previous controls. An endlessly expanded form would be difficult to use.

At the start of this research, `AIGC_Video_Desktop` contained no application. The new Tauri implementation is described separately in IMPLEMENTATION_REVIEW.md.

## 3. Proposed workspace

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Project · selected stage · run status · Save · Regenerate · Continue     │
├──────────────┬────────────────────────────────┬─────────────────────────┤
│ Projects     │ Media preview / text editor    │ Claude Code terminal    │
│              │                                │                         │
│ Source       │ Image / video / audio / text   │ Selected step & scope   │
│ Writing      │                                │ Activate / resume       │
│ Segments     │ Narration and local settings   │                         │
│ Context      │                                │ Interactive terminal    │
│ Voice        │ Review / regenerate / replace  │                         │
│ Visuals      │                                │                         │
│ Assembly     ├────────────────────────────────┤                         │
│ Covers       │ Segment timeline / audio track │                         │
└──────────────┴────────────────────────────────┴─────────────────────────┘
```

This is an editing-style layout proposal, not a finalized visual design. The center should prioritize a large preview, a narration/settings inspector, and a bottom timeline showing segment visuals and recordings. Use actual audio durations once recordings exist and label earlier timings as estimates. During writing, the GUI text editor takes the main center area. Both dividers should resize; the terminal should collapse or expand when you need more room for video or writing. At smaller window sizes, the stage list can collapse.

The initial timeline supports selecting segments, playback, seeking, and locating material that needs regeneration. An editing-style appearance does not by itself commit the first release to arbitrary trimming, multitrack edits, split/merge/reorder, or manual subtitle timing. Your confirmed functional requirement is to regenerate completed work; detailed timeline editing can be added when required, particularly for the future Step 5 agent-editing option.

Selecting a stage changes the center’s artifact viewer. Selecting a segment preserves that selection across Voice and Visuals, so you can hear segment 23 and then inspect its image without finding it again. The opening is a distinct item rather than segment 1.

The center should offer two modes where useful: **Overview**, for comparing a set of outputs, and **Detail**, for inspecting the selected item. A segment detail includes narration, audio, visual, relevant settings, and review notes. The final assembly player should map playback time back to segments. Agent launch controls should be prominent in Writing and Visuals. Other stages do not need equivalent agent controls in the first release; preserve a live terminal session if you navigate away.

Separate actions by their actual consequence: Save text, Regenerate audio, Generate visual, Render edited animation, Replace asset, and Rebuild final video. A generic “Run” button alone would hide too much.

Use the GUI manuscript and segment data as the editing authority. Word documents may remain optional exports, but a stale `raw.docx` must not override GUI or agent edits. Keep the main editor synchronized with validated file changes from Claude Code, and resolve conflicts when the GUI has unsaved text.

Keep ordinary production settings near their stage: writing skill with the manuscript; voice and speaking rate with audio; image/animation choice with visuals; music, subtitles, and dimensions with assembly. Put provider and detailed encoding controls behind an expandable settings area.

## 4. Review and revision behavior

Your confirmed workflow is review-driven: generate a stage, inspect its outputs, correct selected items, then continue when ready. Existing full automation can remain an optional mode. Review markers are useful for keeping track of work; a compulsory approval gate after every stage is not part of the confirmed requirement.

**Completed work remains regenerable, with both options confirmed.** A completed stage stays accessible with its inputs, settings, outputs, and regeneration action. Provide **Regenerate this step**, with an option to rebuild affected later stages, and **Restart production from source** for the whole workflow. Show the affected scope before execution and preserve previous output revisions. Rerunning a step marks dependent outputs stale; rebuilding them happens within the scope you choose.

Track **generation status** separately from **review status**. A generated file can be unreviewed, approved, or needing changes. It can also be stale because one of its inputs changed. Running and failed states belong to jobs; “file exists” should not imply a job is still running.

For example, changing segment 23’s narration should flag its recording, subtitles, relevant visual, and final exports as needing attention. A static image may be kept after review if it still fits; an animation tied to the old recording duration needs timing validation. Changing music should affect assembly rather than regenerate all recordings and images.

Important dependency details:

- Static segment images do not inherently need audio, but animated segments do. The GUI should explain missing audio for the selected animated segment.
- A change to the shared context summary may affect many visuals. Show the affected set and let you choose what to regenerate.
- Rerunning segmentation changes the relationship between text and indexed asset filenames. Preserve identities in app metadata where possible, and explicitly remap or invalidate affected assets. Apply the same rule if split/merge/reorder is added later.
- Opening narration and opening video depend on the selected quote and relevant settings; treat them independently from the main segment list.
- Covers can be produced once suitable text metadata exists; they do not need to wait for the final video.
- Selecting a different title, cover subtitle, or quote must update the metadata consumed by the pipeline. Existing helpers commonly select the first candidate in each list.

Preserve previous outputs when regenerating. Let you compare old and new and choose which revision is active. Review decisions should attach to a specific artifact revision, so replacing a file does not retain an approval that referred to earlier content.

## 5. Right-side terminal and agent intervention

The first version should support a real shell session with stage-specific agent launch actions in Steps 1 and 4. An embedded terminal needs both a terminal renderer and a local pseudo-terminal process; displaying captured logs alone cannot provide the interaction you described. Proposed Tauri integration: xterm.js in the interface, connected through a narrow Rust bridge to a PTY implementation. The Rust [portable-pty crate](https://docs.rs/portable-pty/latest/portable_pty/) provides a cross-platform PTY API and is a candidate for this backend. Its compatibility with the chosen agent must be tested on both operating systems.

Use the user's configured shell, with macOS and Windows defaults resolved separately. Test Windows ConPTY behavior, PowerShell execution, executable discovery, Unicode paths, terminal resize, interactive prompts, interrupt keys, and child-process cleanup. Keep normal production-worker output separate from terminal input/output.

For Step 1, the agent needs the source document, selected writing skill, writing instructions, manuscript, and title/quote metadata. For Step 4, it needs the selected segment, narration, actual audio duration, shared summary, visual settings, relevant skills, and image or animation paths. The launch experience should reflect that difference.

Claude Code's documented CLI supports starting interactive sessions and resuming conversations by session ID or name. Associate sessions with the project and particular production step, because “continue the most recent session” may select unrelated work in a shared directory. This association is app-managed metadata. See the [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference).

Recommended interaction:

1. Select a stage and, where relevant, a segment.
2. Activate or resume the main Claude Code session for that step in the right-side terminal. Keep its live process associated with that step when you navigate elsewhere.
3. Automatically prepare the relevant source, skills, settings, selected segments, output paths, and existing artifact revisions when you activate the step. Show the prepared scope in a compact header. Use saved, validated editor data so the agent sees the same content as the GUI.
4. You give your instructions in the terminal. The app supplies production context; your conversation directs the task and subsequent changes. Selecting a different stage or segment does not silently replace the context of an already active session.
5. Detect changes to production files, validate them, refresh the artifact viewer, and display affected downstream outputs.
6. The app validates the step's required outputs and offers the appropriate preview, render, or continuation action. A shell exit alone does not establish successful completion.

The GUI’s selection is context for an agent; it is not a filesystem permission boundary. Explicitly show the session's step, working directory, and selected scope. Choose a working directory appropriate to that step, such as the configured writing workspace or animation work folder, while the app maintains the project metadata.

Do not silently change the working directory of a live agent when you click another segment. Prepare new context or start an explicitly labeled session instead.

**Confirmed execution model: the terminal runs the main Step 1/4 production agents interactively.** It is not a second agent session added after background generation. The current SDK wrapper runs one-shot queries, so introduce an execution adapter that launches Claude Code CLI with the relevant existing step instructions, skills, environment, and output contract. Preserve the Python preprocessing, validation, rendering, and other production logic where applicable. Do not also start the same step through the existing SDK path while its interactive session is writing those artifacts.

The app supervises the interactive process and recognizes valid production outputs; Claude Code handles that step's task. Navigation, scheduling later steps, progress state, artifact selection, and regeneration remain app responsibilities. Optional segmentation agents and non-agent generation functions do not gain a terminal requirement from this decision. Step 4's existing static-image and animation modes remain available; implementing the interactive path must preserve their distinct production behavior.

One critical animation nuance: the current [HyperFrames generation function](engine/core/infra/ai/claude_agent.py:832) removes the previous `index.html` before generating a fresh composition. After you manually revise animation HTML in the terminal, the GUI needs a **Render existing composition** action that skips regeneration. Otherwise the next generation call can discard your edits.

### Future Step 5: agent-and-skills editing

Keep today's automatic assembly as one Step 5 option and add agent-driven editing as another. The agent should receive the chosen source recordings and visuals, an editable composition or edit plan, relevant editing skills, and your direction. Its output should include saved editing instructions/source plus a rendered video you can inspect and revise.

Do not use the flattened final MP4 as the only editable project representation. Preserve segment identities, asset references, timing, subtitle choices, music, and output settings so edits can be reproduced and rerendered. Both assembly options should feed the same export-review experience and revision history. Which editing engine and skills implement this future option remains open.

## 6. Engineering gaps that affect the design

| Finding in the current implementation | Consequence for the GUI | Recommended change |
| --- | --- | --- |
| Progress is inferred mainly from files and counts; final-video presence can force earlier stages to appear complete | Old outputs may appear current after edits | Store revision-aware artifact and job metadata; use scanner results as legacy discovery |
| Step 1.5 prefers `raw.docx` when present and rewrites `raw.json`; Step 2 also prefers the DOCX manuscript | JSON-only edits can be overridden, and the summary can diverge from edited segments | Make GUI-saved data authoritative on the desktop execution path; treat DOCX as optional export or explicit import |
| `script.docx` is exported as a reading copy; the inspected downstream path reads `script.json` | The README’s broad editing guidance should not become a promise of script-DOCX round trips | Make the GUI segment editor authoritative; keep DOCX round trips outside the initial required workflow |
| SRT export and burned-in subtitle generation are separate paths; assembly builds captions from the script and audio durations | Editing an exported SRT does not currently change burned-in subtitles | Start with honest preview-only SRT behavior, or add a shared editable caption model |
| Exported SRT starts at zero over the main recordings; assembly has opening timing and narration-speed processing | Raw SRT playback should not be assumed to match the final video | Derive final caption review from the same timing model used for assembly |
| Asset resolution prefers `segment_N.png` over segment video when both exist | Switching to animation can still select the older image | Record the active artifact explicitly and resolve by intended visualizer |
| Existing downstream cleanup deletes files broadly and does not account for every derived deliverable | A GUI revision could destroy useful history or leave an old 3:4 export looking current | Preserve revisions and invalidate dependencies; track both export variants and covers |
| Configuration is a mutable global object, with class/instance overrides | Simultaneous jobs with different settings could affect each other | Snapshot settings for each job; initially serialize production jobs and isolate worker processes |
| Generation is blocking and uses provider calls, thread pools, and render subprocesses | Closing a viewer is not equivalent to cancelling work | Add background job supervision, structured progress, and accurate cancel states |
| Per-segment image prompts are assembled inside generation; no durable prompt-editing contract is exposed | A prompt editor cannot reliably promise reproduction yet | Persist effective prompts, settings, and outputs if prompt editing is included |

Cancellation deserves precise behavior: stop queued work, interrupt owned render processes where possible, and report provider work that cannot be stopped. A “stop after current item” option is useful. Do not imply that cancelling the local task reverses a submitted generation request.

External file changes also need conflict handling. Watch files, wait for writes to settle, validate JSON/media, and avoid overwriting an unsaved editor buffer. Provide a refresh action as a fallback. An agent and an app job should not simultaneously write the same artifact.

## 7. Implementation direction and first release

**Required framework: Tauri desktop application, developed and validated on macOS first, with Windows-compatible source.** Proposed architecture: a shared editing-style interface, a Rust application layer for files/jobs/terminal lifecycle, an embedded xterm.js terminal backed by a Rust PTY implementation, and a supervised Python worker using the existing core. The main Step 1/4 agents execute through Claude Code CLI in the terminal. The frontend library remains an implementation choice.

```text
Tauri desktop application — Mac first, Windows-compatible source
  Shared interface: GUI text editor, media preview, timeline, xterm.js
    Rust application layer: files, job events, process supervision
      Python production worker → existing core → AI/media providers
      Step 1/4 PTY sessions → platform shell → main Claude Code agents
      Media runtimes → FFmpeg / HyperFrames and its browser runtime
```

Tauri supports packaging external executables as sidecars, including Python applications, with binaries supplied per target architecture. Use that mechanism for the packaged production worker, while development can use an explicitly configured Python environment. Packaging the actual Python dependencies still needs verification. See [Tauri's sidecar documentation](https://v2.tauri.app/develop/sidecar/).

Use a small application service to call pipeline functions and launch the interactive Step 1/4 execution adapters; avoid automating the menus in `start.py`. Keep the current CLI usable. Add app metadata alongside existing projects so legacy outputs can be imported without a large migration.

The service should expose project discovery, artifact loading/saving, selected-step jobs, selected-segment jobs, render-existing-animation, file-change reconciliation, and job events. A job should capture its input revisions, settings, selected segments, outputs, and errors.

Generated animation HTML belongs in an isolated preview surface without access to the application's filesystem or terminal commands. Keep privileged commands scoped to the trusted application interface, and validate command arguments in Rust. Tauri's [capability documentation](https://v2.tauri.app/security/capabilities/) explains how access is associated with windows/webviews; the preview design must explicitly account for generated content.

### Cross-platform requirements

- **Media preview:** Tauri uses WebView2 on Windows and WKWebView on macOS, so test actual playback, seeking, subtitles, local media access, and animation previews in both runtimes. Keep a playback-compatible preview copy when a delivery codec cannot play in the embedded viewer. See [Tauri's webview documentation](https://v2.tauri.app/reference/webview-versions/).
- **Rendering environment:** The current HyperFrames [runtime helper](engine/core/infra/hyperframes/runtime.py:22) prepends macOS/Homebrew and fixed Node version paths. Replace this assumption with platform-aware discovery or managed runtime paths. Verify Windows invocation of Node/npm executables as well as FFmpeg and the render browser.
- **Encoding:** The current [exporter](engine/core/infra/media/exporter.py:21) first tries VideoToolbox encoding and falls back to libx264. Select encoding from detected platform/capabilities; make the effective codec visible, including a fallback from a requested HEVC export to H.264.
- **Paths and fonts:** Use platform path APIs and structured process arguments. Test spaces, Chinese text, quotes, long paths, Windows drive letters, temporary folders, and available Chinese fonts. Store mutable project data outside packaged application resources.
- **Runtime dependencies:** Define how Python, FFmpeg, Node/HyperFrames, its render browser, and the agent executable are installed or bundled. Tauri does not eliminate those dependencies. Agent authentication should use the configured local agent environment.
- **Development and distribution:** Prioritize a working macOS build and validate it on the development Mac. Keep Windows-specific code and build configuration available behind platform adapters. Before delivering a Windows installer, build and test it on Windows, including a clean installation without an IDE or developer shell. Minimum OS versions and processor architectures can be selected during packaging.

Build the first release in this order:

1. On macOS, open existing projects in an editing-style workspace; view text and media; connect the interactive Claude Code terminal to the main Step 1 writing task, then the Step 4 agent task. Keep Windows-compatible execution interfaces throughout.
2. Add the main GUI manuscript/segment editor, completed-step regeneration, opening controls, file refresh, and render-existing-animation.
3. Add revision history, review status, downstream invalidation, and reliable job interruption.
4. Add export review for both aspect ratios and cover selection; test playback of the actual configured codecs in the chosen GUI runtime.

Core acceptance scenarios:

- Open the 151-segment project and navigate numerically without preloading all full-resolution media.
- Regenerate one recording while preserving the other recordings; identify downstream timing changes.
- Switch one visual from image to animation and ensure assembly uses the selected artifact.
- Edit HyperFrames HTML through the agent terminal, render it, and inspect the result without overwriting the edit.
- Save a manuscript edit, run segmentation, and verify an older DOCX cannot silently undo it.
- Reopen a project and distinguish generated, reviewed, missing, and stale material.
- Keep terminal input and the GUI responsive during production.
- Activate the main Step 1/4 agents in the terminal with the intended skills and output contracts, without launching a duplicate background agent for the same task.
- Reopen a completed stage, regenerate its output, and retain the previous revision for review.
- Restart the whole production from the source as a separate action, retaining earlier output revisions and applying the chosen settings and interactive Step 1/4 workflow.
- Validate the first working build on macOS. Later, run the same source-to-export and agent-intervention scenarios on Windows before claiming runtime support, including fresh installation and paths containing spaces and Chinese characters.

## 8. Confirmed decisions and remaining details

| Decision | Confirmed requirement |
| --- | --- |
| Framework and platform | Tauri; Mac development is the priority; source remains Windows-compatible |
| Agent identity | Claude Code CLI |
| Agent execution | Main Step 1/4 production agents run interactively in the right-side terminal |
| Agent context | The app prepares source/skills/settings/selected-segment context automatically; you give instructions in the terminal |
| Agent responsibility | Particular production step; the app owns project management |
| Review workflow | Review/edit/regenerate outputs and continue when you choose |
| Regeneration | Rerun a completed step with optional rebuilding of affected later steps, and restart the whole production from source |
| Text editor | The GUI is the main manuscript and segment editor |
| Interface style | Editing-style workspace with media preview and timeline/segment navigation |
| Interface language | Switch between English and Chinese in Settings; remember the preference and preserve current work while switching |
| API keys | Enter and save provider API keys in Settings, using secure storage on the computer |
| Future Step 5 | An additional agent-and-skills editing option alongside existing assembly |

The initial product questions are resolved. Minimum OS/architecture baselines can be selected during packaging. Detailed timeline manipulation, the frontend library, and the future Step 5 editing engine remain implementation or later-scope decisions rather than first-release commitments.

## Research limits

This review is based on repository documentation, pipeline and CLI code, configuration contracts, agent integration, subtitle/composition code, and an existing project’s artifacts. It includes official documentation for the proposed terminal integration. I did not run paid generation, launch an agent, render a video, listen to the recordings, or validate the entire project’s visual quality. Runtime behavior and packaging compatibility still need testing during implementation.
