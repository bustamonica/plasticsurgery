# Clinic watermarks

What each consented clinic burns into its published photos, and whether it
touches the anatomy the model is being trained on.

This table exists because the same measurement kept being re-derived in one
scout report after another and then lost: drdanielbarrett carries a wordmark on
every image it publishes and appeared in no watermark table at all until this
file. Per-clinic reports may hold more detail; this is the roll-up that travels
with the code.

## The rule these rows are read against

**Captain ruling, 2026-08-15: a watermark that stays clear of breast tissue is
acceptable and needs no mask.** A watermark drawn *across* the breasts is the
one artifact this corpus most needs to keep out of training - the v1 LoRA
already learned to reproduce a clinic's overlay once - and masking it removes
the primary training signal, so the ruling is to exclude those clinics rather
than repair them.
**Captain ruling, 2026-08-19: a mark that appears on only ONE half of a pair is
cropped, not tolerated.** Clear of the breasts or not, a mark carried by every
after image and no before image correlates perfectly with the training label, so
`ClinicConfig.bottom_crop_px` (`scripts/scrape_gallery.py`) trims both halves
equally when the scraper writes the pair. The crop is measured per clinic and
never transferred between them; the full lesson, the detection recipe and the
measured cost are `.claude/skills/clinic-prospecting/SKILL.md` Screen 4a.

A verdict below records those rulings; whether it is actually enforced on disk is
a separate question, answered in "Excluded on paper vs excluded in fact".

`censorship.py` deliberately does not enforce this. It is anchored on a skin
silhouette, so a mark burned into the backdrop is kept by design; the on-body
call is a human one and lives here.

## Measured marks

Measured by averaging the absolute high-pass response over all same-size images
for the clinic, which resolves any fixed overlay while bodies average away, then
confirming on representative full-size images.

