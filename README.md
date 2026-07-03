# Aurelle — AI Breast Augmentation Preview Studio

A website for people considering breast augmentation: upload a photo, choose
implant **brand** (Mentor, Natrelle, Motiva, Sientra), **shape** (round /
teardrop), **profile** and **volume** (150–800 cc), and an AI generates a
realistic before & after preview with a comparison slider.

Built with Next.js 15 (App Router), React 19, TypeScript and Tailwind CSS 4.

## Pages

| Route | What it does |
| --- | --- |
| `/` | Landing page — how it works, options overview, trust & disclaimers |
| `/studio` | The preview studio: photo upload → implant configurator → AI before/after |
| `/learn` | Plain-English implant guide (brands, shapes, profiles, volume table, safety) |
| `/privacy` | Photo handling, age requirements, medical disclaimers |
| `POST /api/generate` | Server route that calls the AI image model |

## Getting started

```bash
npm install
cp .env.example .env.local   # then add your key (see below)
npm run dev                  # http://localhost:3000
```

Without an API key the app runs in **demo mode**: the full flow works, but the
"after" image is just your original photo with a demo notice.

## Enabling real AI previews

1. Get a Google AI Studio key: https://aistudio.google.com/apikey
2. Put it in `.env.local`:

   ```
   GEMINI_API_KEY=your-key-here
   ```

3. Restart the dev server. The studio now calls `gemini-2.5-flash-image`
   (Google's image-editing model) to render the augmentation preview.

The provider integration lives in `app/api/generate/route.ts` and the prompt
in `lib/prompt.ts` — both are deliberately small so you can swap in another
provider (Replicate/FLUX, OpenAI, a custom model) later.

## Where things live

- `lib/implants.ts` — the implant catalog (brands, shapes, profiles, volume
  bounds) and all marketing copy for the options. Edit this to change what the
  configurator offers.
- `lib/site.ts` — site name & tagline (currently the placeholder "Aurelle").
- `lib/prompt.ts` — how configurator choices become the AI edit instruction.
- `components/BeforeAfterSlider.tsx` — the draggable comparison slider.
- `components/PhotoUpload.tsx` — drag & drop upload with client-side downscaling.

## Privacy posture

Photos are sent only when the user presses Generate, are processed in memory,
and are never written to disk or a database. The generated preview lives only
in the browser session. Review `app/privacy/page.tsx` with counsel before
launch — especially if the site will be operated by a medical practice
(HIPAA/marketing rules may apply).

## Important

All previews are AI illustrations for education — not medical predictions.
The implant copy in `lib/implants.ts` is general and should be reviewed by a
board-certified plastic surgeon before the site goes live.
