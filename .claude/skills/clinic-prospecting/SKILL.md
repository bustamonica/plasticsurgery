---
name: clinic-prospecting
description: Evaluate a clinic gallery before spending a scraping/annotation run on it, or before scoping a collection pass against a clinic already in the queue. Load before assessing, proposing, or configuring a new clinic gallery for the corpus, and before writing a collection brief against one.
---

# Clinic prospecting

A nine-clinic sweep on 2026-08-14 returned 47 usable pairs and burned an
evening discovering things one clinic at a time that were knowable from the
public gallery in five minutes.
This skill is that five minutes, done first.

Every number below is measured, not estimated, and comes from
`~/firstmate/data/ba-viz-emit-*/report.md` (the 2026-08-14 per-clinic sweep),
`ba-viz-emit-backlog/report.md`, and `ba-viz-gallery-sweep/report.md`.
Cite the clinic report when you need the full derivation; this file is
self-contained for the decision itself.

## How to use this

Run the four screens in section 1 first, in order, on whatever sample the
public gallery lets you pull without scraping the whole site (10-20 cases is
enough to know whether a screen kills the clinic).
Any one failure below at scale is a strong reason to stop before writing a
scraper config.
If the clinic survives, do the deeper checks in section 2 - these are the
ones that only show up after someone commits, and they change the yield
estimate, not just the count.
Then compute the yield estimate in section 3 and rank against other
candidates.
Record what you found using section 5's template regardless of the verdict,
so the next agent does not re-spend the same evening.

---

## 1. The four fast screens

Each screen is measured on a real sample, not eyeballed from thumbnails.

### Screen 1: resolution

**Measure the short edge of each half of a before/after pair, after
splitting any composite, against a 400px floor** (`training/scripts/ingest.py`
`MIN_DIMENSION`).
Do this on the largest source the site actually offers, not the page's
display size: check the `srcset` for a wider descriptor than the one in
`<img src>`, look for a lightbox or full-size link, and strip a `?w=` query
string if the CMS uses one (Studio 3/DatoCMS clinics do).
If the largest `srcset` entry is already what `src` points to, there is no
larger version to find - confirm that before concluding a clinic is
resolution-limited.

A single-file composite has a *ceiling*: if the page publishes one fixed
image size for every case, no larger source exists to hunt for, and the
split-half size is the clinic's permanent maximum.
Compute it once (short edge after split) and stop looking.

Measured killers from the 2026-08-14 sweep:

| clinic | published size | after split | floor | verdict |
| --- | --- | --- | --- | --- |
| privateclinic | 610x370, every one of 80 photos, no larger source exists (`srcset` tops out at 610w) | 305x370 | 400 | dead - ceiling is 305, no fix recovers it |
| drtavakoli | 750x411 (2024 batch, 11 cases) | 375x411 | 400 | dead, 25px short |
| skplastic | 1200x502, 3x2 grid composite | 383x250 (best achievable cell) | 400 | dead - even a crop-bug fix caps at 383x250 |
| drrohrich | 800x640, 2x2 grid composite | 400x320 | 400 | dead on the short axis (320) despite clearing it on width |

Corpus-wide, 560 of 5458 images sit below the floor, concentrated in the
grid-composite clinics: drteitelbaum 444, drrohrich 90, skplastic 24,
drtavakoli 2.
`MIN_DIMENSION` is 400, not 512, specifically because drkolker publishes at
418px - it was never meant to admit 300-380px galleries, and no clinic has
cleared it after the fact.

### Screen 2: published implant volume, per case

**`volume_cc` is a hard `REQUIRED_FIELDS` entry in `ingest.py`.** A case
without a documented volume is worthless however good the photograph -
check this before evaluating anything else about the case.

Sample 10-20 case pages and count how many state a volume in any unit.
Then check the unit: clinics that publish in grams (anatomical/shaped
implants are specified that way by their manufacturers) or as a mixed
cc/gram vocabulary on the same page still count as "no volume_cc" until a
captain ruling authorizes reading grams as cc - see the undocumented-spec
lesson below.
A published *range* ("100-149 lbs") is not a measurement and does not
count.

Measured killers:

| clinic | no-volume pairs | cases | note |
| --- | ---: | ---: | --- |
| marina | 339 | 81 | no `volume_cc` anywhere on the page; the case genuinely has no implant in some of these |
| drkolker | 75 | 25 | clean otherwise - 61 of the 75 pass every other gate |
| wny | 2 | 1 | case names a brand and profile but states no volume in any unit |
| drmiroshnik | (180 initially) | 100 | volume published in **grams** - recoverable pending a captain ruling, see below |

