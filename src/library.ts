import type { LibraryCatalog } from "./types";

export const librarySettings: Record<string, [string, string]> = {
  writing_skills: ["step1", "agent_skill"],
  image_styles: ["step4", "image_style_preset"],
  image_templates: ["step4", "image_prompt_template"],
  motion_styles: ["step4", "hyperframes_style_preset"],
  assembly_skills: ["step5", "agent_skill"],
  cover_styles: ["step6", "cover_image_style"],
};
export const libraryChoices = (catalog: LibraryCatalog | undefined, group: string) =>
  catalog?.groups.find((g) => g.id === group)?.items || [];
