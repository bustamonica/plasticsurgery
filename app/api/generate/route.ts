import { NextRequest, NextResponse } from "next/server";
import { buildEditPrompt } from "@/lib/prompt";
import {
  findBrand,
  findProfile,
  findShape,
  PreviewConfig,
  VOLUME_MAX,
  VOLUME_MIN,
} from "@/lib/implants";

export const runtime = "nodejs";
export const maxDuration = 90;

const ALLOWED_MIME = new Set(["image/jpeg", "image/png", "image/webp"]);
// Route handlers have no built-in body size limit in Next 15, so enforce one
// here: reject by Content-Length before buffering, then re-check the decoded
// payload (~8 MB of image data as base64).
const MAX_BODY_BYTES = 12_500_000;
const MAX_BASE64_LENGTH = 11_000_000;

const GEMINI_MODEL = "gemini-2.5-flash-image";

// Aspect ratios the Gemini image model can be pinned to. Snapping the output
// to the closest one keeps the before/after comparison aligned.
const SUPPORTED_ASPECTS: [string, number][] = [
  ["21:9", 21 / 9],
  ["16:9", 16 / 9],
  ["3:2", 3 / 2],
  ["4:3", 4 / 3],
  ["5:4", 5 / 4],
  ["1:1", 1],
  ["4:5", 4 / 5],
  ["3:4", 3 / 4],
  ["2:3", 2 / 3],
  ["9:16", 9 / 16],
];

function closestAspect(width: number, height: number): string {
  const ratio = width / height;
  let best = SUPPORTED_ASPECTS[0];
  for (const candidate of SUPPORTED_ASPECTS) {
    if (Math.abs(Math.log(ratio / candidate[1])) < Math.abs(Math.log(ratio / best[1]))) {
      best = candidate;
    }
  }
  return best[0];
}

// Per-IP sliding-window rate limit. In-memory, so it protects a
// single-instance deploy; put a shared store (e.g. Upstash) or a CDN rule in
// front for multi-instance serverless.
const RATE_LIMIT = 8;
const RATE_WINDOW_MS = 60_000;
const hits = new Map<string, number[]>();

function rateLimited(ip: string): boolean {
  const now = Date.now();
  const recent = (hits.get(ip) ?? []).filter((t) => now - t < RATE_WINDOW_MS);
  if (recent.length >= RATE_LIMIT) {
    hits.set(ip, recent);
    return true;
  }
  recent.push(now);
  hits.set(ip, recent);
  if (hits.size > 10_000) {
    for (const [key, times] of hits) {
      if (times.every((t) => now - t >= RATE_WINDOW_MS)) hits.delete(key);
    }
  }
  return false;
}

interface GenerateRequestBody {
  /** Raw base64 (no data: prefix). */
  image: string;
  mimeType: string;
  imageWidth?: number;
  imageHeight?: number;
  config: PreviewConfig;
}

export async function POST(req: NextRequest) {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
  if (rateLimited(ip)) {
    return NextResponse.json(
      { error: "Too many previews in a short time. Please wait a minute and try again." },
      { status: 429 },
    );
  }

  const contentLength = Number(req.headers.get("content-length"));
  if (!Number.isFinite(contentLength) || contentLength <= 0 || contentLength > MAX_BODY_BYTES) {
    return NextResponse.json(
      { error: "Photo is too large. Please use an image under 8 MB." },
      { status: 413 },
    );
  }

  let body: GenerateRequestBody;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const { image, mimeType, imageWidth, imageHeight, config } = body ?? {};

  if (typeof image !== "string" || image.length === 0) {
    return NextResponse.json({ error: "Missing photo." }, { status: 400 });
  }
  if (image.length > MAX_BASE64_LENGTH) {
    return NextResponse.json(
      { error: "Photo is too large. Please use an image under 8 MB." },
      { status: 413 },
    );
  }
  if (typeof mimeType !== "string" || !ALLOWED_MIME.has(mimeType)) {
    return NextResponse.json(
      { error: "Unsupported image format. Please upload a JPEG, PNG or WebP photo." },
      { status: 400 },
    );
  }
  if (
    !config ||
    !findBrand(config.brandId) ||
    !findShape(config.shape) ||
    !findProfile(config.profileId) ||
    typeof config.volumeCc !== "number" ||
    config.volumeCc < VOLUME_MIN ||
    config.volumeCc > VOLUME_MAX
  ) {
    return NextResponse.json({ error: "Invalid implant configuration." }, { status: 400 });
  }

  const apiKey = process.env.GEMINI_API_KEY;

  // Demo mode: no key configured. Echo the original photo back so the whole
  // flow (including the before/after slider) can be exercised end to end.
  if (!apiKey) {
    return NextResponse.json({
      demo: true,
      image,
      mimeType,
      message:
        "Demo mode: no AI key is configured, so this preview shows your original photo. Add a GEMINI_API_KEY to enable real previews.",
    });
  }

  const prompt = buildEditPrompt(config);
  const hasDimensions =
    typeof imageWidth === "number" &&
    typeof imageHeight === "number" &&
    imageWidth > 0 &&
    imageHeight > 0;

  let res: Response;
  try {
    res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-goog-api-key": apiKey,
        },
        body: JSON.stringify({
          contents: [
            {
              parts: [
                { inlineData: { mimeType, data: image } },
                { text: prompt },
              ],
            },
          ],
          generationConfig: {
            responseModalities: ["IMAGE"],
            ...(hasDimensions
              ? { imageConfig: { aspectRatio: closestAspect(imageWidth, imageHeight) } }
              : {}),
          },
        }),
        signal: AbortSignal.timeout(80_000),
      },
    );
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return NextResponse.json(
      {
        error: timedOut
          ? "The AI took too long to respond. Please try again."
          : "Could not reach the AI service. Please try again in a moment.",
      },
      { status: 502 },
    );
  }

  if (!res.ok) {
    const status = res.status;
    const detail = await res.text().catch(() => "");
    console.error(`Gemini API error ${status}: ${detail.slice(0, 500)}`);
    return NextResponse.json(
      {
        error:
          status === 429
            ? "The AI service is busy right now. Please wait a minute and try again."
            : "The AI service returned an error. Please try again.",
      },
      { status: 502 },
    );
  }

  interface GeminiPart {
    text?: string;
    inlineData?: { mimeType?: string; data?: string };
  }
  interface GeminiResponse {
    candidates?: {
      content?: { parts?: GeminiPart[] };
      finishReason?: string;
    }[];
    promptFeedback?: { blockReason?: string };
  }

  let data: GeminiResponse;
  try {
    data = (await res.json()) as GeminiResponse;
  } catch {
    return NextResponse.json(
      { error: "The AI did not return an image. Please try again." },
      { status: 502 },
    );
  }

  const blocked =
    data.promptFeedback?.blockReason ||
    data.candidates?.[0]?.finishReason === "SAFETY" ||
    data.candidates?.[0]?.finishReason === "IMAGE_SAFETY";
  const imagePart = data.candidates?.[0]?.content?.parts?.find((p) => p.inlineData?.data);

  if (!imagePart?.inlineData?.data) {
    if (blocked) {
      return NextResponse.json(
        {
          error:
            "The AI declined to edit this photo. Please use a well-lit, front-facing photo in a fitted top or sports bra (no nudity), and try again.",
        },
        { status: 422 },
      );
    }
    return NextResponse.json(
      { error: "The AI did not return an image. Please try again." },
      { status: 502 },
    );
  }

  return NextResponse.json({
    demo: false,
    image: imagePart.inlineData.data,
    mimeType: imagePart.inlineData.mimeType ?? "image/png",
  });
}