### Screen 3: breast augmentation only

Mommy Makeover and augmentation-with-lift/mastopexy are excluded by captain
ruling: the after photograph shows a change (abdominoplasty scar, relocated
navel, lift scars) that the implant did not cause, and a model trained on
that pair learns the wrong thing.
A mixed gallery is not disqualified - it just means the case count
overstates the yield.
**Read the procedure description on each case, not just the photo.**

Measured example - wny, 13 published cases / 72 composites:

| outcome | composites | share |
| --- | ---: | --- |
| pure breast augmentation | 5 | 8% |
| mommy makeover (augmentation + abdominoplasty/lift) | 24 | 40% |
| augmentation + mastopexy | 10 | 17% |
| no description published (unemittable regardless) | 3 | - |
| other/unresolved | rest | - |

Fixing a parser bug at wny recovered 39 pairs, but only 5 of those 39 were
pure augmentation - the pure fraction, not the raw recovery count, is what
should go into the yield estimate.

### Screen 4: no on-body watermark

**Off-body corner captions ("Before" / "© Dr. X") are harmless and need no
mask.** A watermark drawn across the torso is a different problem: it sits
exactly where the anatomy is, and the corpus's own censorship/watermark
detectors cannot see it (see the self-measurement lesson below).

Check by eye at full resolution on a sample from each backdrop/template
variant the clinic uses (a script overlay can look different on a light vs
dark backdrop and still be the same mark).

Measured cost - heavenly, universal, on-body, across two backdrop families,
and all 119 of its pairs ruled unusable (which clinic carries which mark, the
verdict on each, and whether that verdict is actually enforced on disk, is
`training/clinic_watermarks.md`):

- Masking the watermark band (`masked_regions`, `dataset_schema.json`) is
  the correct, honest fix - but the band covers the breasts on essentially
  every pair, so "kept with a mask" and "usable for breast augmentation
  training" are different claims.
- A GPU inpainting attempt cost $6.75 (RunPod, H100) and *reported* 99.4%
  removal; a visual audit of the delivered images then found **38 still
  legibly watermarked and 22 more with a partial residual**, and a third look
  found the remainder no cleaner. Two clean-up passes, each overstating its
  own success. See the self-measurement lesson below for why every one of
  those metrics read optimistically.
- Off-body watermarks are cheap by comparison: wny's "WNY PLASTIC SURGERY"
  band sits at the bottom 9% of the after-image frame only, never touching
  the breasts, and needs no mask - but being on the *after* image alone
  correlates it perfectly with the training label, which is its own open
  question.

---

## 2. The lessons that only show up after you commit

These are why this is a pre-flight skill and not just "check the four
screens": every one of them was found *during* or *after* a collection run,
cost real work to discover, and is checkable from the public gallery before
committing.

