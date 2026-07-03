import type { Metadata } from "next";
import Link from "next/link";
import { BRANDS, PROFILES, SHAPES } from "@/lib/implants";

export const metadata: Metadata = {
  title: "Implant guide",
  description:
    "A plain-English guide to breast implant brands, shapes, profiles and volumes — so you know your options before a consultation.",
};

const VOLUME_GUIDE = [
  { range: "150–250 cc", effect: "Subtle enhancement — roughly half to one cup size." },
  { range: "250–400 cc", effect: "The most popular range — roughly one to one and a half cup sizes, natural-looking on most frames." },
  { range: "400–550 cc", effect: "Clearly noticeable — roughly one and a half to two cup sizes." },
  { range: "550–700 cc", effect: "Full — roughly two to two and a half cup sizes." },
  { range: "700–800 cc", effect: "Dramatic — roughly two and a half cup sizes or more; best discussed carefully with your surgeon." },
];

export default function LearnPage() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-12 sm:px-6">
      <h1 className="font-display text-4xl tracking-tight text-ink-950 sm:text-5xl">
        The implant guide
      </h1>
      <p className="mt-4 max-w-2xl text-lg leading-relaxed text-ink-500">
        Everything the studio lets you configure — brands, shapes, profiles and volume —
        explained in plain English. Bring your favorite preview to a consultation and use
        this vocabulary to talk through it.
      </p>

      {/* Brands */}
      <section className="mt-14">
        <h2 className="font-display text-3xl tracking-tight text-ink-950">Brands</h2>
        <p className="mt-2 text-sm text-ink-500">
          All four are FDA-approved implant manufacturers. Availability varies by clinic and
          country; your surgeon usually works primarily with one or two.
        </p>
        <div className="mt-6 space-y-4">
          {BRANDS.map((b) => (
            <div key={b.id} className="rounded-2xl border border-cream-200 bg-white p-6">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-xl font-semibold text-ink-950">{b.name}</h3>
                <span className="text-sm text-ink-400">{b.manufacturer}</span>
              </div>
              <p className="mt-1 text-sm font-medium text-blush-700">{b.fill}</p>
              <p className="mt-3 leading-relaxed text-ink-700">{b.blurb}</p>
              <ul className="mt-4 flex flex-wrap gap-1.5">
                {b.highlights.map((h) => (
                  <li key={h} className="rounded-full bg-cream-100 px-3 py-1 text-xs font-medium text-ink-700">
                    {h}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      {/* Shapes */}
      <section className="mt-14">
        <h2 className="font-display text-3xl tracking-tight text-ink-950">Round vs. teardrop</h2>
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          {SHAPES.map((s) => (
            <div key={s.id} className="rounded-2xl border border-blush-100 bg-blush-50/60 p-6">
              <h3 className="text-lg font-semibold text-ink-950">{s.name}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-700">{s.blurb}</p>
            </div>
          ))}
        </div>
        <p className="mt-4 text-sm leading-relaxed text-ink-500">
          Round implants are by far the most commonly chosen today — modern soft gels settle
          into a fairly natural slope when you&apos;re upright anyway. Teardrop (anatomical)
          implants are usually considered for reconstruction or very thin tissue coverage,
          and they require a textured or specialized surface to avoid rotating.
        </p>
      </section>

      {/* Profiles */}
      <section className="mt-14">
        <h2 className="font-display text-3xl tracking-tight text-ink-950">Profiles</h2>
        <p className="mt-2 text-sm text-ink-500">
          Profile describes how far an implant projects forward relative to how wide its base
          is. Same volume, different silhouette.
        </p>
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          {PROFILES.map((p) => (
            <div key={p.id} className="rounded-2xl border border-cream-200 bg-white p-6">
              <h3 className="text-lg font-semibold text-ink-950">{p.name}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-700">{p.blurb}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Volume */}
      <section className="mt-14">
        <h2 className="font-display text-3xl tracking-tight text-ink-950">Volume</h2>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-ink-500">
          Implant size is measured in cubic centimeters (cc), not cup sizes. As a rough rule
          of thumb, 150–200 cc adds about one cup size — but the same implant looks very
          different on different chest widths and existing breast tissue.
        </p>
        <div className="mt-6 overflow-x-auto rounded-2xl border border-cream-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-cream-200 text-xs tracking-wide text-ink-400 uppercase">
                <th className="px-6 py-4 font-semibold">Volume</th>
                <th className="px-6 py-4 font-semibold">Typical effect</th>
              </tr>
            </thead>
            <tbody>
              {VOLUME_GUIDE.map((row) => (
                <tr key={row.range} className="border-b border-cream-100 last:border-0">
                  <td className="px-6 py-4 font-medium whitespace-nowrap text-blush-700">
                    {row.range}
                  </td>
                  <td className="px-6 py-4 leading-relaxed text-ink-700">{row.effect}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Safety */}
      <section className="mt-14 rounded-2xl bg-ink-950 p-8 text-cream-50">
        <h2 className="font-display text-2xl tracking-tight">Good to know</h2>
        <ul className="mt-4 space-y-3 text-sm leading-relaxed text-cream-200/90">
          <li>
            • In the U.S., the FDA approves saline implants for augmentation from age 18 and
            silicone gel implants from age 22.
          </li>
          <li>
            • Implants are not lifetime devices — many people have a revision or replacement
            at some point, often after 10–20 years.
          </li>
          <li>
            • The FDA recommends periodic imaging (MRI or ultrasound) for silicone implants
            to screen for silent rupture.
          </li>
          <li>
            • Choose a board-certified plastic surgeon and ask to see their before &amp;
            after photos of patients with a frame similar to yours.
          </li>
        </ul>
        <p className="mt-6 text-xs text-cream-200/60">
          This page is general education, not medical advice. Product details change —
          confirm specifics with your surgeon and the manufacturers&apos; official patient
          information.
        </p>
      </section>

      <div className="mt-12 text-center">
        <Link
          href="/studio"
          className="inline-block rounded-full bg-blush-600 px-7 py-3.5 text-sm font-semibold text-white shadow-md transition hover:bg-blush-700"
        >
          Try these options on your photo
        </Link>
      </div>
    </div>
  );
}
