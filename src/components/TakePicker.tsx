import { Check, Clapperboard, Image, ListPlus, ListX, Loader2, Maximize2, Plus, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { media } from "../api";
import { clipState, clipStateLabel, numbers } from "../takes";
import DeleteControl from "./DeleteControl";
import type { Take } from "../types";

/** Where the picker opens: above a sequence thumbnail, or below a toolbar button. */
export interface PickerAnchor {
  index: number;
  rect: { top: number; bottom: number; left: number; width: number };
  placement: "above" | "below";
}

const WIDTH = 456;

/**
 * Every still image and motion clip of one part of the video. The one in use is marked; choosing
 * a still uses it, choosing a clip uses it and opens its preview.
 */
export default function TakePicker({
  project,
  anchor,
  label,
  takes,
  selected,
  version,
  opening,
  locked,
  lockReason,
  renderLock,
  generating,
  newImageLock,
  queue,
  newImageLabel,
  newImageHint,
  onSelect,
  onOpenClip,
  onNewImage,
  onNewClip,
  onUseBackground,
  onDelete,
  deleteLock,
  onClose,
}: {
  project: string;
  anchor: PickerAnchor;
  label: string;
  takes: Take[];
  selected: string | null;
  version: number;
  /** The opening: a clip of the quote over a background; its clips are rendered, not designed by Claude Code. */
  opening: boolean;
  locked: boolean;
  lockReason: string;
  /** Why a new opening cannot be rendered now, or "". */
  renderLock: string;
  generating: boolean;
  /** Why a new image cannot be made or queued now, or "". */
  newImageLock: string;
  /** "add": a job is running, so a new image joins the queue; "queued": it is in the queue already. */
  queue: "" | "add" | "queued";
  newImageLabel: string;
  newImageHint: string;
  onSelect: (take: Take) => void;
  onOpenClip: (take: Take) => void;
  onNewImage: () => void;
  onNewClip: () => void;
  onUseBackground: (take: Take) => void;
  /** Delete a version that is neither in use nor the opening's current background. */
  onDelete: (take: Take) => void;
  deleteLock: string;
  onClose: () => void;
}) {
  const { t, locale } = useI18n();
  const box = useRef<HTMLDivElement>(null);
  // Rendering a new opening is a real job, so a background asks before it starts one.
  const [asking, setAsking] = useState<string | null>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    // A press outside closes the list; the thumbnail that opened it reopens it on its own click.
    const press = (e: PointerEvent) => {
      const target = e.target as Element;
      // The button that toggles the list closes it with its own click.
      if (!box.current?.contains(target) && !target.closest?.("[data-picker-toggle]")) onClose();
    };
    window.addEventListener("keydown", key);
    window.addEventListener("pointerdown", press, true);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("pointerdown", press, true);
    };
  }, [onClose]);

  const number = numbers(takes);
  const stills = takes.filter((take) => take.kind === "still").reverse();
  const clips = takes.filter((take) => take.kind === "motion").reverse();
  const backgrounds = takes.filter((take) => take.kind === "background").reverse();
  const time = (seconds: number) =>
    new Date(seconds * 1000).toLocaleString(locale, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const left = Math.max(12, Math.min(window.innerWidth - WIDTH - 12, anchor.rect.left + anchor.rect.width / 2 - WIDTH / 2));
  const style: React.CSSProperties =
    anchor.placement === "above"
      ? { left, bottom: window.innerHeight - anchor.rect.top + 8, maxHeight: anchor.rect.top - 20 }
      : { left, top: anchor.rect.bottom + 6, maxHeight: window.innerHeight - anchor.rect.bottom - 20 };

  function tile(take: Take) {
    const background = take.kind === "background";
    // A background is never the visual itself; the one the opening renders with is "current".
    const inUse = background ? Boolean(take.current) : take.id === selected;
    const state = take.kind === "motion" ? clipState(take.motion) : null;
    const name = t({ still: "Image {index}", motion: "Clip {index}", background: "Background {index}" }[take.kind], { index: number.get(take.id)! });
    // Click uses a version; double-click also opens it: an image is used and the list closes, a clip opens its preview.
    const blocked = background ? renderLock : locked ? lockReason : "";
    const hint = {
      still: "Click to use this image · double-click to use it and close",
      motion: "Click to use this clip · double-click to preview it",
      background: "Render a new opening with this background",
    }[take.kind];
    return (
      <li key={take.id}>
        <button className={`take-tile${inUse ? " in-use" : ""}`} aria-pressed={inUse} disabled={Boolean(blocked)}
          title={t(blocked || hint)}
          onClick={() => (background ? setAsking(take.id) : onSelect(take))}
          onDoubleClick={() => (take.kind === "still" ? onClose() : take.kind === "motion" ? onOpenClip(take) : undefined)}>
          <span className="take-thumb">
            {take.poster ? <img loading="lazy" src={media(project, take.poster, version)} alt="" />
              : <span className="take-thumb-empty">{take.kind === "motion" ? <Clapperboard size={16} /> : <Image size={16} />}</span>}
            {inUse && <span className="take-in-use"><Check size={11} strokeWidth={3} /> {t(background ? "Current" : "In use")}</span>}
            {state && <span className={`take-state ${state}`}>{t(clipStateLabel[state])}</span>}
          </span>
          <span className="take-caption">
            <strong>{name}</strong>
            <span>{time(take.created)}</span>
          </span>
        </button>
        <div className="take-tools">
          {take.kind === "motion" && (
            <button className="take-tool" aria-label={t("Preview {item}", { item: name })} title={t("Preview {item}", { item: name })}
              onClick={() => onOpenClip(take)}>
              <Maximize2 size={12} />
            </button>
          )}
          {!inUse && <DeleteControl item={name} blocked={deleteLock} onDelete={() => onDelete(take)} />}
        </div>
        {asking === take.id && (
          <div className="delete-confirm render-confirm" role="group" aria-label={t("Render a new opening with {item}?", { item: name })}>
            <span>{t("Render a new opening with {item}?", { item: name })}</span>
            <div>
              <button className="button small primary" onClick={() => { setAsking(null); onUseBackground(take); }}>{t("Render")}</button>
              <button className="button small" onClick={() => setAsking(null)}>{t("Cancel")}</button>
            </div>
          </div>
        )}
      </li>
    );
  }

  return (
    <div ref={box} className={`take-picker ${anchor.placement}`} style={{ ...style, width: WIDTH }}
      role="dialog" aria-label={t("Visuals for {part}", { part: label })}>
      <div className="take-picker-heading">
        <div>
          <strong>{label}</strong>
          <span>{t(opening
            ? "The opening is the quote rendered over a background. Choose a clip to use it, or a background to render a new clip with it."
            : "Choose the visual this part uses. Every version is kept.")}</span>
        </div>
        <button className="icon-button" aria-label={t("Close")} onClick={onClose}><X size={15} /></button>
      </div>
      <div className="take-picker-body">
        {opening && (
          <section>
            <h4><Image size={12} /> {t("Backgrounds")} <span>{backgrounds.length}</span></h4>
            {backgrounds.length ? <ul className="take-grid">{backgrounds.map(tile)}</ul>
              : <p className="take-empty">{t("No backgrounds yet. New opening generates one.")}</p>}
          </section>
        )}
        {(!opening || stills.length > 0) && (
          <section>
            <h4><Image size={12} /> {t("Images")} <span>{stills.length}</span></h4>
            {stills.length ? <ul className="take-grid">{stills.map(tile)}</ul>
              : <p className="take-empty">{t("No images yet.")}</p>}
          </section>
        )}
        <section>
          <h4><Clapperboard size={12} /> {t(opening ? "Opening clips" : "Motion clips")} <span>{clips.length}</span></h4>
          {clips.length ? <ul className="take-grid">{clips.map(tile)}</ul>
            : <p className="take-empty">{t(opening ? "No opening clips yet." : "No motion clips yet. Claude Code designs them with HTML / Three.js.")}</p>}
        </section>
      </div>
      <div className="take-picker-actions">
        <button className="button small" disabled={Boolean(newImageLock)} aria-pressed={queue === "queued" || undefined}
          title={t(newImageLock || newImageHint)} onClick={onNewImage}>
          {generating && queue !== "queued" ? <Loader2 size={13} className="spin" />
            : queue === "queued" ? <ListX size={13} /> : queue === "add" ? <ListPlus size={13} /> : <Sparkles size={13} />} {t(newImageLabel)}
        </button>
        {!opening && (
          <button className="button small" disabled={locked} title={locked ? t(lockReason) : t("Claude Code designs a new clip; the existing ones are kept")} onClick={onNewClip}>
            <Plus size={13} /><Clapperboard size={13} /> {t("New motion clip")}
          </button>
        )}
      </div>
    </div>
  );
}
