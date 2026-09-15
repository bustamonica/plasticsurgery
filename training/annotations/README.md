# Visual-annotation files

One JSON per clinic whose gallery does not document its view labels, in the
format `scrape_gallery.py --annotations` expects (see that module's docstring).
Keys are `<clinic>:<case-id>`; a pair's view is recorded under
`pairs.<pair-key>.view` using the `dataset_schema.json` vocabulary.

These files hold LABELS, never image data, so they belong in the repo even
though `training/data/` does not: they are the only record of how a view call
was made, and without them a corpus rebuild cannot reproduce the emitted pairs.

An entry is an ENUMERATION of the cases actually reviewed, not a rule.
A case added to a gallery later gets no view until someone looks at it - which
is the intended behaviour, since a view is a visual call and the alternative is
inventing one.

## bayside.json

76 cases (the 78 enumerated less the two excluded as combined procedures),
226 pairs. Bayside publishes each case as up to three `<li>` slots in one
carousel, and the slot order is a fixed photography protocol:

| slot | view |
| --- | --- |
| `img1-2` | `front` |
| `img3-4` | `oblique-right` |
| `img5-6` | `oblique-left` |

Do NOT take this from the page's `profile_view` / `frontal_view` /
`oblique_view` HTML comments. That four-comment sequence is byte-identical on
all 78 case pages including the two that publish only two slots, so it is
template boilerplate, and it disagrees with the photographs: slot 1 is a front
view on every case, not a profile.

The mapping was established by opening every case's photographs, and the
laterality is anchored on landmarks rather than on the near-arm geometry alone
(CLAUDE.md: blind left/right calls from contact sheets score about 5/9). Seven
cases carry a mark visible in both the front view - which fixes the absolute
frame, since in a front view the patient's left is on the viewer's right - and
the obliques:

- `bam-case-21-age-30` butterfly tattoo, patient's RIGHT chest: near side in
  `img3-4`, far side in `img5-6`.
- `breast-augmentation-case-44-age-39` heart tattoo, patient's LEFT chest:
  far side in `img3-4`, near side in `img5-6`.
- `breast-augmentation-case-33-age-24` flower tattoo, patient's LEFT abdomen:
  near side in `img5-6`.
- `bam-case-13-age-24` torso tattoo, and the under-breast script tattoos on
  `breast-augmentation-case-17-age-21`, `-24-age-27` and `-26-age-20`, all on
  the patient's RIGHT: visible in `img3-4`, hidden in `img5-6`.

Landmarks on BOTH sides agree, which is what rules out a systematic mirror
error. The convention itself is the corpus one: `oblique-left` means the
patient's LEFT side faces the camera, putting the near shoulder on the
VIEWER'S RIGHT.

## mwps.json

10 cases, 19 pairs - every emitted Mountain West pair, since `scrape_gallery.py` skips any pair with no view.
The other ~95 published composites are deliberately unannotated: they are excluded by the 400px floor after the watermark crop, and annotating a pair that cannot be emitted would be guessing for nothing.

The view is recorded per SLIDE because the slide order is not a protocol here - case 10 publishes oblique-right, front, side-right while case 11 publishes front, side-right - so there is no table to read this file against.

Every non-front call was made at full annotation size, against the corpus convention (`-left` = near shoulder/arm on the VIEWER'S RIGHT, body angled toward the viewer's left), read off the visible chin/shoulder direction.
That size matters: a first pass from 6-per-sheet contact sheets called case 14's oblique `left`, and re-rendered at full size it is unambiguously `oblique-right`.
Every call was redone at the larger size.

## lakeshore.json

The 60 cases already in the corpus as fronts, and all 186 pairs they publish: 60 fronts, 45 obliques/sides labelled, 58 held, 23 withheld (captain-approved intra-case collection, 2026-09-13).
Every pair carries its disposition in the file itself, so there is no table to read it against.

- **Fronts are keyed by pixel match, not by the shared `clinic-corpus/annotations.json`.**
  That file labels `pair2` as front for all 60 cases, but under today's `influx_swiper` parser the emitted front is `pair1` in 53 cases, `pair2` in 5 and `pair3` in 2.
  Replaying the shared file would label obliques as fronts.
  That provenance is recorded in each front's `key_source`, which the loader ignores, so `evidence` keeps its landmark-only meaning.
- **A labelled lateral needs a landmark in the case's front AND in both halves of the pair** (a mark present, or a large mark absent from a region the view clearly shows).
  Its `evidence` sentence names the landmark and reaches the pair's `meta.json` notes.
  Every call was made twice, independently (one full pass plus a blind second pass), and a pair emits only where both agree on each half's view and on the facing.
- **5 of the 45 labelled laterals were stopped by a censorship gate, so 40 are in the corpus.**
  Each of the 5 carries an `ingest_stopped` marker, which the loader ignores: the gate read a texture-free patch of skin, a false positive that was deliberately not overridden, pending the captain's censorship-false-positive-readmit decision.
- **Held pairs** carry the bare view type (`oblique`/`side`) plus `pose_laterality`, the direction both passes read from the pose.
  They are held because no landmark ties both halves to a side, not because the pose is unclear; adding `laterality` releases one.
  Case 52's `pair2` is the exception that needs more than a label: it also carries an `identity_check`, because its BEFORE is pixel-identical to case 45 `pair2`'s BEFORE.
  Settle whether cases 45 and 52 are the same patient before releasing it, because both fronts are already in the corpus and `build_dataset.py` splits train/val by patient.
- **Withheld pairs** carry a `withheld` reason and no view.
  The reasons are ones no label can fix: halves of different views, a duplicate patient (case 43 republishes case 12), or photos that belong to another case.
  This gallery pairs positionally, and cases 45 and 48 publish before-oblique, before-side, after-oblique, after-side, so it pairs two pre-op or two post-op shots.
  A second shoot on a beige wall is appended to cases 55, 57 and 94, and at 94 it is visibly a different woman.

## brisbane.json

All 10 cases, 20 pairs: `ac` (photos a and c) is `front` and `bd` (photos b and d) is `oblique-right` on every case.
The gallery publishes no view labels; the filename letter is a position in a fixed four-photo protocol, confirmed by opening every photograph.
The oblique's laterality is anchored on case 1, whose right-flank tattoo sits on the near side, and every `bd` pair carries that evidence sentence.
Cases 1, 8 and 10 are annotated but emit nothing (no readable implant volume), and case 12 is held at ingest by the mosaic gate pending release.

## sarasota.json

170 cases, 536 pairs: every pure, volume-bearing case whose halves clear the 400px floor after the watermark crop, and every pair each of those cases publishes.
Every pair carries its disposition in the file itself: 386 carry a view (120 `front`, 88 `oblique-left`, 93 `side-left`, 38 `oblique-right`, 47 `side-right`) and 150 carry a `withheld` reason and no view (149 for the crop below, and 11503 `OtL7LhHPLvm5`, whose mosaic survives the crop on the before half only).

- **Laterality is read from pose** (2026-08-26 ruling, corpus-wide): the chest facing the viewer's right, far breast edge-on at the frame's right edge, is `-right`.
  Each lateral's `evidence` sentence says so; none rests on a landmark, and a pair whose pose was unreadable would have been left unannotated (none was).
  Page order is not trusted: case 14424 publishes its front where a band sheet made it look like an oblique, and only the full-frame render settled it.
- **Withheld pairs are the crop's cost, not a view problem.**
  Every composite loses its bottom 0.25 of height to remove the corner logo (`REV_BOTTOM_FRAC` in `bragbook_rev.py`), and on tightly framed shots that line runs through the lower breast pole.
  Under decision `sarasota-corner-block-crop` (A) a pair is withheld when the line crosses or touches breast tissue in EITHER half, judged at 1.5-2x zoom on a strip around the line; a pair whose fold clears the line by a few pixels was kept.
  The withheld reason names the decision, and the pair id list is the collection report's.
