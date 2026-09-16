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
the scraper trims both halves equally when it writes the pair.
**Captain ruling, 2026-09-15: a mark centred on the composite's split seam is
not one-sided, and is not cropped.**
The split cuts it into mirrored fragments, so each half carries an equal piece
and nothing correlates with the label; the screen that says otherwise is the one
that windows the same corner of both halves, and "A seam-centred mark reads as
one-sided if you compare the same corner" below owns that lesson.
The crop is `ClinicConfig.crop`, one `framing.Crop` per clinic: a single
mechanism with one rule per way a clinic actually draws its mark (`px`,
`width_frac`, `height_frac`, `keep_height_frac`, `caption_band`), and the rules
are not interchangeable; `training/scripts/framing.py` says what each does, and
the verdicts below name the one each clinic uses. The crop is measured per clinic and never transferred between
them; the full lesson, the detection recipe and the measured cost are
`.claude/skills/clinic-prospecting/SKILL.md` Screen 4a.

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
| wny | "WNY PLASTIC SURGERY" | lower torso, y 0.91-0.98, **after image only** | **yes**, off-breast | **DECIDED 2026-08-19: cropped, not tolerated.** The mark is clear of the breasts but sits on the after image alone, which correlates it perfectly with the training label. `Crop("px", 60)` (mark top measured 51px from the bottom) trims both halves when the scraper writes the pair; before/after high-pass asymmetry 11.5 -> 1.30. 9 of 10 pairs re-emitted cropped; the crop costs 0 pairs to the 400px floor (every half is 508px on its short side after it), and `wny-19-front` is excluded for carrying no `volume_cc`, which is a spec gate and nothing to do with the crop |
| **tccs** | colour-wheel logo + "THE CENTER FOR COSMETIC SURGERY" wordmark | bottom-left of the composite, so the **before image only** (743 of 743 pairs) | no - lower frame, below the breasts | **cropped 2026-08-19**, `Crop("px", 130)` (mark top 122px from the bottom at the median, 124px at p95); 9 pairs then fall below the 400px floor and are excluded |
| **roth** | "Jeffrey J. Roth, M.D., F.A.C.S." script wordmark | bottom-right, **after image only** (162 pairs) - 16.9x asymmetry, the strongest measured | no - lower frame, below the breasts | **cropped 2026-08-19**, `Crop("px", 175)` (mark top 162px from the bottom); costs 0 pairs to the 400px floor |
| **camp** | "STEVEN CAMP MD PLASTIC SURGERY" | bottom-right, **after image only** (299 pairs) - 7.4x asymmetry | no - lower frame, below the breasts | **cropped 2026-08-19**, `Crop("px", 110)` (mark top 98px from the bottom on the tall height groups, 81px on the 478px group); 53 pairs then fall below the 400px floor and are excluded |
| harrington | faint caption | very bottom of frame, below the subject | no | acceptable |
| mitchellbrown | "Photos courtesy of Dr. Mitchell Brown, TorontoPlasticSurgery.com" | white margin below the subject | no | acceptable |
| drrohrich | "© Rod J. Rohrich MD - http://drrohrich.com" (28 of 45) | white margin between panels | no | not the blocker (resolution is) |
| drteitelbaum | burnt-in "Before"/"After" | top-left, on the backdrop | no | not the blocker (no volume, resolution) |
| skplastic | translucent "SK" box; black "before"/"after" bar | lower-right corner; left ~10% of each half | no | not the blocker (resolution) |
| privateclinic | "The Private Clinic of Harley Street" | **straddles the composite's split midpoint**, so each half carries a fragment | - | not a label leak - seam-centred, so each half carries an equal fragment (see the note under this table); re-check if the clinic ever supplies originals, which is a resolution question, not a mark one |
| drgrover | "© Sanjay Grover, MD, FACS" + date stamp (4 of 12 sampled) | bottom-right; **grazes the lower abdomen on some** | sometimes | re-check before this clinic is ever emitted |
| allure, austinweston, charlotte, drjeremyhunt, drmiroshnik, lakeshore, marina, mya, sixsurgery | none | - | - | clean |
| **choice** | white caption band printing **BEFORE** under the left half and **AFTER** under the right, in gold | caption band under both halves of the composite | no - below the subject | **cropped 2026-08-25**, `Crop("px", 60)` (the band starts 50-53px from the bottom and its gold text tops out at 53px, measured over all 52 published composites); the halves stay 455x441, above the 400px floor. The words themselves are the label, so this is the leak in its most literal form even though both halves carry a band |
| **arps** | "(c) Dr Eddie Cheng" (some exports "(c) DR Eddie Cheng AR Plastic Surgery") | bottom-left or bottom-right corner over clothing/backdrop at hip height, on **both** halves, so not a label leak | no - well below breast tissue | **cropped 2026-08-25**, `Crop("width_frac", 0.10)` - a fraction of the frame's **WIDTH**, because the mark is drawn proportional to width and this gallery publishes six export sizes (top edge 3.8-7.2% of width, but 24-115px). Verified gone by eye on all 186 images and by the bottom band's high-pass peak falling from 3.4-28x the body baseline to ~1x; costs 0 pairs to the 400px floor |
| **bandy** | practice wordmark burned into the bottom band of the 655x491 exports (241 of 2,664 images are un-watermarked originals, cropped identically anyway rather than by a per-image detector) | bottom band, both halves | no - over the lower abdomen | **cropped 2026-08-25**, `Crop("keep_height_frac", 0.8167)` keeps the top 81.67% of the frame; measured by a median high-pass over all 2,199 exports (the ink starts at y=401 of 491) |
| **mwps** | translucent "MW / MOUNTAIN WEST PLASTIC SURGERY" circle monogram over two lines of type | centred **on the composite's split seam** at the bottom, so an equal piece lands on each half - not a label leak, though cropping one side would create one | no - over the lower abdomen | **cropped 2026-08-25**, `Crop("height_frac", 0.22, stage="composite")` - a fraction of **HEIGHT** applied to the whole composite BEFORE the split, so the halves stay dimension- and framing-matched. The mark is scaled to the frame, not stamped at a fixed size (the circle's top sits at 0.2064-0.2077 of height on three heights). Do not re-derive it from a bottom-aligned residual: that population is dominated by one height and reports a plausible, wrong ~100px constant. The crop is why the yield is 19 pairs and not 125 - most of the gallery falls below the 400px floor after it |
| **tcclinic** | white TCC caption band **plus a logo badge that rises 44px out of it into the frame** | bottom of the 1200x571 family only (9 of 65 composites; the 835px families carry none), straddling the seam so both halves carry it | no - lower torso | **cropped 2026-08-25**, `Crop("caption_band", stage="composite")` measures the rows per image (161px on all nine banded composites, 0 on the 56 unbanded) plus the clinic's `Frame(seam_trim=8)`, 8px either side of the midpoint. The band's own 117px height is the wrong crop - it would leave the badge on the image. Every emitted half clears the 400px floor |
| aips, bayside, blaine, dsm, ncps, psiw, sculpted, swan, wyten | none | - | - | clean; each opened at full resolution during its 2026-08-25 collection - no corner, edge or on-body mark, so no crop is applied |
| **gryskiewicz**, **ciaravino**, **gallatin** | none | - | - | clean - all 449 front pairs the 2026-08-25 batch emitted. 448 were measured 2026-08-26: per-half high-pass peaks are 5-18 (a clean clinic reads 10-11, the wordmark clinics above 36-78) and the two halves sit within 1.3x of each other, so there is no fixed overlay and no one-sided mark to crop. `gallatin-120-front`, added by gallatin's 2026-09-14 re-emission, was confirmed 2026-09-14 by an independent re-implementation of the same recipe over all 33 gallatin fronts (300x300 canvas, per-half mean, radius-12 Gaussian high-pass, 99.5th-percentile peak): halves 1.15x apart, no overlay in the rendered means, and both halves of the pair opened and clean by eye. Its peaks (21.06/18.36) are on a different absolute scale from the 2026-08-26 script's, so compare only the ratio, never the raw figures. The batch's held lateral pairs are outside that measurement too and want their own pass when a laterality ruling releases them |
| **coberly** | opaque white "Coberly Plastic Surgery & Med Spa" script wordmark | lower abdomen, same place on both halves, so not a label leak - but its height **varies by case**, 167-339 rows above the bottom across the 216 images carrying it | no - well below the inframammary fold | **cropped 2026-08-26**, `Crop("px", 355)`, the WORST case over every image plus margin (a median high-pass said 215 and a six-image crop ladder said 265; both looked clean on their samples and were wrong). Leaves 1005x645 on the uniform 1005x1000 exports; the 379x377 family is under the floor anyway |
| **folk** | "Stacey Folk, MD" script wordmark | bottom-right of **each** half, over the lower abdomen, so not a label leak | no - lower abdomen | **cropped 2026-08-26**, `Crop("width_frac", 0.105)`, a fraction of WIDTH, because the gallery serves several export sizes (worst case measured 0.096 of half width). Verified gone by eye on both halves of every surviving size family; the one 1194x450 composite falls under the 400px floor after the crop and is rejected at ingest |
| drtabbal | burnt-in "Before"/"After" word | top-left, x 32-169 / y 49-79 of its 576x1024 frames | - | **withheld, never collected** (captain, 2026-08-26): the word contradicts the gallery's own before/after slots on 51 of 96 pairs, so it is a wrong label on the axis the corpus teaches, and removing it needs a top crop no mechanism provides. Evidence in `~/firstmate/data/ba-viz-collect-rosemont-16/report.md` section 8 |
| weston, pscarolina, leber, jkps, boynton, bottger, sbschooler, najera, goldberg, copeland, lintner, savetsky, teleos | none | - | - | clean; per-clinic median over 40 images during the 2026-08-26 Rosemont collection shows no overlay, so no crop is applied |
| brisbane | none | - | - | clean; measured 2026-09-15 over all 40 published photographs (mean of the 20 before-halves against the 20 after-halves, 384x384, radius-6 high-pass): peaks 19/18, 99.5th percentile 9/9, so no fixed overlay and no one-sided mark, and all 40 opened by eye. The after mean's only structure is dark clothing at the bottom edge, which is photograph content, not a burned-in mark. Two cases carry burned-in mosaic instead (12 on all four photographs, off the breasts; 8 on its AFTER photographs only); see the collection's PR |
| sarasota | red "SARASOTA PLASTIC SURGERY CENTER" block | bottom-right corner of each half | no | cropped: 0.25 of composite HEIGHT before the split (`Crop("height_frac", 0.25, stage="composite")`), measured 2026-09-15 as a block whose top edge sits at 0.205-0.226 of height across the 1800x599/600/678 and 1280x480 families; on both halves of almost every composite, one half only on `Q1M074iQ9yB8`, and absent on a few, so the crop runs on every composite. Tight framings put the crop line through the lower breast pole: 149 pairs are withheld by eye for it (decision `sarasota-corner-block-crop`, A), each carrying its reason in `training/annotations/sarasota.json`. The 1280x480 family and smaller fall under the 400px floor once cropped |
| ablavsky | peach-filled disc logo, "ABLAVSKY / PLASTIC SURGERY" | top of the composite, **centred on the split midpoint**, so each half carries half the disc - the before half at its top-RIGHT, the after half at its top-LEFT | no - above the breasts, at the clavicle line | **no crop, ruled 2026-09-15** (captain, option A): both halves carry an equal fragment, so it is not a label leak, and it ships uncropped on the privateclinic precedent - mwps and tcclinic carry the same seam-centred shape but are cropped anyway for a reason that is not the leak, their marks sitting at the bottom where a whole-composite crop reaches them and leaves both halves matched. A v2 sample review reported it as after-only; that screen compared the SAME corner of both halves, which is the wrong pairing for a seam-centred mark - see the note under this table. Measured on all 86 emitted pairs: badge fill present on both halves of 86 of 86, and inner-edge high-pass 1.01-1.03x across the three size groups (the same-corner screen reads 7.4-9.9x, and the mirrored corner "proves" before-only). Removing it anyway would need a new TOP crop direction that `framing.py` does not have, at 0.155 of height, taking the clavicle and upper chest off every frame for 3px of headroom over the 400px floor. 0 pairs lost. Evidence: `~/firstmate/data/ba-viz-ablavsky-badge-crop/report.md` |

