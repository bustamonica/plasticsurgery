"use client";

import { useEffect, useRef, useState } from "react";
import { BeforeAfterSlider } from "@/components/BeforeAfterSlider";
import { PhotoUpload, UploadedPhoto } from "@/components/PhotoUpload";
import {
  BRANDS,
  describeVolume,
  ImplantShape,
  PreviewConfig,
  PROFILES,
  SHAPES,
  VOLUME_DEFAULT,
  VOLUME_MAX,
  VOLUME_MIN,
  VOLUME_STEP,
} from "@/lib/implants";

interface PreviewResult {
  dataUrl: string;
  mimeType: string;
  demo: boolean;
  message?: string;
  /** The configuration that actually produced this image. */
  config: PreviewConfig;
}

const EXTENSION_BY_MIME: Record<string, string> = {
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
};

export default function StudioPage() {
  const [photo, setPhoto] = useState<UploadedPhoto | null>(null);
  const [consent, setConsent] = useState(false);

  const [brandId, setBrandId] = useState(BRANDS[0].id);
  const [shape, setShape] = useState<ImplantShape>("round");
  const [profileId, setProfileId] = useState("moderate-plus");
  const [volumeCc, setVolumeCc] = useState(VOLUME_DEFAULT);

  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Invalidates in-flight generations when the photo changes, so a slow
  // response for photo A can never be shown against photo B.
  const requestIdRef = useRef(0);
  const resultRef = useRef<HTMLDivElement>(null);

  const canGenerate = Boolean(photo) && consent && !generating;

  useEffect(() => {
    if (result && resultRef.current) {
      // On phones the configurator sits below the photo, so the finished
      // preview would otherwise appear far above the viewport.
      resultRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
      resultRef.current.focus({ preventScroll: true });
    }
  }, [result]);

  function replacePhoto(next: UploadedPhoto | null) {
    requestIdRef.current += 1;
    setPhoto(next);
    setResult(null);
    setError(null);
    setGenerating(false);
  }

  async function generate() {
    if (!photo || !consent || generating) return;
    const requestId = ++requestIdRef.current;
    const config: PreviewConfig = { brandId, shape, profileId, volumeCc };
    setGenerating(true);
    setError(null);
    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image: photo.base64,
          mimeType: photo.mimeType,
          imageWidth: photo.width,
          imageHeight: photo.height,
          config,
        }),
      });
      const data = await res.json().catch(() => null);
      if (requestIdRef.current !== requestId) return; // photo was replaced mid-flight
      if (!res.ok || !data?.image) {
        setError(data?.error ?? "Something went wrong. Please try again.");
        return;
      }
      setResult({
        dataUrl: `data:${data.mimeType};base64,${data.image}`,
        mimeType: data.mimeType,
        demo: Boolean(data.demo),
        message: data.message,
        config,
      });
    } catch {
      if (requestIdRef.current === requestId) {
        setError("Network error — please check your connection and try again.");
      }
    } finally {
      if (requestIdRef.current === requestId) {
        setGenerating(false);
      }
    }
  }

  function downloadResult() {
    if (!result) return;
    const ext = EXTENSION_BY_MIME[result.mimeType] ?? "png";
    const { brandId: b, shape: s, volumeCc: v } = result.config;
    const a = document.createElement("a");
    a.href = result.dataUrl;
    a.download = `preview-${b}-${s}-${v}cc.${ext}`;
    a.click();
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
      <div className="max-w-2xl">
        <h1 className="font-display text-4xl tracking-tight text-ink-950 sm:text-5xl">
          Preview studio
        </h1>
        <p className="mt-3 text-ink-500">
          Upload a photo, choose your implant options, and generate a realistic AI
          before &amp; after. Nothing is stored on our servers — your photo is used only
          to create the preview in this session.
        </p>
      </div>

      {/* Announce progress and completion to screen readers. */}
      <p aria-live="polite" className="sr-only">
        {generating
          ? "Generating your preview."
          : result
            ? "Preview ready. A before and after comparison slider is available."
            : ""}
      </p>

      <div className="mt-10 grid gap-10 lg:grid-cols-[1fr_400px]">
        {/* ============ Left: photo & result ============ */}
        <div>
          <StepLabel n={1} title="Your photo" />
          {!photo ? (
            <PhotoUpload onPhoto={(p) => replacePhoto(p)} />
          ) : (
            <div>
              {result ? (
                <div ref={resultRef} tabIndex={-1} className="outline-none">
                  <BeforeAfterSlider beforeSrc={photo.dataUrl} afterSrc={result.dataUrl} />
                </div>
              ) : (
                <div className="relative overflow-hidden rounded-2xl bg-ink-950">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={photo.dataUrl} alt="Your uploaded photo" className="block w-full" />
                  {generating && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-ink-950/60 backdrop-blur-sm">
                      <span className="size-8 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                      <span className="text-sm font-medium text-white">
                        Generating your preview… usually 10–30 seconds
                      </span>
                    </div>
                  )}
                </div>
              )}

              {result?.demo && (
                <p className="mt-3 rounded-xl bg-blush-50 px-4 py-3 text-sm text-blush-800">
                  {result.message ??
                    "Demo mode: this shows your original photo. Configure an AI key to enable real previews."}
                </p>
              )}

              <div className="mt-4 flex flex-wrap gap-3">
                {result && (
                  <button
                    type="button"
                    onClick={downloadResult}
                    className="rounded-full bg-ink-950 px-5 py-2.5 text-sm font-medium text-cream-50 transition hover:bg-blush-700"
                  >
                    Download after image
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => replacePhoto(null)}
                  className="rounded-full border border-cream-300 bg-white px-5 py-2.5 text-sm font-medium text-ink-700 transition hover:border-blush-300 hover:text-ink-950"
                >
                  Use a different photo
                </button>
              </div>

              {result && (
                <p className="mt-4 text-xs leading-relaxed text-ink-400">
                  This is an AI-generated illustration, not a medical prediction. Actual
                  results depend on your anatomy, the surgical plan and healing — review
                  options with a board-certified plastic surgeon.
                </p>
              )}
            </div>
          )}
        </div>

        {/* ============ Right: configurator ============ */}
        <aside className="lg:sticky lg:top-24 lg:self-start">
          <StepLabel n={2} title="Implant options" />
          <div className="space-y-6 rounded-2xl border border-cream-200 bg-white p-5 shadow-sm">
            {/* Brand */}
            <fieldset>
              <legend className="text-sm font-semibold text-ink-900">Brand</legend>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {BRANDS.map((b) => (
                  <button
                    key={b.id}
                    type="button"
                    onClick={() => setBrandId(b.id)}
                    aria-pressed={brandId === b.id}
                    className={`rounded-xl border px-3 py-2.5 text-left transition ${
                      brandId === b.id
                        ? "border-blush-500 bg-blush-50 ring-1 ring-blush-500"
                        : "border-cream-200 hover:border-blush-300"
                    }`}
                  >
                    <span className="block text-sm font-medium text-ink-950">{b.name}</span>
                    <span className="mt-0.5 block text-[11px] leading-tight text-ink-400">
                      {b.manufacturer}
                    </span>
                  </button>
                ))}
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-400">
                {BRANDS.find((b) => b.id === brandId)?.blurb}
              </p>
            </fieldset>

            {/* Shape */}
            <fieldset>
              <legend className="text-sm font-semibold text-ink-900">Shape</legend>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {SHAPES.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => setShape(s.id)}
                    aria-pressed={shape === s.id}
                    className={`rounded-xl border px-3 py-2.5 text-left transition ${
                      shape === s.id
                        ? "border-blush-500 bg-blush-50 ring-1 ring-blush-500"
                        : "border-cream-200 hover:border-blush-300"
                    }`}
                  >
                    <span className="block text-sm font-medium text-ink-950">{s.name}</span>
                    <span className="mt-0.5 block text-[11px] leading-tight text-ink-400">
                      {s.blurb}
                    </span>
                  </button>
                ))}
              </div>
            </fieldset>

            {/* Profile */}
            <div>
              <label htmlFor="profile" className="text-sm font-semibold text-ink-900">
                Profile (projection)
              </label>
              <select
                id="profile"
                value={profileId}
                onChange={(e) => setProfileId(e.target.value)}
                className="mt-2 w-full rounded-xl border border-cream-200 bg-white px-3 py-2.5 text-sm text-ink-900 focus:border-blush-500 focus:ring-2 focus:ring-blush-500 focus:outline-none"
              >
                {PROFILES.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-xs leading-relaxed text-ink-400">
                {PROFILES.find((p) => p.id === profileId)?.blurb}
              </p>
            </div>

            {/* Volume */}
            <div>
              <div className="flex items-baseline justify-between">
                <label htmlFor="volume" className="text-sm font-semibold text-ink-900">
                  Volume
                </label>
                <span className="font-display text-xl text-blush-700">{volumeCc} cc</span>
              </div>
              <input
                id="volume"
                type="range"
                className="volume mt-3"
                min={VOLUME_MIN}
                max={VOLUME_MAX}
                step={VOLUME_STEP}
                value={volumeCc}
                onChange={(e) => setVolumeCc(Number(e.target.value))}
              />
              <div className="mt-1 flex justify-between text-[11px] text-ink-500">
                <span>{VOLUME_MIN} cc</span>
                <span>{VOLUME_MAX} cc</span>
              </div>
              <p className="mt-2 text-xs text-ink-400">{describeVolume(volumeCc)}</p>
            </div>
          </div>

          <StepLabel n={3} title="Generate" className="mt-8" />
          <div className="rounded-2xl border border-cream-200 bg-white p-5 shadow-sm">
            <label className="flex cursor-pointer items-start gap-3 text-xs leading-relaxed text-ink-500">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                className="mt-0.5 size-4 shrink-0 accent-blush-600"
              />
              <span>
                I am 18 or older, the photo is of me (or of someone who gave me permission),
                and I understand this preview is an AI illustration for education — not a
                medical prediction or guarantee.
              </span>
            </label>
            <button
              type="button"
              onClick={generate}
              disabled={!canGenerate}
              className="mt-4 w-full rounded-full bg-blush-600 px-5 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-blush-700 disabled:cursor-not-allowed disabled:bg-cream-300 disabled:text-ink-500"
            >
              {generating
                ? "Generating…"
                : result
                  ? "Regenerate with these options"
                  : "Generate my preview"}
            </button>
            {!photo && (
              <p className="mt-2 text-center text-xs text-ink-500">Upload a photo first.</p>
            )}
            {photo && !consent && (
              <p className="mt-2 text-center text-xs text-ink-500">
                Tick the checkbox above to enable generation.
              </p>
            )}
            {error && (
              <p role="alert" className="mt-3 rounded-xl bg-blush-50 px-4 py-3 text-sm text-blush-800">
                {error}
              </p>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

function StepLabel({ n, title, className }: { n: number; title: string; className?: string }) {
  return (
    <div className={`mb-3 flex items-center gap-2.5 ${className ?? ""}`}>
      <span className="flex size-6 items-center justify-center rounded-full bg-ink-950 text-xs font-semibold text-cream-50">
        {n}
      </span>
      <h2 className="text-sm font-semibold tracking-wide text-ink-900 uppercase">{title}</h2>
    </div>
  );
}
