"use client";

import { useState } from "react";

interface BeforeAfterSliderProps {
  beforeSrc: string;
  afterSrc: string;
  className?: string;
}

/**
 * Draggable before/after comparison. A full-size transparent range input
 * drives the divider, which makes the slider keyboard- and screen-reader-
 * accessible for free (its focus is surfaced on the container via CSS).
 *
 * The user's original photo is the in-flow image so it defines the layout
 * box at its true aspect ratio; the AI "after" image is overlaid with
 * object-cover, so a small aspect-ratio drift in the model output crops the
 * generated image slightly instead of distorting the user's real photo.
 */
export function BeforeAfterSlider({ beforeSrc, afterSrc, className }: BeforeAfterSliderProps) {
  const [percent, setPercent] = useState(50);

  return (
    <div
      className={`ba-slider relative select-none overflow-hidden rounded-2xl bg-ink-950 ${className ?? ""}`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={beforeSrc} alt="Before photo" className="block w-full" draggable={false} />
      <div
        className="absolute inset-0 overflow-hidden"
        style={{ clipPath: `inset(0 0 0 ${percent}%)` }}
        aria-hidden
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={afterSrc}
          alt=""
          className="block h-full w-full object-cover"
          draggable={false}
        />
      </div>

      {/* Divider + handle */}
      <div
        className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-white/90 shadow-[0_0_12px_rgba(0,0,0,0.4)]"
        style={{ left: `${percent}%` }}
        aria-hidden
      >
        <div className="absolute top-1/2 left-1/2 flex size-9 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white text-ink-900 shadow-lg">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
            <path d="M4.5 2.5 1 7l3.5 4.5M9.5 2.5 13 7l-3.5 4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </div>

      <span className="pointer-events-none absolute top-3 left-3 rounded-full bg-ink-950/70 px-2.5 py-1 text-xs font-medium text-white backdrop-blur">
        Before
      </span>
      <span className="pointer-events-none absolute top-3 right-3 rounded-full bg-blush-600/85 px-2.5 py-1 text-xs font-medium text-white backdrop-blur">
        After
      </span>

      <input
        type="range"
        min={0}
        max={100}
        step={0.5}
        value={percent}
        onChange={(e) => setPercent(Number(e.target.value))}
        aria-label="Reveal before and after comparison"
        className="absolute inset-0 h-full w-full cursor-ew-resize opacity-0"
      />
    </div>
  );
}
