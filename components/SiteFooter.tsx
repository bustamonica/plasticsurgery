import Link from "next/link";
import { SITE_NAME } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer className="border-t border-cream-200 bg-cream-100/60">
      <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="font-display text-xl text-ink-950">
              {SITE_NAME}
              <span className="text-blush-500">.</span>
            </p>
            <p className="mt-1 max-w-md text-sm text-ink-500">
              An AI visualization studio for breast augmentation — explore brand,
              shape and volume options before your consultation.
            </p>
          </div>
          <nav className="flex gap-6 text-sm text-ink-700">
            <Link href="/studio" className="hover:text-ink-950">
              Studio
            </Link>
            <Link href="/learn" className="hover:text-ink-950">
              Implant guide
            </Link>
            <Link href="/privacy" className="hover:text-ink-950">
              Privacy
            </Link>
          </nav>
        </div>
        <p className="mt-8 border-t border-cream-200 pt-6 text-xs leading-relaxed text-ink-400">
          <strong className="text-ink-500">Medical disclaimer:</strong> {SITE_NAME} produces
          AI-generated illustrations for educational purposes only. They are not a prediction,
          promise or guarantee of any surgical outcome, and nothing on this site is medical
          advice. Implant availability, sizing and suitability vary by person and by country —
          always consult a board-certified plastic surgeon. In the U.S., the FDA has approved
          saline implants for augmentation at age 18+ and silicone implants at age 22+.
        </p>
        <p className="mt-3 text-xs text-ink-400">
          © {new Date().getFullYear()} {SITE_NAME}. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
