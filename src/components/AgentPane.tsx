import { useI18n } from "../i18n";
import { useEffect, useImperativeHandle, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  TerminalSquare,
  Play,
  Square,
  ArrowUpRight,
  PanelRightClose,
} from "lucide-react";
import "@xterm/xterm/css/xterm.css";
import type { ClaudeCodeMode, Launch, Stage } from "../types";

/** Lets the workspace end the session (for example "Finish session") the same way the Stop button does. */
export type AgentHandle = { stop: () => Promise<void> };

export default function AgentPane({
  handle,
  target,
  step,
  launch,
  onActivate,
  onFinished,
  onClose,
  onError,
  directWriting,
  noWritingSkill = false,
  claudeMode,
  disabled,
}: {
  handle?: React.Ref<AgentHandle>;
  /** What an activation here works on, such as "Segment 3". */
  target?: string;
  step: Stage;
  launch: Launch | null;
  onActivate: () => void;
  onFinished: () => void;
  onClose: () => void;
  onError: (message: string) => void;
  directWriting: boolean;
  noWritingSkill?: boolean;
  claudeMode: ClaudeCodeMode;
  disabled: boolean;
}) {
  const { t } = useI18n();
  const host = useRef<HTMLDivElement>(null),
    terminal = useRef<Terminal | null>(null),
    fit = useRef<FitAddon | null>(null),
    session = useRef<Launch | null>(null);
  const [running, setRunning] = useState(false);
  useEffect(() => {
    const t = new Terminal({
      fontFamily: '"SFMono-Regular", Consolas, monospace',
      fontSize: 12,
      lineHeight: 1.35,
      cursorBlink: true,
      scrollback: 6000,
      theme: {
        // Matches the pane's surface (--bg-0) and accent in styles.css.
        background: "#111315",
        foreground: "#d8dcdf",
        cursor: "#eba27e",
        cursorAccent: "#111315",
        selectionBackground: "#3a4047",
        black: "#111315",
        red: "#e07b75",
        green: "#87b79e",
        yellow: "#e8b884",
        blue: "#9caac7",
        magenta: "#b69cb7",
        cyan: "#97c6c8",
        white: "#e4e7eb",
      },
    });
    const f = new FitAddon();
    t.loadAddon(f);
    t.open(host.current!);
    terminal.current = t;
    fit.current = f;
    const resize = new ResizeObserver(() => {
      f.fit();
      if (session.current)
        invoke("terminal_resize", {
          id: session.current.id,
          cols: t.cols,
          rows: t.rows,
        }).catch(() => {});
    });
    resize.observe(host.current!);
    t.onData((data) => {
      if (session.current)
        invoke("terminal_write", { id: session.current.id, data }).catch((e) =>
          onError(String(e)),
        );
    });
    let unlisten: (() => void) | undefined,
      disposed = false;
    if (isTauri())
      listen<{ id: string; data: string; exited: boolean }>(
        "terminal-output",
        ({ payload }) => {
          if (payload.id !== session.current?.id) return;
          t.write(payload.data);
          if (payload.exited) {
            session.current = null;
            setRunning(false);
            onFinished();
          }
        },
      ).then((fn) => {
        if (disposed) fn();
        else unlisten = fn;
      });
    return () => {
      disposed = true;
      unlisten?.();
      resize.disconnect();
      t.dispose();
    };
  }, []);
  useEffect(() => {
    if (!launch || session.current?.id === launch.id) return;
    if (!isTauri()) {
      onError(
        "The interactive terminal is available in the Tauri desktop app.",
      );
      return;
    }
    session.current = launch;
    terminal.current?.clear();
    fit.current?.fit();
    invoke("terminal_start", {
      launch,
      cols: terminal.current?.cols || 80,
      rows: terminal.current?.rows || 24,
    })
      .then(() => {
        setRunning(true);
        terminal.current?.focus();
      })
      .catch((e) => {
        session.current = null;
        setRunning(false);
        onError(String(e));
        onFinished();
      });
  }, [launch]);
  async function stop() {
    // Forget the session first: the exit event that follows the kill must not finish it twice.
    const current = session.current;
    session.current = null;
    if (current)
      await invoke("terminal_stop", { id: current.id }).catch((e) => onError(String(e)));
    setRunning(false);
    onFinished();
  }
  useImperativeHandle(handle, () => ({ stop }));
  return (
    <aside className="agent-pane">
      <div className="pane-heading">
        <span>
          <TerminalSquare size={15} /> {t("Claude Code")}
        </span>
        <button
          className="icon-button"
          aria-label={t("Collapse agent pane")}
          onClick={onClose}
        >
          <PanelRightClose size={16} />
        </button>
      </div>
      <div className="agent-context">
        <div className="agent-context-line">
          <span className={running ? "status-dot live" : "status-dot"} />
          <span>{running ? t("Interactive session") : t("Step agent")}</span>
          <span className="muted">
            {(launch?.scope && t(launch.scope)) || (step === "1" ? t("Writing") : step === "6" ? t("Covers") : step === "5" ? t("Assembly") : target || t("Visuals"))}
          </span>
        </div>
        <p className="small-muted agent-mode">
          {t(claudeMode === "subscription" ? "Your Claude Code · subscription" : "Built-in Claude Code · API key")}
        </p>
        {launch && (
          <p title={launch.cwd} className="context-path">
            {launch.cwd.split(/[\\/]/).slice(-3).join(" / ")}
          </p>
        )}
      </div>
      <div className="terminal-wrap">
        <div className="terminal-host" ref={host} />
        {!running && !launch && (
          <div className="agent-empty">
            <div className="agent-symbol">✳</div>
            <h3>
              {t("Your production agent,")}
              <br />
              {t("right beside the work.")}
            </h3>
            <p>
              {t(
                step === "5"
                  ? "Claude Code starts editing the whole video, opening included, as soon as it opens. Guide it here at any time."
                  : step === "6"
                  ? "Your cover copy, sizes and style are prepared. Direct Claude Code to design covers in HTML and Three.js here."
                  : step === "1" && noWritingSkill
                  ? "No writing skill is selected. Give Claude Code your writing instructions here."
                  : directWriting
                  ? "Your writing skill is prepared. Give Claude Code your topic and requirements here; no source document is needed."
                  : "Source, skills, and selected segments are prepared automatically. Give your instructions here.",
              )}
            </p>
            <button
              className="button agent-activate"
              disabled={disabled || !isTauri()}
              onClick={onActivate}
            >
              <Play size={14} /> {t("Activate Claude Code")}
              <ArrowUpRight size={14} />
            </button>
            <span className="small-muted">
              {isTauri() ? t("An interactive agent for this step.") : t("Launch the desktop app to use the terminal.")}
            </span>
          </div>
        )}
      </div>
      <div className="agent-footer">
        {running ? (
          <button className="button small" onClick={stop}>
            <Square size={11} /> {t("Stop & refresh outputs")}
          </button>
        ) : (
          <span className="small-muted">
            {launch
              ? t("Session ended · outputs refreshed")
              : t("You direct the step. Grist manages the project.")}
          </span>
        )}
      </div>
    </aside>
  );
}
