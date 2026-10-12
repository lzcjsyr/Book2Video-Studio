import type { Motion, Take } from "./types";

/** Each take's number among its kind ("Image 2", "Clip 1"), in the order they were made. */
export function numbers(takes: Take[]) {
  const count = { still: 0, motion: 0, background: 0 };
  return new Map(takes.map((take) => [take.id, ++count[take.kind]]));
}

/** Where a motion clip stands, from Claude Code's composition to a current render. */
export type ClipState = "rendered" | "outdated" | "unrendered" | "empty";
export function clipState(motion: Motion | null | undefined): ClipState {
  if (motion?.video) return motion.video_current ? "rendered" : "outdated";
  return motion?.composition ? "unrendered" : "empty";
}
export const clipStateLabel: Record<ClipState, string> = {
  rendered: "Rendered",
  outdated: "Render out of date",
  unrendered: "Not rendered",
  empty: "Not designed yet",
};
