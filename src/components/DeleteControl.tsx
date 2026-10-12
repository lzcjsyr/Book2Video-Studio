import { Trash2 } from "lucide-react";
import { useState } from "react";
import { useI18n } from "../i18n";

/** A small delete button that asks once in place ("Delete Image 2?") before it deletes. */
export default function DeleteControl({ item, blocked, onDelete }: {
  /** What is deleted, as people see it ("Image 2", "Cover 3"). */
  item: string;
  /** Why it cannot be deleted right now, or "". */
  blocked: string;
  onDelete: () => void;
}) {
  const { t } = useI18n();
  const [asking, setAsking] = useState(false);
  if (asking)
    return (
      <div className="delete-confirm" role="group" aria-label={t("Delete {item}?", { item })}>
        <span>{t("Delete {item}?", { item })}</span>
        <div>
          <button className="button small danger" disabled={Boolean(blocked)} title={blocked ? t(blocked) : undefined}
            onClick={() => { setAsking(false); onDelete(); }}>{t("Delete")}</button>
          <button className="button small" onClick={() => setAsking(false)}>{t("Cancel")}</button>
        </div>
      </div>
    );
  return (
    <button className="delete-control" aria-label={t("Delete {item}", { item })} disabled={Boolean(blocked)}
      title={blocked ? t(blocked) : t("Delete {item}; Revision history keeps a copy", { item })}
      onClick={() => setAsking(true)}>
      <Trash2 size={12} />
    </button>
  );
}
