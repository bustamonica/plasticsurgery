import Link from "next/link";
import { SITE_NAME } from "@/lib/site";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-cream-200/80 bg-cream-50/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="font-display text-2xl tracking-tight text-ink-950">
          {SITE_NAME}
          <span className="text-blush-500">.</span>
        </Link>
        <nav className="flex items-center gap-1 sm:gap-2">
          <Link
            href="/learn"
            className="rounded-full px-3 py-2 text-sm font-medium text-ink-700 transition hover:bg-cream-100 hover:text-ink-950"
          >
            Implant guide
          </Link>
          <Link
            href="/privacy"
            className="hidden rounded-full px-3 py-2 text-sm font-medium text-ink-700 transition hover:bg-cream-100 hover:text-ink-950 sm:block"
          >
            Privacy
          </Link>
          <Link
            href="/studio"
            className="ml-2 rounded-full bg-ink-950 px-4 py-2 text-sm font-medium text-cream-50 shadow-sm transition hover:bg-blush-700"
          >
            Open the studio
          </Link>
        </nav>
      </div>
    </header>
  );
}
