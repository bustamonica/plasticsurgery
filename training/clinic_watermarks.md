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
the primary training signal, so those clinics are excluded rather than repaired.

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
| heavenly | script "Heavenly / PLASTIC SURGERY" lockup, diagonal | y 0.21-0.68 of frame - **over the breasts** | **yes, over breast tissue** | **excluded, all 119 pairs.** Two clean-up passes each overstated their own success; see `~/firstmate/data/report/README.md` |
| wny | "WNY PLASTIC SURGERY" | lower torso, y 0.91-0.98, **after image only** | **yes**, off-breast | open captain decision `wny-after-only-watermark`: clear of the breasts, but perfectly correlated with the training label |
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

## Maintaining this file

Add a row when a clinic is measured, not when one is guessed at. Say where the
mark sits in the frame and whether it touches breast tissue - that is the only
part of the observation the emit decision turns on.
