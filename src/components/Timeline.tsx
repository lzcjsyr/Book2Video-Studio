import { useI18n } from "../i18n";
import { useEffect, useRef } from "react";
import { Images, Music2, ChevronLeft, ChevronRight, Quote, Clapperboard, Layers } from "lucide-react";
import type { Detail, Take } from "../types";
import { media } from "../api";
export const formatTime = (seconds: number) =>
  `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;
export default function Timeline({
  detail,
  selected,
  onSelect,
  version,
  openingEnabled,
  focus,
  onPick,
}: {
  detail: Detail;
  selected: number;
  onSelect: (n: number) => void;
  /** Visuals: a thumbnail also opens the list of the part's images and clips. */
  onPick?: (index: number, rect: DOMRect) => void;
  version: number;
  openingEnabled: boolean;
  /** The track the current step works on; it carries the selection highlight. */
  focus: "visual" | "voice" | "both";
}) {
  const { t } = useI18n();
  const active = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    active.current?.scrollIntoView({
      block: "nearest",
      inline: "nearest",
      behavior: "smooth",
    });
  }, [selected]);
  let elapsed = openingEnabled ? detail.opening.duration || 0 : 0;
  const offsets = detail.segments.map((s) => {
    const start = elapsed;
    elapsed += s.duration ?? s.estimated_duration;
    return start;
  });
  // The sequence shows only the take each part uses: a still, or a motion clip's keyframe.
  function thumb(part: { poster: string | null; takes: Take[]; motion: Detail["segments"][number]["motion"] }, opening = false) {
    const motion = part.motion;
    const rendered = Boolean(motion?.video && motion.video_current);
    return (
      <>
        {part.poster ? (
          <img loading="lazy" src={media(detail.id, part.poster, version)} alt="" />
        ) : (
          <span className="timeline-visual-placeholder">
            {opening ? <Quote size={16} /> : motion ? t("Motion") : t("No visual")}
          </span>
        )}
        {motion && (
          <span className={`clip-motion${rendered ? "" : " pending"}`}
            title={rendered ? t("Motion · rendered") : t("Motion · not rendered yet")}>
            <Clapperboard size={10} />
          </span>
        )}
        {onPick && part.takes.length > 1 && (
          <span className="clip-takes" title={t("{count} versions kept", { count: part.takes.length })}>
            <Layers size={9} /> {part.takes.length}
          </span>
        )}
      </>
    );
  }
  // Outdated parts are marked on their own track: the visual (step 4) or the recording (step 3).
  const outdated = (stage: "3" | "4", index: number) => {
    const stale = detail.state.stale[stage];
    return stale && (!stale.segments.length || stale.segments.includes(index)) ? stale.reason : null;
  };
  const marks = (index: number) => {
    const visual = outdated("4", index), voice = outdated("3", index);
    return {
      thumb: `timeline-thumb${visual ? " outdated" : ""}`,
      thumbTitle: visual ? t(visual) : undefined,
      voice: voice ? " outdated" : "",
      voiceTitle: voice ? t(voice) : undefined,
    };
  };
  const pick = (index: number, e: React.MouseEvent<HTMLButtonElement>) => {
    onSelect(index);
    onPick?.(index, (e.currentTarget.querySelector(".timeline-thumb") || e.currentTarget).getBoundingClientRect());
  };
  return (
    <section className={`timeline focus-${focus}`}>
      <div className="timeline-heading">
        <div>
          <Images size={15} />
          <span>{t("Sequence")}</span>
          <span className="muted">
            {t("{count} segments", { count: detail.segments.length })}
          </span>
        </div>
        <div>
          <button
            aria-label={t("Previous segment")}
            className="icon-button"
            disabled={selected <= (openingEnabled ? 0 : 1)}
            onClick={() => onSelect(selected - 1)}
          >
            <ChevronLeft size={15} />
          </button>
          <span className="mono muted">
            {String(selected).padStart(3, "0")} / {detail.segments.length}
          </span>
          <button
            aria-label={t("Next segment")}
            className="icon-button"
            disabled={selected >= detail.segments.length}
            onClick={() => onSelect(selected + 1)}
          >
            <ChevronRight size={15} />
          </button>
        </div>
      </div>
      <div className="timeline-body">
        <div className="track-labels">
          <span className="track-label-visual">
            <Images size={12} /> {t("VISUAL")}
          </span>
          <span className="track-label-voice">
            <Music2 size={12} /> {t("VOICE")}
          </span>
        </div>
        <div className="timeline-scroll">
          {openingEnabled && (
            <button
              ref={selected === 0 ? active : undefined}
              className={`timeline-item ${selected === 0 ? "selected" : ""}`}
              onClick={(e) => pick(0, e)}
              aria-label={t("Select opening")}
            >
              <span className="timeline-ruler">00:00</span>
              <span className={marks(0).thumb} title={marks(0).thumbTitle}>
                {thumb({ ...detail.opening, motion: null }, true)}
                <span className="clip-number">{t("Opening")}</span>
              </span>
              <span
                className={`voice-clip ${detail.opening.audio ? "available" : ""}${marks(0).voice}`}
                title={marks(0).voiceTitle}
              >
                <Music2 size={13} />
                <span>
                  {detail.opening.duration
                    ? t("{seconds}s", {
                        seconds: detail.opening.duration.toFixed(1),
                      })
                    : t("No audio")}
                </span>
              </span>
            </button>
          )}
          {detail.segments.map((s, i) => (
            <button
              key={s.index}
              ref={s.index === selected ? active : undefined}
              className={`timeline-item ${s.index === selected ? "selected" : ""}`}
              onClick={(e) => pick(s.index, e)}
              aria-label={t("Select segment {index}", { index: s.index })}
            >
              <span className="timeline-ruler">{formatTime(offsets[i])}</span>
              <span className={marks(s.index).thumb} title={marks(s.index).thumbTitle}>
                {thumb(s)}
                <span className="clip-number">
                  {String(s.index).padStart(2, "0")}
                </span>
              </span>
              <span className={`voice-clip ${s.audio ? "available" : ""}${marks(s.index).voice}`} title={marks(s.index).voiceTitle}>
                <Music2 size={13} />
                <span>
                  {s.duration
                    ? t("{seconds}s", { seconds: s.duration.toFixed(1) })
                    : t("No audio")}
                </span>
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="timeline-footer">
        <span>
          {detail.segments.some((s) => !s.duration)
            ? t("Estimated timing for segments without recordings")
            : t("Timing from actual recordings")}
        </span>
        <span className="mono">
          {t("{duration} total", { duration: formatTime(elapsed) })}
        </span>
      </div>
    </section>
  );
}
