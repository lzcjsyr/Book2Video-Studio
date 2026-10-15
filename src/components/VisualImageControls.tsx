import { ChevronDown, Pencil } from "lucide-react";
import { useCallback, useState } from "react";
import { useI18n } from "../i18n";
import { libraryChoices } from "../library";
import type { LibraryCatalog, Settings } from "../types";
import ImageStylePicker from "./ImageStylePicker";

const fields = [
  { key: "image_style_preset", group: "image_styles", label: "Image style", edit: "Edit image styles" },
  { key: "image_prompt_template", group: "image_templates", label: "Image prompt", edit: "Edit image templates" },
] as const;

/** Project-wide creative choices, kept beside the visual being reviewed. */
export default function VisualImageControls({ settings, library, disabled, onChange, onEdit }: {
  settings: Settings[string];
  library?: LibraryCatalog;
  disabled: boolean;
  onChange: (patch: Record<string, unknown>) => Promise<boolean>;
  onEdit: (group: string) => void;
}) {
  const { t } = useI18n();
  const [stylePickerOpen, setStylePickerOpen] = useState(false);
  const closeStylePicker = useCallback(() => setStylePickerOpen(false), []);
  return <section className="visual-image-controls" aria-label={t("Image generation settings")}>
    <div className="visual-image-controls-fields">
      {fields.map((field) => {
        const choices = libraryChoices(library, field.group);
        const value = String(settings[field.key] ?? "");
        const chosen = choices.find((choice) => choice.id === value);
        return <div className="visual-image-control" key={field.key}>
          <label>
            <span>{t(field.label)}</span>
            {field.group === "image_styles" ? <button type="button" className="image-style-trigger"
              aria-label={t("Image style")} aria-haspopup="dialog" aria-expanded={stylePickerOpen}
              title={chosen?.description || t("Choose image style")} disabled={disabled || !choices.length}
              onClick={() => setStylePickerOpen(true)}>
              <span>{chosen ? t(chosen.label) : value ? t("Unavailable: {id}", { id: value }) : t("Choose an option")}</span>
              <ChevronDown size={13} />
            </button> : <select aria-label={t(field.label)} value={value} title={chosen?.description || t(chosen?.label || field.label)}
              disabled={disabled || !choices.length}
              onChange={(e) => onChange({ [field.key]: e.target.value })}>
              {!chosen && <option value={value} disabled>{value ? t("Unavailable: {id}", { id: value }) : t("Choose an option")}</option>}
              {choices.map((choice) => <option key={choice.id} value={choice.id}>{t(choice.label)}</option>)}
            </select>}
          </label>
          <button type="button" className="icon-button" aria-label={t(field.edit)} title={t(field.edit)}
            disabled={disabled} onClick={() => onEdit(field.group)}><Pencil size={13} /></button>
        </div>;
      })}
    </div>
    <p className="small-muted">{t("Saved automatically. Applies to new images across this project.")}</p>
    {stylePickerOpen && <ImageStylePicker styles={libraryChoices(library, "image_styles")}
      selected={String(settings.image_style_preset ?? "")} disabled={disabled} onClose={closeStylePicker}
      onSelect={async (id) => {
        if (id === settings.image_style_preset || await onChange({ image_style_preset: id })) closeStylePicker();
      }} />}
  </section>;
}
