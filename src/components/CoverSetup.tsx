import { FileText, Image, TerminalSquare } from "lucide-react";
import { useId, useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { libraryChoices } from "../library";
import type { LibraryCatalog, Settings } from "../types";

/** The cover's creative choices, saved directly from its production page (or from auto production, without the heading). */
export default function CoverSetup({ settings, library, disabled, onChange, onEditStyles, copy, agentMode, heading = true }: {
  settings: Settings[string];
  library?: LibraryCatalog;
  disabled: boolean;
  onChange: (key: string, value: string | number | string[]) => Promise<boolean>;
  onEditStyles: () => void;
  copy: { title: string; subtitle: string };
  agentMode: string;
  heading?: boolean;
}) {
  const uid = useId();
  const { t } = useI18n();
  const presets = (settings.cover_image_size_presets ?? {}) as Record<string, string>;
  // One generation makes every chosen size, each with its own candidates.
  const saved = settings.cover_image_size_keys;
  const sizes = (Array.isArray(saved) ? saved.map(String) : []).filter((ratio) => Object.hasOwn(presets, ratio));
  function toggleSize(ratio: string) {
    // Saved in the list's order, which is the order the covers are made in.
    onChange("cover_image_size_keys", Object.keys(presets).filter((r) => r === ratio ? !sizes.includes(r) : sizes.includes(r)));
  }
  const style = String(settings.cover_image_style ?? "");
  const method = settings.cover_method === "agent" ? "agent" : "api";
  const styles = libraryChoices(library, "cover_styles");
  const count = Number(settings.cover_image_count ?? 1);
  const [countText, setCountText] = useState(String(count));
  useEffect(() => setCountText(String(count)), [count]);
  return <section className="cover-setup" aria-label={t("Cover setup")}>
    {heading && <div className="cover-setup-heading">
      <h2>{t("Cover setup")}</h2>
      <button className="button small" disabled={disabled} onClick={onEditStyles}>
        <FileText size={13} /> {t("Edit cover styles")}
      </button>
    </div>}
    <div className="cover-methods" role="group" aria-label={t("Cover generation method")}>
      {(["api", "agent"] as const).map((value) => <button type="button" key={value}
        className={`cover-method${method === value ? " selected" : ""}`} aria-pressed={method === value}
        disabled={disabled} onClick={() => onChange("cover_method", value)}>
        {value === "api" ? <Image size={18} /> : <TerminalSquare size={18} />}
        <span><strong>{t(value === "api" ? "Image API" : "Claude Code + HTML / Three.js")}</strong>
          <small>{t(value === "api" ? "Generate cover candidates with your image provider." : "Design editable HTML covers with 3D scenes, then render PNGs.")}</small></span>
      </button>)}
    </div>
    <div className="cover-copy"><span>{t("Selected cover copy")}</span><strong>{copy.title || t("Choose a cover title in Writing.")}</strong>
      {copy.subtitle && <p>{copy.subtitle}</p>}<small>{t("Edit the title and subtitle in Writing.")}</small></div>
    {method === "agent" && <p className="small-muted">{t("Uses the same Claude Code login and agent provider as Writing.")} {t(agentMode)}</p>}
    <div className="cover-setup-fields">
      <fieldset className="cover-size-list" disabled={disabled}>
        <legend>{t("Cover sizes")}</legend>
        {Object.entries(presets).map(([ratio, dimensions]) => <label key={ratio} className="cover-size-choice">
          <input type="checkbox" checked={sizes.includes(ratio)} onChange={() => toggleSize(ratio)}
            disabled={sizes.length === 1 && sizes.includes(ratio)} />
          <span>{ratio}</span>
          <span className="cover-size-dimensions">{String(dimensions).replace(/[x×]/g, " × ")}</span>
        </label>)}
        {!sizes.length && <p className="small-muted">{t("No cover size is chosen, so covers use the video size.")}</p>}
      </fieldset>
      <label className="field">
        <span>{t("Cover candidates per size")}</span>
        <input type="number" min={1} max={method === "agent" ? 20 : undefined} step={1} disabled={disabled} value={countText}
          onChange={(e) => setCountText(e.target.value)}
          onBlur={async (e) => {
            const next = Number(countText);
            if (!e.currentTarget.validity.valid || !Number.isInteger(next) || next < 1) {
              setCountText(String(count));
              return;
            }
            if (next !== count && !await onChange("cover_image_count", next)) setCountText(String(count));
          }}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
      </label>
    </div>
    <p className="small-muted">{t("Selections save automatically and apply when you generate covers.")}</p>
    <details className="cover-styles-disclosure">
      <summary>{t("Cover style")} · {t(styles.find((choice) => choice.id === style)?.label || style)}</summary>
    <fieldset className="cover-style-list" disabled={disabled}>
      <legend>{t("Cover style")}</legend>
      {!styles.some((choice) => choice.id === style) && <p className="small-muted">{t("Unavailable: {id}", { id: style })}</p>}
      {styles.map((choice) => <label key={choice.id} className={`cover-style-choice${choice.id === style ? " selected" : ""}`}>
        <input type="radio" name={`${uid}-cover-style`} value={choice.id} checked={choice.id === style}
          onChange={() => onChange("cover_image_style", choice.id)} />
        <span>
          <strong>{t(choice.label)}</strong>
          <span className="cover-style-description">{choice.description || t("No description available.")}</span>
        </span>
      </label>)}
    </fieldset>
    </details>
  </section>;
}
