/** Parse explicit segment numbers without silently broadening an invalid selection. */
export function parseSegmentNumbers(value: string, available: number[]): number[] {
  const allowed = new Set(available);
  const result = new Set<number>();
  const parts = value.trim().split(/[,，\s]+/).filter(Boolean);
  if (!parts.length) throw new Error("Enter segment numbers, for example 2, 5-8.");
  for (const part of parts) {
    const match = /^(\d+)(?:[-–—](\d+))?$/.exec(part);
    if (!match) throw new Error("Enter segment numbers, for example 2, 5-8.");
    const start = Number(match[1]), end = Number(match[2] ?? match[1]);
    if (end < start || end - start >= allowed.size || !allowed.has(start) || !allowed.has(end))
      throw new Error("Choose segment numbers that exist in this project.");
    for (let index = start; index <= end; index++) {
      if (!allowed.has(index)) throw new Error("Choose segment numbers that exist in this project.");
      result.add(index);
    }
  }
  return [...result].sort((a, b) => a - b);
}
