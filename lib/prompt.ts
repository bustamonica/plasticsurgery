import {
  PreviewConfig,
  findBrand,
  findProfile,
  findShape,
  VOLUME_MAX,
  VOLUME_MIN,
} from "./implants";

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

  const sizeLanguage =
    cc < 250
      ? "a subtle increase of roughly half to one cup size"
      : cc < 400
        ? "a natural-looking increase of roughly one to one and a half cup sizes"
        : cc < 550
          ? "a clearly noticeable increase of roughly one and a half to two cup sizes"
          : cc < 700
            ? "a full increase of roughly two to two and a half cup sizes"
            : "a dramatic increase of roughly two and a half or more cup sizes";

  return [
    "You are assisting a plastic-surgery consultation tool that shows patients a realistic preview of breast augmentation results.",
    `Edit this photo to simulate the outcome of breast augmentation surgery with ${cc} cc ${
      shape?.promptDescriptor ?? "implants"
    }, using ${profile?.promptDescriptor ?? "a balanced profile"}${
      brand ? `, in the style of ${brand.promptDescriptor}` : ""
    }.`,
    `The change should read as ${sizeLanguage}, proportionate and anatomically plausible for this person's frame.`,
    "Strict requirements:",
    "- Only adjust the breast size and shape under the existing clothing.",
    "- Keep the person's identity, face, expression, pose, arms, skin tone, clothing, lighting and background exactly the same.",
    "- Keep the same camera angle, framing and image resolution.",
    "- The result must look like an unretouched photograph of the same person after surgery — realistic, tasteful and clinical, not exaggerated.",
    "Return only the edited image.",
  ].join("\n");
}
