import {
  ImplantShape,
  PreviewConfig,
  findBrand,
  findProfile,
  findShape,
  VOLUME_MAX,
  VOLUME_MIN,
} from "./implants";

function sizeLanguage(cc: number): string {
  return cc < 250
    ? "a subtle increase of roughly half to one cup size"
    : cc < 400
      ? "a natural-looking increase of roughly one to one and a half cup sizes"
      : cc < 550
        ? "a clearly noticeable increase of roughly one and a half to two cup sizes"
        : cc < 700
          ? "a full increase of roughly two to two and a half cup sizes"
          : "a dramatic increase of roughly two and a half or more cup sizes";
}

/**
 * Builds the image-edit instruction sent to the AI model.
 *
 * The framing matters: this is a medical-aesthetics consultation tool, the
 * edit must be confined to breast size/shape, and everything else in the
 * photo must be preserved so the before/after comparison is honest.
 */
export function buildEditPrompt(config: PreviewConfig): string {
  const brand = findBrand(config.brandId);
  const profile = findProfile(config.profileId);
  const shape = findShape(config.shape);
  const cc = Math.min(VOLUME_MAX, Math.max(VOLUME_MIN, config.volumeCc));

  return [
    "You are assisting a plastic-surgery consultation tool that shows patients a realistic preview of breast augmentation results.",
    `Edit this photo to simulate the outcome of breast augmentation surgery with ${cc} cc ${
      shape?.promptDescriptor ?? "implants"
    }, using ${profile?.promptDescriptor ?? "a balanced profile"}${
      brand ? `, in the style of ${brand.promptDescriptor}` : ""
    }.`,
    `The change should read as ${sizeLanguage(cc)}, proportionate and anatomically plausible for this person's frame.`,
    "Strict requirements:",
    "- Only adjust the breast size and shape under the existing clothing.",
    "- Keep the person's identity, face, expression, pose, arms, skin tone, clothing, lighting and background exactly the same.",
    "- Keep the same camera angle, framing and image resolution.",
    "- The result must look like an unretouched photograph of the same person after surgery — realistic, tasteful and clinical, not exaggerated.",
    "Return only the edited image.",
  ].join("\n");
}

/** Clothing states the custom model is trained on (dataset_schema.json). */
export type CustomModelClothing = "nude" | "bra" | "top";

/** Inputs for the custom-model prompt, mirroring a training meta.json. */
export interface CustomModelPromptInput {
  volumeCc: number;
  shape: ImplantShape;
  profileId?: string;
  brandId?: string;
  clothing?: CustomModelClothing;
}

// Keep in sync with CLOTHING_LANGUAGE in training/scripts/build_dataset.py.
const CUSTOM_MODEL_CLOTHING_LANGUAGE: Record<CustomModelClothing, string> = {
  nude: "The subject is photographed nude from the waist up; render realistic natural skin and anatomy in the chest area.",
  bra: "The subject is wearing a bra; adjust only the breast size and shape under the existing bra and keep the bra itself unchanged.",
  top: "The subject is wearing a top; adjust only the breast size and shape under the existing top and keep the top itself unchanged.",
};

/**
 * Builds the instruction for the future self-hosted custom model (the
 * AI_PROVIDER swap seam). This MUST emit exactly the format of
 * build_caption() in training/scripts/build_dataset.py, because the model is
 * trained on those captions - the wording it sees at inference time has to
 * match the wording it was trained on.
 *
 * Unlike buildEditPrompt (Gemini), there is no persona, no requirement list
 * and no "under the existing clothing" boilerplate; clothing handling comes
 * from the explicit `clothing` field, same as the training captions.
 */
export function buildCustomModelPrompt(input: CustomModelPromptInput): string {
  const shape = findShape(input.shape);
  const profile = input.profileId ? findProfile(input.profileId) : undefined;
  const brand = input.brandId ? findBrand(input.brandId) : undefined;
  const cc = input.volumeCc;

  // No clothing to preserve in a nude photo; for clothed (or unknown) pairs,
  // explicitly pin the clothing. Mirrors build_caption().
  const preserve =
    input.clothing === "nude"
      ? "identity, pose, skin tone, lighting and background"
      : "identity, pose, skin tone, clothing, lighting and background";

  const parts = [
    `Edit this photo to simulate the outcome of breast augmentation surgery with ${cc} cc ${
      shape?.promptDescriptor ?? "implants"
    }, using ${profile?.promptDescriptor ?? "a balanced profile"}${
      brand ? `, in the style of ${brand.promptDescriptor}` : ""
    }. The change should read as ${sizeLanguage(cc)}.`,
    `Keep the person's ${preserve} exactly the same.`,
  ];
  if (input.clothing) {
    parts.push(CUSTOM_MODEL_CLOTHING_LANGUAGE[input.clothing]);
  }
  return parts.join(" ");
}
