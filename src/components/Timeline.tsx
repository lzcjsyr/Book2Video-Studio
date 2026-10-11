import { useI18n } from "../i18n";
import { useEffect, useRef } from "react";
import { Images, Music2, ChevronLeft, ChevronRight } from "lucide-react";
import type { Detail, Segment } from "../types";
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
}: {
  detail: Detail;
  selected: number;
  onSelect: (n: number) => void;
  version: number;
  openingEnabled: boolean;
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
  function thumb(s: Segment) {
    return s.visual && !/\.(mp4|mov|webm)$/i.test(s.visual) ? (
      <img loading="lazy" src={media(detail.id, s.visual, version)} alt="" />
    ) : (
      <span className="timeline-visual-placeholder">
        {s.visualizer === "hf" ? t("Motion") : t("No visual")}
      </span>
    );
  }
  return (
    <section className="timeline">
      <div className="timeline-heading">
        <div>
          <Images size={14} />
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
          <span>
            <Images size={13} /> {t("VISUAL")}
          </span>
          <span>
            <Music2 size={13} /> {t("VOICE")}
          </span>
        </div>
        <div className="timeline-scroll">
          {openingEnabled && (
            <button
              className={`timeline-item ${selected === 0 ? "selected" : ""}`}
              onClick={() => onSelect(0)}
              aria-label={t("Select opening")}
            >
              <span className="timeline-ruler">00:00</span>
              <span className="timeline-thumb">
                {detail.opening.visual &&
                !/\.(mp4|mov|webm)$/i.test(detail.opening.visual) ? (
                  <img
                    src={media(detail.id, detail.opening.visual, version)}
                    alt={t("Opening visual")}
                  />
                ) : (
                  <span className="timeline-visual-placeholder">
                    {t("Opening")}
                  </span>
                )}
                <span className="clip-number">{t("Opening")}</span>
              </span>
              <span
                className={`voice-clip ${detail.opening.audio ? "available" : ""}`}
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
              onClick={() => onSelect(s.index)}
              aria-label={t("Select segment {index}", { index: s.index })}
            >
              <span className="timeline-ruler">{formatTime(offsets[i])}</span>
              <span className="timeline-thumb">
                {thumb(s)}
                <span className="clip-number">
                  {String(s.index).padStart(2, "0")}
                </span>
              </span>
              <span className={`voice-clip ${s.audio ? "available" : ""}`}>
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