| lesson | evidence | check before committing |
| --- | --- | --- |
| A published spec is not a parseable spec | lakeshore published 218 pairs' worth of volumes in plain sight; a single-layout parser dropped every one, because the shared `influx_swiper` template publishes the same spec block three ways on one site (one `<p>` per label, one `<li>` per field, a bare-value layout with no labels) plus once more as the wrapper `<p>`'s own text | View source on 3-5 case pages, not just the rendered page. Count how many *distinct markups* the spec block appears in, not just whether text is visible |
| Case ids are not reliably unique | mya: case id `C-MYA508703` published twice with **different photographs** - the second silently overwrote the first, and the surviving pair kept the wrong view label. basu: 12 case ids repeated up to 6x each (pointing at the *same* image), inflating the raw parsed-entry count from a true 174 cases to 234 | Diff the case-id set for duplicates before trusting any case count; check whether a duplicate id points at the same image (inflation only) or a different one (silent overwrite risk) |
| Count the pipeline's output, not the raw intake tree | drkolker was recorded everywhere as "105 cases / 315 pairs" - that is the raw scraper intake tree, byte-identical to the published source. The real pipeline output (after `ingest.py`) was **240 pairs / 80 cases**; sanantonio has the same gap (291 raw vs 203 staged) | Ask "which tree is this count from" before repeating any clinic's case/pair number - intake and pipeline-output are different questions |
| Never trust a number a tool derived from its own assumption | The heavenly watermark-removal metric projected the image onto the mask it had itself chosen for scoring - 97.5% of that template's energy sat in two flat caption blobs that come off trivially, so the score read ~99% regardless of whether the actual script lockup was removed. Separately, `detect_censorship` measured a damaged image as CLEAN in memory and only caught the damage after a quality-95 re-encode, because JPEG quantization moves the texture signal the detector reads | **Open the images and look at them** - a real sample, at full resolution, before accepting any tool's own success metric |
| Undocumented specs are never invented | Natrelle model numbers (e.g. `SRM-445`) encode a volume in cc but are not decoded; sanantonio, drkolker ("Mini Motiva"), allure (`SHPX` = Smooth High Profile Xtra) and charlotte (Mentor style `1600`, which encodes nothing at all) all hit this and none were decoded without a captain ruling | If a case's only spec is a manufacturer model code, treat it as unparseable, not as a near-miss - the decode decision is captain-level every time |
| Platform family is the highest-leverage question | One parser serves every clinic on the same site builder. Known families: Influx legacy numbered-subpage (drkolker, lakeshore/marina via `influx_swiper`, austinweston, charlotte, allure), Etna Interactive composite (drrohrich 2x2 grid, wny per-view 2-up), Webflow (sixsurgery, skplastic 2x3 grid), Studio 3/DatoCMS paginated (drgrover, basu, drteitelbaum), bespoke WordPress (harrington, drjeremyhunt, drmiroshnik, privateclinic, mitchellbrown, heavenly, mya, drtavakoli) | **Identify the builder before estimating integration cost.** A candidate on an already-supported family is worth far more than its case count suggests - it needs zero new parsing code, only a `CLINICS` config entry. View source and compare markup shape against `training/scripts/scrape_gallery.py`'s per-parser docstrings before assuming a new parser is needed |
| Enumerability, and views per case | Three clinics (drkolker, drdanielbarrett, sanantonio) have no cached listing page to force-refresh - enumeration depends on the live site staying reachable the same way each time. Views per case vary by clinic and multiply yield directly: harrington's 5-view cases publish a fixed front/oblique-left/side-left/oblique-right/side-right order; the `influx_swiper` schema enumerates up to 5 views per case; a clinic publishing only a front composite caps yield at 1 view x case count regardless of how many cases exist | Check how the case list is discovered (single listing page, pagination, link-chain, positional ids with no stable id at all) and count views per case on the sample - both go directly into the yield formula below |

---

## 3. Yield estimate - arithmetic, not a feeling

```
expected usable pairs ≈ cases × views per case × pure-augmentation fraction × expected pass rate
```

Where `expected pass rate` is the fraction of augmentation cases that clear
every screen in section 1 on your sample - do not assume 100%; use the
measured pass rate from the sample you pulled.
Subtract the observed killers explicitly rather than folding them into a
vague discount:

```
usable ≈ cases
        × views_per_case
        × pure_augmentation_fraction
        − resolution_floor_losses
        − no_volume_losses
        − watermark/censorship_losses (after any documented mask)
        − duplicate/overwrite losses
```

**Rank candidates by this number, never by raw case count.** A 20-case
gallery that clears every screen beats a 120-case gallery that dies at one
of them - drkolker's 315-pair raw intake produced 240 pipeline pairs and
sanantonio's 291 produced 203; a small clean gallery with no such gap is a
better bet than either looks at first glance.

---

## 4. The consent boundary

**The corpus rests on executed AI-training consent, clinic by clinic.**
Everything in this skill - resolution checks, spec sampling, platform
identification - covers *publicly published* material and is safe to do
from a browser or a read-only scrape of listing pages.
A clinic that passes every screen in this skill is a clinic to **approach**
for consent, never one to collect from.
Do not run a full ingest pipeline against a clinic without a captain-
confirmed, executed consent record for that clinic, no matter how good its
gallery looks.

---

## 5. The honest-shortfall rule

If a target pair count cannot be met with clinics that genuinely pass,
**deliver fewer, ranked, with the near-misses and their exact failing
screen** - not a padded number that includes clinics that will die at
ingest.
A truthful 22 beats 30 that includes skplastic's 383x250 ceiling or
marina's 339 spec-less pairs counted as if they were viable.

When you finish an assessment, record for each candidate: platform family,
case count, views per case, measured resolution (with source URL/selector),
volume-field coverage on the sample, augmentation-purity fraction, watermark
class (none / off-body / on-body), and the yield estimate with its
subtractions shown - so the next agent inherits the measurement, not just
the verdict.
