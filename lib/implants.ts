// Catalog of implant options exposed in the configurator.
//
// The descriptions are intentionally general: exact product lines, sizes and
// availability vary by country and change over time, and the clinic should
// confirm this copy with a board-certified surgeon before going live.

export type ImplantShape = "round" | "teardrop";

export interface ImplantBrand {
  id: string;
  name: string;
  manufacturer: string;
  fill: string;
  blurb: string;
  highlights: string[];
  /** Short descriptor woven into the AI prompt. */
  promptDescriptor: string;
}

export interface ImplantProfile {
  id: string;
  name: string;
  blurb: string;
  /** Short descriptor woven into the AI prompt. */
  promptDescriptor: string;
}

export const BRANDS: ImplantBrand[] = [
  {
    id: "mentor",
    name: "Mentor",
    manufacturer: "Johnson & Johnson MedTech",
    fill: "MemoryGel cohesive silicone",
    blurb:
      "One of the most widely used implant brands in the world, known for a soft, natural feel and a long safety track record.",
    highlights: ["Soft, natural feel", "Large long-term safety studies", "Round and shaped options"],
    promptDescriptor: "soft cohesive silicone gel implants with a natural feel",
  },
  {
    id: "natrelle",
    name: "Natrelle",
    manufacturer: "Allergan Aesthetics (AbbVie)",
    fill: "Cohesive silicone gel (multiple gel softness levels)",
    blurb:
      "A broad portfolio with several gel cohesivity levels — from very soft to firmer, shape-holding “gummy” gel — so the look can be tuned from subtle to full.",
    highlights: ["Widest range of gel softness", "“Gummy” high-cohesivity options", "Many size and profile combinations"],
    promptDescriptor: "cohesive silicone gel implants with pronounced, shape-holding upper fullness",
  },
  {
    id: "motiva",
    name: "Motiva",
    manufacturer: "Establishment Labs",
    fill: "SmoothSilk shell with adaptive ProgressiveGel (Ergonomix line)",
    blurb:
      "A newer-generation implant (FDA-approved in 2024) whose gel adapts to movement — rounder when lying down, teardrop when upright — for a particularly natural, dynamic look.",
    highlights: ["Adaptive gel that moves with you", "Very natural upright teardrop look", "Modern low-texture SmoothSilk surface"],
    promptDescriptor:
      "modern ergonomic silicone implants that settle into a soft natural teardrop when upright",
  },
  {
    id: "sientra",
    name: "Sientra",
    manufacturer: "Tiger Aesthetics",
    fill: "High-strength cohesive (HSC / HSC+) silicone gel",
    blurb:
      "US-made cohesive gel implants offered exclusively through board-certified plastic surgeons, with round and shaped lines.",
    highlights: ["High-strength cohesive gel", "Sold only via board-certified surgeons", "Round and shaped lines"],
    promptDescriptor: "high-strength cohesive silicone gel implants",
  },
];

export const PROFILES: ImplantProfile[] = [
  {
    id: "moderate",
    name: "Moderate",
    blurb: "Wider base, gentler forward projection — the most understated silhouette.",
    promptDescriptor: "a moderate profile with a wide base and gentle forward projection",
  },
  {
    id: "moderate-plus",
    name: "Moderate plus",
    blurb: "A balanced middle ground — the most commonly chosen profile.",
    promptDescriptor: "a balanced moderate-plus profile",
  },
  {
    id: "high",
    name: "High",
    blurb: "Narrower base with more forward projection for a fuller, rounder look.",
    promptDescriptor: "a high profile with noticeable forward projection and a rounder look",
  },
  {
    id: "extra-high",
    name: "Extra high",
    blurb: "Maximum projection from the narrowest base — the most dramatic option.",
    promptDescriptor: "an extra-high profile with maximum forward projection",
  },
];

export const SHAPES: { id: ImplantShape; name: string; blurb: string; promptDescriptor: string }[] = [
  {
    id: "round",
    name: "Round",
    blurb: "Even fullness top and bottom, more visible upper-pole roundness and cleavage.",
    promptDescriptor: "round implants giving even fullness and visible roundness in the upper breast",
  },
  {
    id: "teardrop",
    name: "Teardrop",
    blurb: "Tapered at the top, fuller at the bottom — mimics the natural slope of the breast.",
    promptDescriptor:
      "anatomical teardrop implants giving a gently sloped upper breast and fuller lower pole, a natural-looking result",
  },
];

export const VOLUME_MIN = 150;
export const VOLUME_MAX = 800;
export const VOLUME_STEP = 25;
export const VOLUME_DEFAULT = 350;

/** Rough rule of thumb used across the industry: ~150–200 cc per cup size. */
export function describeVolume(cc: number): string {
  if (cc < 250) return "Subtle — roughly half to one cup size";
  if (cc < 400) return "Natural — roughly one to one and a half cup sizes";
  if (cc < 550) return "Noticeable — roughly one and a half to two cup sizes";
  if (cc < 700) return "Full — roughly two to two and a half cup sizes";
  return "Dramatic — roughly two and a half or more cup sizes";
}

export interface PreviewConfig {
  brandId: string;
  shape: ImplantShape;
  profileId: string;
  volumeCc: number;
}

export function findBrand(id: string): ImplantBrand | undefined {
  return BRANDS.find((b) => b.id === id);
}

export function findProfile(id: string): ImplantProfile | undefined {
  return PROFILES.find((p) => p.id === id);
}

export function findShape(id: string) {
  return SHAPES.find((s) => s.id === id);
}
