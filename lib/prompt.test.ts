import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { buildCustomModelPrompt } from "./prompt.ts";
import type { CustomModelPromptInput, CustomModelView } from "./prompt.ts";

// Run with `npm test`.
//
// buildCustomModelPrompt() serves the custom model the instruction format
// build_caption() in training/scripts/build_dataset.py trained it on. The two
// must agree byte for byte, so both read the same golden file - this half
// asserts the TypeScript side, training/tests/test_captions.py the Python one.
// Neither language owns the golden.

const HERE = dirname(fileURLToPath(import.meta.url));
const CASES_PATH = join(HERE, "..", "training", "caption_parity_cases.json");

interface ParityCase {
  name: string;
  input: CustomModelPromptInput;
  caption: string;
}

const cases: ParityCase[] = JSON.parse(readFileSync(CASES_PATH, "utf8")).cases;

test("every golden caption is reproduced byte for byte", () => {
  assert.ok(cases.length > 0, "the parity contract should carry cases");
  for (const { name, input, caption } of cases) {
    assert.equal(buildCustomModelPrompt(input), caption, name);
  }
});

const VIEWS: CustomModelView[] = [
  "front",
  "oblique-left",
  "oblique-right",
  "side-left",
  "side-right",
];

const base: CustomModelPromptInput = { volumeCc: 350, shape: "round" };

test("each view states itself and the five read distinctly", () => {
  const prompts = VIEWS.map((view) => buildCustomModelPrompt({ ...base, view }));
  for (const prompt of prompts) {
    assert.match(prompt, /^The photograph is .+\. Edit this photo/);
  }
  assert.equal(new Set(prompts).size, VIEWS.length);
});

test("laterality follows the corpus convention: '-left' faces the camera left", () => {
  assert.match(
    buildCustomModelPrompt({ ...base, view: "side-left" }),
    /left side toward the camera/,
  );
  assert.match(
    buildCustomModelPrompt({ ...base, view: "oblique-right" }),
    /right side toward the camera/,
  );
});

test("an omitted view asserts none", () => {
  const prompt = buildCustomModelPrompt(base);
  assert.ok(prompt.startsWith("Edit this photo"));
  assert.ok(!prompt.includes("The photograph is"));
});

test("an omitted profile asserts none rather than defaulting to balanced", () => {
  const prompt = buildCustomModelPrompt(base);
  assert.ok(!prompt.includes("profile"));
  assert.ok(!prompt.includes("using"));
  assert.match(prompt, /upper breast\. The change should read as/);
});

test("an unrecognised profile id asserts none", () => {
  const prompt = buildCustomModelPrompt({ ...base, profileId: "ultra-high" });
  assert.ok(!prompt.includes("profile"));
});

test("a recorded profile still renders", () => {
  assert.match(
    buildCustomModelPrompt({ ...base, profileId: "high" }),
    /, using a high profile with noticeable forward projection and a rounder look\./,
  );
});

test("the volume is the literal figure, never bucketed", () => {
  for (const cc of [140, 245, 335, 505, 700]) {
    assert.match(buildCustomModelPrompt({ ...base, volumeCc: cc }), new RegExp(`with ${cc} cc `));
  }
  const steps = [];
  for (let cc = 140; cc <= 700; cc += 10) {
    steps.push(buildCustomModelPrompt({ ...base, volumeCc: cc }));
  }
  assert.equal(new Set(steps).size, steps.length);
});