| clinic | mark | where | on body? | verdict |
| --- | --- | --- | --- | --- |
| **drdanielbarrett** | "BARRETT PLASTIC SURGERY" wordmark + figure logo, translucent | across the **lower abdomen**, every image | **yes**, off-breast | acceptable - emitted 2026-08-15 |
| **drkolker** | "ADAM R. KOLKER, MD" | bottom of frame, over the waistband/jeans | off-body relative to the anatomy | acceptable - emitted 2026-08-15 |
| **sanantonio** | none | - | - | clean; 200-image average shows no overlay |
| heavenly | script "Heavenly / PLASTIC SURGERY" lockup, diagonal | y 0.21-0.68 of frame - **over the breasts** | **yes, over breast tissue** | **retired, all 119 pairs - enforced 2026-08-15/16.** Captain ruling: "For the uncleaned heavenly data, let's just discard it and not use it for training." Moved out of the finished tree into `~/firstmate/data/ba-viz-emit-backlog/quarantine/retired-watermark/heavenly/`, registered in `training/retired_pairs.json` (`retired_watermark`). Three clean-up passes each overstated their own success; see `~/firstmate/data/report/README.md` |
| wny | "WNY PLASTIC SURGERY" | lower torso, y 0.91-0.98, **after image only** | **yes**, off-breast | **DECIDED 2026-08-19: cropped, not tolerated.** The mark is clear of the breasts but sits on the after image alone, which correlates it perfectly with the training label. `ClinicConfig.bottom_crop_px=60` (mark top measured 51px from the bottom) trims both halves when the scraper writes the pair; before/after high-pass asymmetry 11.5 -> 1.30. 9 of 10 pairs re-emitted cropped; the crop costs 0 pairs to the 400px floor (every half is 508px on its short side after it), and `wny-19-front` is excluded for carrying no `volume_cc`, which is a spec gate and nothing to do with the crop |
| **tccs** | colour-wheel logo + "THE CENTER FOR COSMETIC SURGERY" wordmark | bottom-left of the composite, so the **before image only** (743 of 743 pairs) | no - lower frame, below the breasts | **cropped 2026-08-19**, `bottom_crop_px=130` (mark top 122px from the bottom at the median, 124px at p95); 9 pairs then fall below the 400px floor and are excluded |
| **roth** | "Jeffrey J. Roth, M.D., F.A.C.S." script wordmark | bottom-right, **after image only** (162 pairs) - 16.9x asymmetry, the strongest measured | no - lower frame, below the breasts | **cropped 2026-08-19**, `bottom_crop_px=175` (mark top 162px from the bottom); costs 0 pairs to the 400px floor |
| **camp** | "STEVEN CAMP MD PLASTIC SURGERY" | bottom-right, **after image only** (299 pairs) - 7.4x asymmetry | no - lower frame, below the breasts | **cropped 2026-08-19**, `bottom_crop_px=110` (mark top 98px from the bottom on the tall height groups, 81px on the 478px group); 53 pairs then fall below the 400px floor and are excluded |
| harrington | faint caption | very bottom of frame, below the subject | no | acceptable |
| mitchellbrown | "Photos courtesy of Dr. Mitchell Brown, TorontoPlasticSurgery.com" | white margin below the subject | no | acceptable |
| drrohrich | "© Rod J. Rohrich MD - http://drrohrich.com" (28 of 45) | white margin between panels | no | not the blocker (resolution is) |
| drteitelbaum | burnt-in "Before"/"After" | top-left, on the backdrop | no | not the blocker (no volume, resolution) |
| skplastic | translucent "SK" box; black "before"/"after" bar | lower-right corner; left ~10% of each half | no | not the blocker (resolution) |
| privateclinic | "The Private Clinic of Harley Street" | **straddles the composite's split midpoint**, so each half carries a fragment | - | re-check if the clinic ever supplies originals; a fragment on both halves is harder to reason about than a whole mark on one |
| drgrover | "© Sanjay Grover, MD, FACS" + date stamp (4 of 12 sampled) | bottom-right; **grazes the lower abdomen on some** | sometimes | re-check before this clinic is ever emitted |
| allure, austinweston, charlotte, drjeremyhunt, drmiroshnik, lakeshore, marina, mya, sixsurgery | none | - | - | clean |

drtavakoli marks some gallery images with its own name, but none of the pairs in
the corpus carries one.

sixsurgery is blocked on **censorship**, not watermarking: every published photo
has opaque circles over the nipples.

## Excluded on paper vs excluded in fact

A verdict in the table above is a documented *intent*.
Nothing in the pipeline reads this file, so a row that says a clinic is excluded
only keeps its pairs out of training if something on disk keeps them out too.

