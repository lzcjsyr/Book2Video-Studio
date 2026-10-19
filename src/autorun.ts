import type { NeedId } from "./keys";
import type { AutoPlan, AutoPlanStep, AutoRunStep, Settings } from "./types";

type Translate = (key: string, values?: Record<string, string | number>) => string;

/** How each step of an automatic run ended, or where it is. */
export const autoStatus: Record<AutoRunStep["status"], string> = {
  pending: "Not started",
  running: "Running",
  done: "Done",
  skipped: "Up to date · skipped",
  failed: "Failed",
  stopped: "Stopped",
};

export const segmentCount = (t: Translate, count: number) => (count === 1 ? t("1 segment") : t("{count} segments", { count }));

/** What a step of the next run will do, in one line. `everything`: the run makes everything again. */
export function planText(t: Translate, plan: AutoPlanStep | undefined, segments: number, settings: Settings, everything = false): string {
  if (!plan) return t("Checking what to make…");
  if (plan.off) return t("Left out of this run");
  // Without segments yet (or when everything is made again), every segment is made after Segments.
  const fresh = segments === 0 || everything;
  switch (plan.step) {
    case "2":
      return plan.run ? t(segments === 0 ? "Split the manuscript into segments" : "Split the manuscript again")
        : t("Up to date · {segments}", { segments: segmentCount(t, segments) });
    case "3": {
      if (!plan.run) return t("Up to date · every recording is current");
      if (plan.after) return t(fresh ? "Record every segment" : "Record new and changed segments");
      if (!plan.segments) return t("Record the opening");
      return t(plan.opening ? "Record {segments} and the opening" : "Record {segments}", { segments: segmentCount(t, plan.segments) });
    }
    case "4": {
      if (!plan.run) return t("Up to date · every visual is current");
      if (plan.after) return t(fresh ? "Make a picture for every segment" : "Make pictures for new and changed segments");
      const items = [];
      if (plan.images) items.push(plan.images === 1 ? t("1 image") : t("{count} images", { count: plan.images }));
      if (plan.motion) items.push(plan.motion === 1 ? t("1 motion clip") : t("{count} motion clips", { count: plan.motion }));
      if (plan.opening) items.push(t("the opening background"));
      const list = items.join(t(", "));
      if (plan.summary === "write") return t("Content summary first, then make {items}", { items: list });
      if (plan.summary === "rewrite") return t("New content summary first, then make {items}", { items: list });
      return t("Make {items}", { items: list });
    }
    case "5": {
      if (!plan.run) return t("Up to date · the video is current");
      if (!plan.after && plan.jobs && !plan.jobs.includes("assembly")) return t("Build the 3:4 copy");
      const portrait = settings.three_by_four?.enabled !== false;
      if (settings.step5?.assembly_method === "agent")
        return t(portrait ? "Claude Code edits the video; Studio draws the 3:4 copy" : "Claude Code edits the video");
      return t(portrait ? "Assemble the video and the 3:4 copy" : "Assemble the video");
    }
    default:
      if (!plan.run) return t("Up to date · covers are current");
      if (settings.step6?.cover_method === "agent")
        return t(plan.jobs?.join() === "covers" ? "Render the saved HTML covers" : "Claude Code designs HTML covers; Studio renders them");
      return t("Generate cover candidates");
  }
}

/** The API keys (or Claude Code logins) a run with this plan uses. */
export function autoNeeds(plan: AutoPlan | null, settings: Settings, { motion = false, summary = false } = {}): NeedId[] {
  if (!plan) return [];
  const step = (id: string) => plan.steps.find((s) => s.step === id);
  const runs = (id: string) => Boolean(step(id)?.run);
  const agentSplit = settings.step2?.split_mode === "agent";
  const visuals = step("4");
  const needs: NeedId[] = [];
  if (runs("2") && agentSplit) needs.push("segments");
  if (runs("3")) needs.push("voice");
  if (visuals?.run) {
    // After new segments, which kinds of visuals are needed is decided by them.
    const after = Boolean(visuals.after);
    if (after || visuals.images || visuals.opening) needs.push("images");
    if (visuals.summary || (after && !summary)) needs.push("direction");
    if (visuals.motion || (after && (motion || agentSplit))) needs.push("motion_agent");
  }
  if (runs("5") && settings.step5?.assembly_method === "agent") needs.push("assembly_agent");
  if (runs("6")) needs.push("covers");
  return needs;
}