drtavakoli marks some gallery images with its own name, but none of the pairs in
the corpus carries one.

sixsurgery is blocked on **censorship**, not watermarking: every published photo
has opaque circles over the nipples.

### A seam-centred mark reads as one-sided if you compare the same corner

The one-sided-mark screen compares the before half against the after half.
Which WINDOW of each half it compares decides the answer, and for a mark centred
on the composite's split midpoint the obvious choice is the wrong one.

Such a mark is cut in two by `framing.split_composite`, and the two fragments
land in **mirrored** corners: the before half's is at its top- or bottom-RIGHT,
the after half's at its top- or bottom-LEFT.
Compare the same corner of both halves and one of them is empty, so the screen
reports a large asymmetry and names whichever half you happened to window as the
marked one.
Run the identical procedure at the mirrored corner and it reports the opposite
half with equal confidence.

ablavsky is the worked example, and it cost a v2 training run's sample review a
false finding: same-corner 7.4-9.9x ("after only"), mirrored corner 37x ("before
only"), inner edges 1.01-1.03x, which is the true answer.
Before acting on a one-sided verdict, either window the WHOLE half (the Screen 4a
recipe's peak over the full frame, which is symmetric for a seam mark) or compare
the inner edges against each other, and look at the mark.
privateclinic, mwps, tcclinic and ablavsky all carry marks of this shape.

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
  ground, not on size. This is enforced in code (the clinic's `crop`), not just
  documented - as it is for tccs, roth and camp, the other three clinics the
  2026-08-19 one-sided-mark measurement caught.
- **sixsurgery**: withheld *in fact*.
  Nothing was ever emitted there - 0 pairs in the finished tree - so its
  withholding is already true as a matter of fact rather than of prose.
  That is what an enforced exclusion looks like.
- **drtabbal**: withheld *in fact*.
  It has no `CLINICS` entry in `training/scripts/scrape_gallery.py`, so no run
  can collect it, and nothing was ever emitted.

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