- **heavenly**: enforced.
  All 119 pairs were moved out of the finished corpus tree
  (`clinic-corpus/heavenly/`, now empty) into
  `~/firstmate/data/ba-viz-emit-backlog/quarantine/retired-watermark/heavenly/`
  on the captain's 2026-08-15 ruling to discard the clinic outright. They carry
  a `training/retired_pairs.json` entry (`retired_watermark`) that blocks
  re-emission if heavenly is ever re-staged.
  A first verification pass checked only the finished-tree shape
  (`clinic-corpus/<clinic>/<pair-id>/`) and missed that two pre-existing,
  non-canonical duplicate-ingest dumps at the corpus **root** -
  `~/firstmate/data/clinic-corpus/_staging/` and
  `~/firstmate/data/clinic-corpus-staging/`, dated 2026-08-12, documented in
  `~/firstmate/data/report/README.md` since 2026-08-15 as "duplicate partial
  ingest runs... counted nowhere in this report" - each still held 63
  `heavenly-*` pair directories, a pre-inpaint snapshot of 63 of the 119. No
  training script reads either path as an input, so they were never a route
  back into a dataset build, but they were exactly the shape of risk this
  retirement exists to close: present on disk, invisible to the count everyone
  trusts. Both were cleared on 2026-08-16, moved intact into
  `~/firstmate/data/ba-viz-emit-backlog/quarantine/retired-watermark-staging-dup/heavenly/`
  and `.../retired-watermark-corpus-staging-dup/heavenly/` respectively (see
  the quarantine tree's `MANIFEST.md`).
  Both new buckets are listed in `emit_corpus.py`'s `QUARANTINE_DIRS` so they
  read as archives of the `retired_watermark` ruling rather than as holds of
  their own - otherwise deleting heavenly from `retired_pairs.json` would free
  only 56 of the 119, with the other 63 still pinned by a tree copy.
  **Verified now, and named precisely: zero `heavenly-*` directories in
  `clinic-corpus/heavenly/`, `clinic-corpus/_staging/`, or
  `clinic-corpus-staging/`** - the finished tree plus the two duplicate-ingest
  dumps this retirement swept, which is every corpus-tree location - and zero
  heavenly pairs reach `build_dataset.py`'s output when run over the whole
  corpus.
  That claim is about the corpus trees and stops there. Outside them, 212
  `heavenly-*` pair directories deliberately remain as the working record of
  the clean-up attempts: 106 in `~/firstmate/data/ba-viz-heavenly-inpaint/staging/`
  (the discredited "99.4% removed" inpaint run) and 53 each in
  `~/firstmate/data/ba-viz-heavenly-inpaint-rerun/staging2/` and
  `.../work/out/` (the corrected re-clean that was never run and, per the
  retirement brief, deliberately **not promoted** - kept only as evidence,
  pending any future captain decision to reopen heavenly).
  They are a preserved decision, not a gap, and
  `~/firstmate/data/report/heavenly/NO-PAIRS.md` scopes them the same way.
  One more heavenly directory sits *inside* `clinic-corpus/` and is also not a
  gap: `clinic-corpus/.scraper-cache/images/heavenly/` holds the 63 cached
  source downloads, which is scraper intake rather than a pair tree - it holds
  no `heavenly-*` pair directory, and raw intake is never deleted (see
  `README.md`'s "retired, not deleted").
  The remaining trees are also the reason the registry entry is not
  redundant: `emit_corpus.py`
  takes the staging tree as a positional argument, so pointing it at one of
  those trees is an ordinary invocation, and `retired_pairs.json` is what
  refuses it.
- **wny**: resolved 2026-08-19. The `wny-after-only-watermark` question is
  closed - the captain ruled that a corner or edge-band mark is cropped rather
  than tolerated, so the 9 viable pairs are live in the finished tree *cropped*.
  The crop costs this clinic 0 pairs: measured after it, every half is 508px on
  its short side, nowhere near the 400px floor. `wny-19-front` is excluded for
  carrying no `volume_cc` - `ingest.py`'s `validate_meta` rejects it on that
  ground, not on size. This is enforced in code (`bottom_crop_px`), not just
  documented - as it is for tccs, roth and camp, the other three clinics the
  2026-08-19 one-sided-mark measurement caught.
- **sixsurgery**: withheld *in fact*.
  Nothing was ever emitted there - 0 pairs in the finished tree - so its
  withholding is already true as a matter of fact rather than of prose.
  That is what an enforced exclusion looks like.

There is a sharp edge to know before assuming a registry edit settles one of
these: **a `retired_pairs.json` entry gates the EMIT PATH only.**
It stops `emit_corpus.py` carrying a pair from `staging/` into the finished
tree, and does nothing about a pair that is already there.
Removing an already-emitted pair takes a change to the corpus tree itself.

## Maintaining this file

Add a row when a clinic is measured, not when one is guessed at. Say where the
mark sits in the frame and whether it touches breast tissue - that is the only
part of the observation the emit decision turns on.

Keep a verdict honest about enforcement.
If a clinic is ruled out but its pairs are still in the finished tree, say so in
the row and in the section above: a reader trusts this table and would otherwise
conclude those pairs cannot reach training when nothing stops them.
