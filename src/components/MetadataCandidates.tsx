import { Plus, Trash2 } from "lucide-react";
import { useI18n } from "../i18n";
import type { MetadataField, MetadataSelections, TextData } from "../types";

const groups: { field: MetadataField; label: string }[] = [
  { field: "video_titles", label: "video titles" },
  { field: "cover_titles", label: "cover titles" },
  { field: "cover_subtitles", label: "cover subtitles" },
  { field: "golden_quotes", label: "golden quotes" },
];

// The existing engine stores each group's primary choice first.
export function primarySelections(data: TextData): MetadataSelections {
  return Object.fromEntries(
    groups.map(({ field }) => [field, data[field]?.length ? 0 : null]),
  ) as MetadataSelections;
}

export default function MetadataCandidates({
  value,
  selections,
  disabled,
  onChange,
  fields = groups.map((g) => g.field),
  showHeading = true,
  active = true,
  hideLegend = false,
}: {
  value: TextData;
  selections: MetadataSelections;
  disabled: boolean;
  onChange: (value: TextData, selections: MetadataSelections) => void;
  fields?: MetadataField[];
  showHeading?: boolean;
  active?: boolean;
  hideLegend?: boolean;
}) {
  const { t } = useI18n();

  function remove(field: MetadataField, index: number) {
    const candidates = (value[field] || []).filter((_, i) => i !== index);
    const current = selections[field];
    const next = !candidates.length
      ? null
      : current === null || current === index
        ? 0
        : current > index
          ? current - 1
          : current;
    onChange(
      { ...value, [field]: candidates },
      { ...selections, [field]: next },
    );
  }

  return (
    <section className="metadata-workspace" aria-label={t("Generation choices")}>
      {showHeading && <div className="metadata-heading">
        <span>{t("Generation choices")}</span>
        <p>{t("Select one per group. Save your choices before generating.")}</p>
      </div>}
      <div className="metadata-editor">
        {groups.filter((g) => fields.includes(g.field)).map(({ field, label }) => (
          <fieldset className="metadata-group" key={field} disabled={disabled}>
            <legend className={hideLegend ? "sr-only" : undefined}>{t(label)}</legend>
            <div className="metadata-candidate-list">
              {(value[field] || []).map((candidate, index) => {
                const selected = selections[field] === index;
                const params = { group: t(label), number: index + 1 };
                return (
                  <div
                    className={`metadata-candidate${selected ? " selected" : ""}`}
                    key={index}
                  >
                    <div className="metadata-candidate-header">
                      <label className="metadata-choice">
                        <input
                          type="radio"
                          name={`metadata-${field}`}
                          checked={selected}
                          aria-label={t("Use {group} candidate {number}", params)}
                          disabled={disabled}
                          onChange={() =>
                            onChange(value, { ...selections, [field]: index })
                          }
                        />
                        <span>{t("Candidate {number}", params)}</span>
                        <span className="metadata-choice-status">
                          {selected ? t(active ? "Selected for generation" : "Saved choice · opening disabled") : t("Use this")}
                        </span>
                      </label>
                      <button
                        className="metadata-remove icon-button"
                        type="button"
                        aria-label={t("Remove {group} candidate {number}", params)}
                        title={t("Remove candidate")}
                        disabled={disabled}
                        onClick={() => remove(field, index)}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                    <textarea
                      aria-label={t("Edit {group} candidate {number}", params)}
                      value={candidate}
                      rows={field === "golden_quotes" ? 4 : 2}
                      placeholder={t("Enter candidate text")}
                      disabled={disabled}
                      onChange={(e) =>
                        onChange(
                          {
                            ...value,
                            [field]: (value[field] || []).map((text, i) =>
                              i === index ? e.target.value : text,
                            ),
                          },
                          selections,
                        )
                      }
                    />
                  </div>
                );
              })}
              {!value[field]?.length && (
                <p className="metadata-empty">{t("No candidates yet")}</p>
              )}
            </div>
            <button
              className="metadata-add"
              type="button"
              aria-label={t("Add candidate to {group}", { group: t(label) })}
              disabled={disabled}
              onClick={() =>
                onChange(
                  { ...value, [field]: [...(value[field] || []), ""] },
                  {
                    ...selections,
                    [field]: selections[field] ?? 0,
                  },
                )
              }
            >
              <Plus size={13} /> {t("Add candidate")}
            </button>
          </fieldset>
        ))}
      </div>
    </section>
  );
}
