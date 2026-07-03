"use client";

import { useCallback, useRef, useState } from "react";

export interface UploadedPhoto {
  /** data: URL for display. */
  dataUrl: string;
  /** Raw base64 (no prefix) for the API. */
  base64: string;
  mimeType: string;
  width: number;
  height: number;
}

interface PhotoUploadProps {
  onPhoto: (photo: UploadedPhoto) => void;
}

const MAX_DIMENSION = 1536;
const ACCEPTED = ["image/jpeg", "image/png", "image/webp"];

/** Downscale large photos client-side so uploads stay fast and under limits. */
async function processFile(file: File): Promise<UploadedPhoto> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_DIMENSION / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas unavailable");
  // JPEG has no alpha channel — without this, transparent PNG/WebP areas
  // would composite to solid black.
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();

  const dataUrl = canvas.toDataURL("image/jpeg", 0.92);
  return {
    dataUrl,
    base64: dataUrl.split(",")[1],
    mimeType: "image/jpeg",
    width,
    height,
  };
}

export function PhotoUpload({ onPhoto }: PhotoUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleFile = useCallback(
    async (file: File | undefined | null) => {
      setError(null);
      if (!file) return;
      if (!ACCEPTED.includes(file.type)) {
        setError("Please upload a JPEG, PNG or WebP photo.");
        return;
      }
      if (file.size > 20 * 1024 * 1024) {
        setError("That photo is over 20 MB. Please choose a smaller one.");
        return;
      }
      setBusy(true);
      try {
        onPhoto(await processFile(file));
      } catch {
        setError("Could not read that photo. Please try a different file.");
      } finally {
        setBusy(false);
      }
    },
    [onPhoto],
  );

  return (
    <div>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          handleFile(e.dataTransfer.files?.[0]);
        }}
        className={`flex w-full flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-14 text-center transition ${
          dragOver
            ? "border-blush-400 bg-blush-50"
            : "border-cream-300 bg-white hover:border-blush-300 hover:bg-blush-50/40"
        }`}
      >
        <span className="flex size-12 items-center justify-center rounded-full bg-blush-100 text-blush-600">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M12 16V4m0 0 4 4m-4-4L8 8M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="text-sm font-medium text-ink-900">
          {busy ? "Preparing your photo…" : "Drop a photo here, or click to browse"}
        </span>
        <span className="max-w-xs text-xs leading-relaxed text-ink-400">
          Best results: a front-facing, well-lit photo from the waist up, wearing a fitted top
          or sports bra. Photos are never stored on our servers — see Privacy for how the AI
          provider handles them.
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED.join(",")}
        className="hidden"
        onChange={(e) => {
          handleFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {error && (
        <p role="alert" className="mt-2 text-sm text-blush-700">
          {error}
        </p>
      )}
    </div>
  );
}
