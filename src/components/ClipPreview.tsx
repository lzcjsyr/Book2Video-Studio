import { Check, Clapperboard, FolderOpen, Loader2, Play, TerminalSquare, X } from "lucide-react";
import { isTauri } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { media } from "../api";
import { clipState, clipStateLabel } from "../takes";
import type { Take } from "../types";

/**
 * One motion clip up close: its video (or keyframes until it is rendered) and what can be done
 * with it — use it, render it, or have Claude Code revise this clip.
 */
export default function ClipPreview({
  project,
  title,
  take,
  inUse,
  narration,
  version,
  revisable,
  agentHere,
  agentElsewhere,
  locked,
  lockReason,
  rendering,
  onUse,
  onRevise,
  onRender,
  onFinish,
  onShowFiles,
  onClose,
}: {
  project: string;
  title: string;
  take: Take;
  inUse: boolean;
  narration: string;
  version: number;
  /** Claude Code works on narration segments; the opening's clips can only be viewed. */
  revisable: boolean;
  /** Claude Code has this clip open now. */
  agentHere: boolean;
  /** Claude Code is busy with another segment. */
  agentElsewhere: number | null;
  locked: boolean;
  lockReason: string;
  rendering: boolean;
  onUse: () => void;
  onRevise: () => void;
  onRender: () => void;
  onFinish: () => void;
  onShowFiles: (folder: string) => void;
  onClose: () => void;
}) {
  const { t, locale } = useI18n();
  const motion = take.motion;
  const frames = motion?.keyframes || [];
  // The video when there is one; a chosen keyframe otherwise (the middle one to start).
  const [frame, setFrame] = useState<string | null>(null);
  useEffect(() => setFrame(null), [take.id]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  const shown = frame && frames.includes(frame) ? frame : motion?.video ? null : frames[Math.floor(frames.length / 2)] || null;
  const state = clipState(motion);
  const time = (seconds: number) => new Date(seconds * 1000).toLocaleString(locale, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const canRender = Boolean(motion?.composition) && state !== "rendered";

  return (
    <div className="modal-backdrop" onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="modal clip-modal" role="dialog" aria-modal="true" aria-labelledby="clip-title">
        <div className="modal-heading">
          <h2 id="clip-title">
            <Clapperboard size={16} /> {title}
            {inUse && <span className="clip-in-use"><Check size={11} strokeWidth={3} /> {t("In use")}</span>}
          </h2>
          <button className="icon-button" aria-label={t("Close dialog")} onClick={onClose}><X size={18} /></button>
        </div>
        <div className="clip-viewer">
          {shown ? (
            <img src={media(project, shown, version)} alt={t("Keyframe {index}", { index: frames.indexOf(shown) + 1 })} />
          ) : motion?.video ? (
            <video key={`${motion.video}-${version}`} controls autoPlay src={media(project, motion.video, version)} aria-label={t("Motion clip preview")} />
          ) : (
            <div className="preview-empty">
              <Clapperboard size={28} />
              <h2>{t(agentHere ? "Claude Code is designing this clip." : "This clip has no keyframes yet.")}</h2>
              <p>{t(agentHere ? "Keyframes appear here as Claude Code saves its snapshots." : "Revise it with Claude Code to design it.")}</p>
            </div>
          )}
          {shown && <span className="preview-note">{t("Keyframe {index} of {count}", { index: frames.indexOf(shown) + 1, count: frames.length })}</span>}
        </div>
        {(frames.length > 0 || motion?.video) && (
          <div className="clip-frames" role="group" aria-label={t("Keyframes")}>
            {motion?.video && (
              <button className={!shown ? "selected" : ""} aria-pressed={!shown} onClick={() => setFrame(null)} title={t("Play the rendered video")}>
                <Play size={14} /> <span>{t("Video")}</span>
              </button>
            )}
            {frames.map((path, i) => (
              <button key={path} className={shown === path ? "selected" : ""} aria-pressed={shown === path}
                title={t("Show keyframe {index}", { index: i + 1 })} onClick={() => setFrame(path)}>
                <img loading="lazy" src={media(project, path, version)} alt={t("Keyframe {index}", { index: i + 1 })} />
              </button>
            ))}
          </div>
        )}
        <div className="clip-details">
          <div className="clip-status">
            <span className={`take-state ${state}`}>{t(clipStateLabel[state])}</span>
            <span>
              {state === "outdated"
                ? t("The composition changed after this video was rendered.")
                : motion?.composed ? t("Composition saved {time}", { time: time(motion.composed) }) : t("Made {time}", { time: time(take.created) })}
            </span>
          </div>
          {narration && <p className="clip-narration">{narration}</p>}
        </div>
        <div className="modal-actions clip-actions">
          {motion?.folder && (
            <button className="text-link" disabled={!isTauri()} title={motion.folder} onClick={() => onShowFiles(motion.folder)}>
              <FolderOpen size={13} /> {t("Show files")}
            </button>
          )}
          <span className="clip-actions-spacer" />
          {agentHere ? (
            <>
              <span className="small-muted">{t("Claude Code is working on this clip in the right pane.")}</span>
              <button className="button primary" disabled={!motion?.composition} onClick={onFinish}>
                <Play size={14} /> {t("Finish & render")}
              </button>
            </>
          ) : (
            <>
              {!inUse && (
                <button className="button" disabled={locked} title={locked ? t(lockReason) : undefined} onClick={onUse}>
                  <Check size={14} /> {t("Use this clip")}
                </button>
              )}
              {canRender && (
                <button className="button" disabled={locked || rendering} title={locked ? t(lockReason) : undefined} onClick={onRender}>
                  {rendering ? <Loader2 size={14} className="spin" /> : <Play size={14} />} {rendering ? t("Rendering…") : t("Render video")}
                </button>
              )}
              {revisable && (
                <button className="button primary" disabled={locked || agentElsewhere !== null}
                  title={agentElsewhere !== null ? t("Claude Code is working on segment {index}.", { index: agentElsewhere }) : locked ? t(lockReason) : undefined}
                  onClick={onRevise}>
                  <TerminalSquare size={14} /> {t("Revise with Claude Code")}
                </button>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
