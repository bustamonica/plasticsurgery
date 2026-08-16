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
A verdict below records that ruling; whether it is actually enforced on disk is
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
| wny | "WNY PLASTIC SURGERY" | lower torso, y 0.91-0.98, **after image only** | **yes**, off-breast | open captain decision `wny-after-only-watermark`: clear of the breasts, but perfectly correlated with the training label. **Not enforced: all 10 pairs are live in the finished tree** (see below) |
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
  re-emission if heavenly is ever re-staged. Verified precisely: zero
  `heavenly-*` directories in the finished-tree shape
  (`clinic-corpus/<clinic>/<pair-id>/`, i.e. `clinic-corpus/heavenly/`), and
  zero heavenly pairs reach `build_dataset.py`'s output when run over the whole
  corpus.
  That claim does not cover `~/firstmate/data/clinic-corpus/_staging/` and
  `~/firstmate/data/clinic-corpus-staging/`, which each still hold 63
  `heavenly-*` pair directories. Those are pre-existing, non-canonical
  duplicate-ingest artifacts from 2026-08-12 (`~/firstmate/data/report/`'s
  README has recorded them since 2026-08-15 as "duplicate partial ingest
  runs... counted nowhere in this report"); no training script reads either
  path as an input, so they are outside the finished-tree definition and
  outside this retirement - named here only so the verification above is not
  misread as covering them.
- **wny**: all 10 pairs are live in the finished tree, 9 of them hanging on the
  open `wny-after-only-watermark` decision.
  Same shape, one decision away.
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
