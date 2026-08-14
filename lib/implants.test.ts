import { test } from "node:test";
import assert from "node:assert/strict";
import { availableProfiles, availableShapes, PROFILES, SHAPES } from "./implants.ts";

// Run with `npm test`. The gate reads NEXT_PUBLIC_ENABLE_UNBACKED_IMPLANT_OPTIONS
// on every call, so a single process can exercise both flag states.
const FLAG = "NEXT_PUBLIC_ENABLE_UNBACKED_IMPLANT_OPTIONS";

function withFlag<T>(value: string | undefined, fn: () => T): T {
  const previous = process.env[FLAG];
  if (value === undefined) delete process.env[FLAG];
  else process.env[FLAG] = value;
  try {
    return fn();
  } finally {
    if (previous === undefined) delete process.env[FLAG];
    else process.env[FLAG] = previous;
  }
}

const ids = (options: { id: string }[]) => options.map((o) => o.id);

test("unset flag hides the unbacked options and keeps every other one", () => {
  withFlag(undefined, () => {
    assert.deepEqual(ids(availableProfiles()), ["moderate", "moderate-plus", "high"]);
    assert.deepEqual(ids(availableShapes()), ["round"]);
  });
});

test("flag enabled offers the full canonical option lists", () => {
  withFlag("true", () => {
    assert.deepEqual(ids(availableProfiles()), ids(PROFILES));
    assert.deepEqual(ids(availableShapes()), ids(SHAPES));
    assert.ok(ids(availableProfiles()).includes("extra-high"));
    assert.ok(ids(availableShapes()).includes("teardrop"));
  });
});

test("only the literal value \"true\" opens the gate", () => {
  for (const value of ["", "false", "1", "TRUE", "yes"]) {
    withFlag(value, () => {
      assert.ok(!ids(availableProfiles()).includes("extra-high"), `profile leaked for "${value}"`);
      assert.ok(!ids(availableShapes()).includes("teardrop"), `shape leaked for "${value}"`);
    });
  }
});

test("the gate touches nothing but the two unbacked options", () => {
  const gated = new Set(["extra-high", "teardrop"]);
  withFlag(undefined, () => {
    assert.deepEqual(
      ids(availableProfiles()),
      ids(PROFILES).filter((id) => !gated.has(id)),
    );
    assert.deepEqual(
      ids(availableShapes()),
      ids(SHAPES).filter((id) => !gated.has(id)),
    );
  });
});
