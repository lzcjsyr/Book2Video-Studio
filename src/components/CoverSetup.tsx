import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { libraryChoices } from "../library";
import type { LibraryCatalog, Settings } from "../types";

const defaultSizes = { "16:9": "3200x1800", "9:16": "1800x3200", "4:3": "3000x2250", "3:4": "2250x3000", "1:1": "2500x2500" };

/** The cover's creative choices, saved directly from its production page. */
export default function CoverSetup({ settings, library, disabled, onChange, onEditStyles }: {
  settings: Settings[string];
  library?: LibraryCatalog;
  disabled: boolean;
  onChange: (key: string, value: string | number) => Promise<boolean>;
  onEditStyles: () => void;
}) {
  const { t } = useI18n();
  const sizes = settings.cover_image_size_presets;
  const presets = sizes && typeof sizes === "object" && !Array.isArray(sizes) ? sizes : defaultSizes;
  const size = String(settings.default_cover_image_size_key ?? "1:1");
  const style = String(settings.cover_image_style ?? "");
  const styles = libraryChoices(library, "cover_styles");
  const count = Number(settings.cover_image_count ?? 1);
  const [countText, setCountText] = useState(String(count));
  useEffect(() => setCountText(String(count)), [count]);
  return <section className="cover-setup" aria-label={t("Cover setup")}>
    <div className="cover-setup-heading">
      <h2>{t("Cover setup")}</h2>
      <button className="button small" disabled={disabled} onClick={onEditStyles}>
        <FileText size={13} /> {t("Edit cover styles")}
      </button>
    </div>
    <div className="cover-setup-fields">
      <label className="field">
        <span>{t("Cover size")}</span>
        <select disabled={disabled} value={size} onChange={(e) => onChange("default_cover_image_size_key", e.target.value)}>
          {!Object.hasOwn(presets, size) && <option value={size}>{t("Unavailable: {id}", { id: size })}</option>}
          {Object.entries(presets).map(([ratio, dimensions]) => <option key={ratio} value={ratio}>
            {ratio} · {String(dimensions).replace(/[x×]/g, " × ")}
          </option>)}
        </select>
      </label>
      <label className="field">
        <span>{t("Cover candidates")}</span>
        <input type="number" min={1} step={1} disabled={disabled} value={countText}
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
    <fieldset className="cover-style-list" disabled={disabled}>
      <legend>{t("Cover style")}</legend>
      {!styles.some((choice) => choice.id === style) && <p className="small-muted">{t("Unavailable: {id}", { id: style })}</p>}
      {styles.map((choice) => <label key={choice.id} className={`cover-style-choice${choice.id === style ? " selected" : ""}`}>
        <input type="radio" name="cover-style" value={choice.id} checked={choice.id === style}
          onChange={() => onChange("cover_image_style", choice.id)} />
        <span>
          <strong>{t(choice.label)}</strong>
          <span className="cover-style-description">{choice.description || t("No description available.")}</span>
        </span>
      </label>)}
    </fieldset>
  </section>;
}
