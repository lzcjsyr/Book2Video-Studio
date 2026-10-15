import assert from "node:assert/strict";
import { test } from "node:test";
import { parseSegmentNumbers } from "../src/visual-generation.ts";

test("manual selection supports ranges, duplicates, Chinese separators and the opening", () => {
  assert.deepEqual(parseSegmentNumbers("5-8, 2，5 0", [0, 1, 2, 3, 4, 5, 6, 7, 8]), [0, 2, 5, 6, 7, 8]);
  assert.deepEqual(parseSegmentNumbers("1–3", [1, 2, 3]), [1, 2, 3]);
});
test("invalid manual selection never falls back to generating everything", () => {
  for (const input of ["", "all", "3-1", "0", "2, 9", "1-99999999999999", "1.5", "-1", "1-3"]) {
    assert.throws(() => parseSegmentNumbers(input, [1, 2]));
  }
});
