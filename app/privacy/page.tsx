import type { Metadata } from "next";
import { SITE_NAME } from "@/lib/site";

export const metadata: Metadata = {
  title: "Privacy & disclaimers",
  description: "How photos are handled, and what the AI previews do and do not mean.",
};

export default function PrivacyPage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-12 sm:px-6">
      <h1 className="font-display text-4xl tracking-tight text-ink-950 sm:text-5xl">
        Privacy &amp; disclaimers
      </h1>

      <div className="mt-10 space-y-10 leading-relaxed text-ink-700">
        <section>
          <h2 className="font-display text-2xl text-ink-950">Your photos</h2>
          <ul className="mt-3 list-disc space-y-2 pl-5 text-sm">
            <li>
              Photos are uploaded only when you press <em>Generate</em>, are processed in
              memory to create your preview, and are <strong>not stored</strong> on{" "}
              {SITE_NAME} servers.
            </li>
            <li>
              To generate the preview, your photo is sent to our AI image provider (Google
              Gemini) for processing, subject to their API data-handling terms.
            </li>
            <li>
              Generated previews exist only in your browser session. Download anything you
              want to keep — refresh and it&apos;s gone.
            </li>
            <li>No account, no tracking pixels, no photo gallery on our side.</li>
          </ul>
        </section>

        <section>
          <h2 className="font-display text-2xl text-ink-950">Who may use this tool</h2>
          <ul className="mt-3 list-disc space-y-2 pl-5 text-sm">
            <li>You must be 18 or older.</li>
            <li>
              Only upload photos of yourself, or of someone who has explicitly given you
              permission for this exact use.
            </li>
            <li>
              Use tasteful, clothed photos (fitted top or sports bra). The AI provider will
              refuse images containing nudity.
            </li>
          </ul>
        </section>

        <section>
          <h2 className="font-display text-2xl text-ink-950">What the previews mean</h2>
          <p className="mt-3 text-sm">
            Previews are AI-generated illustrations intended to help you explore preferences
            and communicate with a surgeon. They are <strong>not</strong> medical advice, a
            simulation of your tissue, or a prediction, promise or guarantee of any surgical
            outcome. Real results depend on your anatomy, the implant actually selected, the
            surgical technique and how you heal. Decisions about surgery should be made with
            a board-certified plastic surgeon.
          </p>
        </section>

        <section>
          <h2 className="font-display text-2xl text-ink-950">Contact</h2>
          <p className="mt-3 text-sm">
            Questions about privacy or these terms? Contact the site owner. (Clinic contact
            details go here before launch.)
          </p>
        </section>
      </div>
    </div>
  );
}
