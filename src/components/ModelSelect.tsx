import { useState } from "react";
import { useI18n } from "../i18n";

/** Existing model IDs stay editable even when absent from the preset list. */
export default function ModelSelect({ label, value, options, onChange }: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  const [custom, setCustom] = useState(false);
  const isCustom = custom || !options.includes(value);
  return <div className="field model-select">
    <label>
      <span>{label}</span>
      <select aria-label={label} value={isCustom ? "__custom__" : value} onChange={(event) => {
        const next = event.target.value;
        setCustom(next === "__custom__");
        if (next !== "__custom__") onChange(next);
      }}>
        {options.map((model) => <option key={model} value={model}>{model}</option>)}
        <option value="__custom__">{t("Custom model")}</option>
      </select>
    </label>
    {isCustom && <label>
      <span className="field-hint">{t("Custom model ID")}</span>
      <input aria-label={`${label} · ${t("Custom model ID")}`} value={value}
        onChange={(event) => onChange(event.target.value)} placeholder={t("Enter a model ID")} />
    </label>}
  </div>;
}
