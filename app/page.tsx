import Link from "next/link";
import { BRANDS, SHAPES } from "@/lib/implants";
import { SITE_NAME, SITE_TAGLINE } from "@/lib/site";

export default function HomePage() {
  return (
    <div>
      {/* ============ Hero ============ */}
      <section className="relative overflow-hidden">
        <div
          className="pointer-events-none absolute inset-0 opacity-60"
          style={{
            background:
              "radial-gradient(600px 400px at 80% 10%, var(--color-blush-100), transparent), radial-gradient(500px 350px at 10% 90%, var(--color-cream-200), transparent)",
          }}
          aria-hidden
        />
        <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-4 py-16 sm:px-6 sm:py-24 lg:grid-cols-2">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-blush-200 bg-blush-50 px-3 py-1 text-xs font-medium text-blush-800">
              <span className="size-1.5 rounded-full bg-blush-500" aria-hidden />
              AI-powered augmentation preview
            </p>
            <h1 className="mt-5 font-display text-5xl leading-[1.05] tracking-tight text-ink-950 sm:text-6xl">
              {SITE_TAGLINE}
            </h1>
            <p className="mt-5 max-w-lg text-lg leading-relaxed text-ink-500">
              Thinking about breast augmentation? Upload one photo, choose implant brand,
              shape and volume, and {SITE_NAME} shows you a realistic before &amp; after —
              so you walk into your consultation knowing what you want.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-4">
              <Link
                href="/studio"
                className="rounded-full bg-ink-950 px-7 py-3.5 text-sm font-semibold text-cream-50 shadow-md transition hover:bg-blush-700"
              >
                Try it with your photo
              </Link>
              <Link
                href="/learn"
                className="rounded-full border border-cream-300 bg-white/70 px-7 py-3.5 text-sm font-semibold text-ink-700 transition hover:border-blush-300 hover:text-ink-950"
              >
                Learn about implants
              </Link>
            </div>
            <p className="mt-5 text-xs text-ink-400">
              Free to try · photos never stored · illustrations, not medical predictions
            </p>
          </div>

          {/* Stylized studio mock */}
          <div className="relative mx-auto w-full max-w-md">
            <div className="rounded-3xl border border-cream-200 bg-white p-5 shadow-xl shadow-blush-900/5">
              <div className="relative flex h-64 items-stretch overflow-hidden rounded-2xl bg-gradient-to-br from-cream-100 to-blush-100">
                <div className="flex flex-1 items-center justify-center border-r border-white/70">
                  <Silhouette variant="before" />
                </div>
                <div className="flex flex-1 items-center justify-center">
                  <Silhouette variant="after" />
                </div>
                <span className="absolute top-3 left-3 rounded-full bg-ink-950/70 px-2.5 py-1 text-[11px] font-medium text-white">
                  Before
                </span>
                <span className="absolute top-3 right-3 rounded-full bg-blush-600/90 px-2.5 py-1 text-[11px] font-medium text-white">
                  After
                </span>
                <span className="absolute top-1/2 left-1/2 flex size-8 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white text-ink-900 shadow-lg">
                  <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden>
                    <path d="M4.5 2.5 1 7l3.5 4.5M9.5 2.5 13 7l-3.5 4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
              </div>
              <div className="mt-4 flex flex-wrap gap-1.5">
                {["Motiva", "Teardrop", "375 cc", "Moderate plus"].map((chip) => (
                  <span
                    key={chip}
                    className="rounded-full border border-blush-200 bg-blush-50 px-3 py-1 text-xs font-medium text-blush-800"
                  >
                    {chip}
                  </span>
                ))}
              </div>
              <div className="mt-4">
                <div className="flex justify-between text-[11px] text-ink-400">
                  <span>Volume</span>
                  <span className="font-semibold text-blush-700">375 cc</span>
                </div>
                <div className="mt-1.5 h-1.5 rounded-full bg-cream-200">
                  <div className="h-full w-[42%] rounded-full bg-blush-500" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============ How it works ============ */}
      <section className="border-y border-cream-200 bg-white/60">
        <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
          <h2 className="font-display text-3xl tracking-tight text-ink-950 sm:text-4xl">
            How it works
          </h2>
          <div className="mt-10 grid gap-8 sm:grid-cols-3">
            {[
              {
                n: "01",
                title: "Upload a photo",
                body: "A front-facing photo in a fitted top or sports bra works best. It's processed in memory and never stored.",
              },
              {
                n: "02",
                title: "Choose your options",
                body: "Pick an implant brand, round or teardrop shape, projection profile, and volume from 150 to 800 cc.",
              },
              {
                n: "03",
                title: "See your before & after",
                body: "AI edits only the augmentation onto your photo. Compare with a slider, tweak options, and download the result.",
              },
            ].map((step) => (
              <div key={step.n}>
                <span className="font-display text-4xl text-blush-300">{step.n}</span>
                <h3 className="mt-3 text-lg font-semibold text-ink-950">{step.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-500">{step.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============ Options teaser ============ */}
      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h2 className="font-display text-3xl tracking-tight text-ink-950 sm:text-4xl">
            Every real-world choice, visualized
          </h2>
          <Link href="/learn" className="text-sm font-semibold text-blush-700 hover:text-blush-800">
            Read the full implant guide →
          </Link>
        </div>
        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          {BRANDS.map((brand) => (
            <div
              key={brand.id}
              className="rounded-2xl border border-cream-200 bg-white p-6 shadow-sm transition hover:border-blush-200 hover:shadow-md"
            >
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="text-lg font-semibold text-ink-950">{brand.name}</h3>
                <span className="text-xs text-ink-400">{brand.manufacturer}</span>
              </div>
              <p className="mt-2 text-sm leading-relaxed text-ink-500">{brand.blurb}</p>
              <ul className="mt-4 flex flex-wrap gap-1.5">
                {brand.highlights.map((h) => (
                  <li
                    key={h}
                    className="rounded-full bg-cream-100 px-3 py-1 text-xs font-medium text-ink-700"
                  >
                    {h}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {SHAPES.map((s) => (
            <div key={s.id} className="rounded-2xl border border-blush-100 bg-blush-50/60 p-6">
              <h3 className="text-lg font-semibold text-ink-950">{s.name} shape</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-500">{s.blurb}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ============ Trust ============ */}
      <section className="border-t border-cream-200 bg-ink-950 text-cream-50">
        <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
          <h2 className="font-display text-3xl tracking-tight sm:text-4xl">
            Built for honest decisions
          </h2>
          <div className="mt-10 grid gap-8 sm:grid-cols-3">
            <div>
              <h3 className="font-semibold text-blush-300">Private by design</h3>
              <p className="mt-2 text-sm leading-relaxed text-cream-200/80">
                Your photo is processed in memory to generate the preview and is never saved
                on our servers. No account required.
              </p>
            </div>
            <div>
              <h3 className="font-semibold text-blush-300">An illustration, not a promise</h3>
              <p className="mt-2 text-sm leading-relaxed text-cream-200/80">
                Every preview is an AI-generated visualization for education. Real outcomes
                depend on your anatomy, your surgeon&apos;s plan and healing.
              </p>
            </div>
            <div>
              <h3 className="font-semibold text-blush-300">Consult a professional</h3>
              <p className="mt-2 text-sm leading-relaxed text-cream-200/80">
                Use your favorite previews as a starting point for a conversation with a
                board-certified plastic surgeon — not as a substitute for one.
              </p>
            </div>
          </div>
          <Link
            href="/studio"
            className="mt-12 inline-block rounded-full bg-blush-600 px-7 py-3.5 text-sm font-semibold text-white shadow-md transition hover:bg-blush-500"
          >
            Open the preview studio
          </Link>
        </div>
      </section>
    </div>
  );
}

/** Tasteful abstract figure used in the hero mock — deliberately non-photographic. */
function Silhouette({ variant }: { variant: "before" | "after" }) {
  const chest = variant === "before" ? "M30 74 q10 10 20 0" : "M26 72 q14 20 28 0";
  return (
    <svg width="80" height="150" viewBox="0 0 80 150" fill="none" aria-hidden>
      <circle cx="40" cy="26" r="14" fill="var(--color-blush-300)" opacity="0.65" />
      <path
        d={`M40 44 C 24 48 20 60 22 78 L 20 118 Q 40 130 60 118 L 58 78 C 60 60 56 48 40 44 Z`}
        fill="var(--color-blush-300)"
        opacity="0.65"
      />
      <path d={chest} stroke="var(--color-blush-700)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
    </svg>
  );
}
