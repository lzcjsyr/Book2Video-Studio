import { Check, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import type { LibraryChoice } from "../types";

export default function ImageStylePicker({ styles, selected, disabled, onSelect, onClose }: {
  styles: LibraryChoice[];
  selected: string;
  disabled: boolean;
  onSelect: (id: string) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
      if (event.key !== "Tab") return;
      const buttons = dialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
      if (!buttons?.length) return;
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, [onClose]);

  return createPortal(<div className="modal-backdrop" onPointerDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <section ref={dialog} className="modal image-style-picker" role="dialog" aria-modal="true"
      aria-labelledby="image-style-picker-title" aria-describedby="image-style-picker-help">
      <div className="modal-heading">
        <h2 id="image-style-picker-title">{t("Choose image style")}</h2>
        <button type="button" className="icon-button" aria-label={t("Close style picker")} onClick={onClose}><X size={18} /></button>
      </div>
      <p id="image-style-picker-help" className="small-muted image-style-picker-help">{t("Read the descriptions and select a style. Your choice saves automatically for this project.")}</p>
      <ul className="image-style-options" aria-label={t("Image styles")}>
        {styles.map((style) => <li key={style.id}>
          <button type="button" className={"image-style-option" + (style.id === selected ? " selected" : "")}
            aria-pressed={style.id === selected} disabled={disabled} onClick={() => onSelect(style.id)}>
            <span className="image-style-option-heading">
              <strong>{t(style.label)}</strong>
              {style.id === selected && <span className="image-style-selected"><Check size={12} />{t("Selected")}</span>}
            </span>
            <span className="image-style-description">{style.description || t("No description available.")}</span>
          </button>
        </li>)}
      </ul>
    </section>
  </div>, document.body);
}
