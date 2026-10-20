import type { LibraryCatalog, LibraryGroup } from "./types";

const librarySteps: Record<string, number> = {
  writing_skills: 1, summary: 4, image_styles: 4, image_templates: 4,
  motion_styles: 4, motion_skills: 4, assembly_skills: 5, cover_styles: 6,
};
const templateLabels: Record<number, string> = {
  0: "All steps", 1: "Writing templates", 2: "Segmentation templates", 4: "Visual templates", 5: "Assembly templates", 6: "Cover templates",
};
// Templates that belong to no single step (agent_rules.md) are listed first, under "All steps".
const templateStep = (id: string) => id.startsWith("step1_5_") ? 2 : Number(id.match(/^step(\d)/)?.[1] ?? 0);

/** Presentation only: template IDs, files and save/history routes stay the same. */
export function orderedLibraryGroups(catalog: LibraryCatalog): (LibraryGroup & { step: number })[] {
  return catalog.groups.flatMap((group) => group.id === "templates"
    ? Object.entries(templateLabels).map(([step, label]) => ({ ...group, step: Number(step), label,
        items: group.items.filter((item) => templateStep(item.id) === Number(step)) })).filter((group) => group.items.length)
    : [{ ...group, step: librarySteps[group.id] || 0 }])
    .sort((a, b) => a.step - b.step);
}

export const librarySettings: Record<string, [string, string]> = {
  writing_skills: ["step1", "agent_skill"],
  image_styles: ["step4", "image_style_preset"],
  image_templates: ["step4", "image_prompt_template"],
  motion_styles: ["step4", "motion_style_preset"],
  assembly_skills: ["step5", "agent_skill"],
  cover_styles: ["step6", "cover_image_style"],
};
export const libraryChoices = (catalog: LibraryCatalog | undefined, group: string) =>
  catalog?.groups.find((g) => g.id === group)?.items || [];
