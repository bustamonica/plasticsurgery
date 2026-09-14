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
- **A labelled lateral needs a landmark in the case's front AND in both halves of the pair** (a mark present, or a large mark absent from a region the view clearly shows).
  Its `evidence` sentence names the landmark and reaches the pair's `meta.json` notes.
  Every call was made twice, independently (one full pass plus a blind second pass), and a pair emits only where both agree on each half's view and on the facing.
- **Held pairs** carry the bare view type (`oblique`/`side`) plus `pose_laterality`, the direction both passes read from the pose.
  They are held because no landmark ties both halves to a side, not because the pose is unclear; adding `laterality` releases one.
- **Withheld pairs** carry a `withheld` reason and no view.
  The reasons are ones no label can fix: halves of different views, a duplicate patient (case 43 republishes case 12), or photos that belong to another case.
  This gallery pairs positionally, and cases 45 and 48 publish before-oblique, before-side, after-oblique, after-side, so it pairs two pre-op or two post-op shots.
  A second shoot on a beige wall is appended to cases 55, 57 and 94, and at 94 it is visibly a different woman.
