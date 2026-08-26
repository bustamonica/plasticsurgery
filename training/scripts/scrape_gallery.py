#!/usr/bin/env python3
"""Scrape consented clinic before/after galleries into the training intake layout.

Pluggable per-clinic configs (CLINICS) describe how each consented gallery is
crawled and parsed; adding a new clinic means adding one ClinicConfig and, if
its markup differs, one parser. Currently supported:

- drkolker: https://drkolker.com/gallery/breast/breast-augmentation/
  Static HTML; each case is a page (<gallery>/<NN>/) with images NN/01.jpg..
  NN/06.jpg whose alt text carries Before/After and Front/Oblique/Side view
  labels, plus a 'Patient Details' spec block.
- drdanielbarrett: https://www.drdanielbarrett.com/los-angeles-before-after-photos/...
  Webflow CMS; all cases are inline items (div.before-after_items) on one
  category page. Views are NOT labeled; before/after is derived from tagged
  thumbnail slides plus the (verified) filename-index convention, and every
  view label comes from visual-inspection annotations.
- sanantonio: https://sanantonioplasticsurgery.com/before-after-photos/breast-augmentation/
  WordPress + BRAG book plugin; one listing page links each case page
  (div.brag-book-gallery-case-detail-view). Each case carries a Patient
  Information grid (Age/Height/Weight/Implant Size/Brand/.../Photo Taken) and
  a Case Notes narrative. Images are side-by-side before|after COMPOSITES
  (left half before, right half after) served as signed Supabase URLs; the
  scraper splits each composite into the pair's before/after files. Views are
  not labeled ('Angle N'), so every view label comes from visual-inspection
  annotations.

Output layout (what ingest.py expects):

    <out>/<clinic>/<pair_id>/{before,after}.<ext> + meta.json

with pair_id = "<clinic>-<case>-<view>" per training/dataset_schema.json.

Politeness contract (per the clinic agreements): sequential requests only,
>= --delay seconds between requests (default 2s), descriptive User-Agent, no
parallelism. All fetches go through a local on-disk cache so re-runs (parser
iterations, re-emitting metadata) never re-hit the site.

Access contract, separate from the consent instrument: a path a site's robots.txt
disallows is fetched only under that practice's own written grant. The one such
route today is the Etna gallery's case-list endpoint, opened by --gallery-endpoint
and refused unless the clinic's ClinicConfig.endpoint_grant document is on disk;
see that field and the case-list-endpoint section below.

View labels and laterality: dataset_schema.json requires view in
front/oblique-left/oblique-right/side-left/side-right, but neither gallery
documents laterality in text (Barrett does not document view at all). Rather
than inventing labels, pairs whose view is not derivable from the page are
emitted only when an annotations file (--annotations, JSON) provides one,
produced by visually inspecting the downloaded images:

    {"<clinic>:<case>": {
        "laterality": "left" | "right",       # kolker shorthand (oblique+side)
        "clothing": "nude" | "bra" | "top",
        "pairs": {"<pair_key>": {"view": "<schema view>",
                                 "clothing": "nude" | "bra" | "top"}}}}

Case spec fields that the gallery does not document are omitted (or use the
schema's "unknown" enum value); values are never guessed.

20 more consented clinics (2026-08 batch) are registered in CLINICS below,
one parser family per gallery platform (Influx legacy/Growthstack/S3, Etna
Interactive, Webflow, Studio 3 Marketing/DatoCMS, WordPress-custom, and a
handful of bespoke builds); see each parser function's docstring for its
specific markup contract.

The 2026-08-25 batch (clinics prospected in ba-viz-prospect-20-international
and consented on 2026-08-25) adds one parser per gallery platform in the same
way; kind='aips' is the first, a Breakdance page builder whose photos are CSS
background-images rather than <img> tags. See its section below.

12 more (2026-08-15 batch) all run the Etna Interactive photo gallery and are
served by the single kind='etna' parser - one parser configured twelve times.
See the etna section below for the platform's markup contract, its five
published spec-block layouts, and how enumeration and procedure purity work.

The 2026-08-25 batch (19 prospected clinics, consent in
clinic-corpus/CONSENT-2026-08-25-PROSPECTED-CLINICS.md) is being collected one
clinic at a time; kind='blaine' is the first of them, a bespoke WordPress
[gallery] shortcode. See its section for why 'Case #N' is not a patient key and
why the procedure screen reads the whole figure rather than the spec field.

The 2026-08-25 batch (prospected clinics, consent in
clinic-corpus/CONSENT-2026-08-25-PROSPECTED-CLINICS.md) adds kind='mwps', an
Influx Growthstack gallery whose subcategory is 'stitched': every slide is a
whole before|after composite, and every composite carries a bottom-edge
watermark that is cropped before the split. See the mwps section below.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

import page1_solutions as p1

USER_AGENT = (
    "clinic-corpus-scraper/1.0 (consented before/after gallery crawl for "
    "AI training-data intake; sequential, rate-limited)"
)

SCHEMA_VIEWS = {"front", "oblique-left", "oblique-right", "side-left", "side-right"}
SCHEMA_CLOTHING = {"nude", "bra", "top"}

BRAND_KEYWORDS = [
    ("motiva", "motiva"),
    ("mentor", "mentor"),
    ("natrelle", "natrelle"),
    ("allergan", "natrelle"),  # Natrelle is Allergan/AbbVie's implant line
    ("sientra", "sientra"),
]
# 'motiva' is also the prefix of 'motivated'/'motivation', which clinic
# marketing prose uses constantly ('her motivation was to enhance her natural
# silhouette' - mwps case 188). A substring test read that as a Motiva implant,
# and because Motiva is first in BRAND_KEYWORDS it also beat the brand a case
# actually named (12 etna cases published Natrelle and were recorded Motiva).
# Worse, a false Motiva unlocks MOTIVA_PROFILE_PATTERNS, so 'a full, balanced
# figure' in the same sentence became profile=high - a fabricated training
# label out of prose naming no implant at all.
# Only this one keyword is anchored. The others stay substring matches because
# clinic markup routinely glues a brand to its neighbour: harrington strips its
# inline <a> without a separator, publishing '...mammoplasty withSientrasmooth
# round silicone implants', where a word boundary would silently drop 66 pairs'
# documented brand.
BRAND_WORD_ONLY = {"motiva"}
PROFILE_PATTERNS = [
    (re.compile(r"\b(extra[- ]high|ultra[- ]high)\b", re.I), "extra-high"),
    (re.compile(r"\bmoderate[- ](profile[- ])?plus\b", re.I), "moderate-plus"),
    (re.compile(r"\bhigh\s+(profile|projection)\b", re.I), "high"),
    (re.compile(r"\bmoderate\s+(profile|projection)\b", re.I), "moderate"),
]
# Motiva projection families map onto the schema's profile enum (captain's
# ruling): Mini -> moderate, Demi -> moderate-plus, Full -> high,
# Corsé -> extra-high. The bare words ('Demi', 'Full') are ambiguous outside
# Motiva, so they are only matched when the case documents a Motiva implant.
MOTIVA_PROFILE_PATTERNS = [
    (re.compile(r"\bmini\b", re.I), "moderate"),
    (re.compile(r"\bdemi\b", re.I), "moderate-plus"),
    (re.compile(r"\bfull\b", re.I), "high"),
    (re.compile(r"\bcors[eé]\b", re.I), "extra-high"),
]
# Chart vocabulary for the schema's placement/incision enums. Dual-plane is
# tried first because a dual-plane case is routinely also described as
# submuscular. Only terms clinics actually print on a spec chart are listed:
# 'over the muscle' and 'incision around the areola' are prose descriptions of
# a placement/incision, not the documented value, and reading them as one would
# be the kind of inference AGENTS.md rules out.
PLACEMENT_PATTERNS = [
    (re.compile(r"\bdual[- ]?plane\b", re.I), "dual-plane"),
    # 'Post Pectoral' is drbandy's chart word for the same placement as
    # subpectoral/retropectoral - behind the pectoralis. Its opposite,
    # 'Pre-Pectoral', is deliberately NOT listed: in front of the muscle is
    # either subglandular or subfascial and the chart does not say which, so
    # reading it as one of them would invent the value.
    (re.compile(r"\b(?:sub[- ]?muscular|subpectoral|retropectoral"
                r"|post[- ]?pectoral)\b", re.I), "submuscular"),
    (re.compile(r"\bsub[- ]?glandular\b", re.I), "subglandular"),
    (re.compile(r"\bsub[- ]?fascial\b", re.I), "subfascial"),
]
INCISION_PATTERNS = [
    (re.compile(r"\binframammary\b", re.I), "inframammary"),
    (re.compile(r"\b(?:peri|circum)[- ]?areolar\b", re.I), "periareolar"),
    (re.compile(r"\b(?:trans[- ]?)?axillary\b", re.I), "transaxillary"),
]


@dataclass
class ClinicConfig:
    slug: str
    consent_ref: str
    base_url: str
    gallery_paths: list[str]
    kind: str  # parser implementation key
    # Rows trimmed from the BOTTOM of both halves of every pair, after the
    # composite split.
    #
    # This exists for one reason and it is not cosmetic. tccs burns a colour-wheel
    # logo and a THE CENTER FOR COSMETIC SURGERY wordmark into the bottom-left of
    # the composite - which, because the composite splits at the midpoint, puts it
    # on the BEFORE half of 743 of 743 pairs and on 0 after halves. A mark that
    # perfectly correlates with the before/after label is a poisoned axis, not a
    # blemish: an edit model can satisfy "make the breasts larger" by learning to
    # remove a logo, and that would score as success in evaluation while teaching
    # nothing about augmentation. wny has the same defect inverted (a caption on
    # every AFTER image and none on the before). choice is the extreme case -
    # its band literally prints the words BEFORE and AFTER under the respective
    # halves.
    #
    # The crop is applied to BOTH halves equally so the two never differ in
    # framing - a framing difference would be the same correlated-with-the-label
    # artifact in another form. It removes lower abdomen, well below the breasts,
    # which is why a free crop is the right tool here rather than masking or
    # inpainting: heavenly's three inpaint passes cost $28 and still failed.
    # Anything falling under ingest.py's 400px floor after the crop is rejected
    # there rather than shipped shrunken.
    bottom_crop_px: int = 0
    # Reference to the site owner's grant of access to the gallery's own case-list
    # endpoint (`admin-ajax.php`, which these robots.txt files otherwise disallow).
    #
    # This is deliberately a per-clinic opt-in and not a platform-wide switch. The
    # executed AI-training consent covers USE of the material; it says nothing about
    # how the material is fetched from the practice's own server, and the practice -
    # not Etna Interactive, which is its vendor - is the party who can grant that.
    # A clinic with no grant on file here can never have its endpoint called, however
    # the run is invoked, and `--gallery-endpoint` must be passed as well, so neither
    # a config edit nor a stray flag alone opens it.
    endpoint_grant: str | None = None
    # Rows trimmed from the BOTTOM of every emitted image, as a fraction of
    # that image's WIDTH, applied equally to the before and the after of a pair.
    #
    # A corner or edge watermark is CROPPED rather than tolerated or masked
    # (captain, 2026-08-19). Masking preserves the defect it is meant to remove
    # - the masked region is still present on whichever half carried the mark -
    # and where a mark falls on only one half of a pair it correlates perfectly
    # with the training label, so an edit model can satisfy "make the breasts
    # larger" by learning to add or remove a logo.
    #
    # Against WIDTH, and as a fraction, because that is how a burned-in mark is
    # actually drawn. arps publishes one "(c) Dr Eddie Cheng" mark across six
    # export sizes from 667x1000 to 1707x2560: measured against height its top
    # edge ranges over 2.5%-6% (the aspect ratios differ), against width it is
    # a much tighter 3.8%-7.2%, and in pixels it is nothing like constant. A
    # single pixel figure would either miss the mark on the large exports or
    # eat a sixth of the small ones.
    #
    # Measure it per clinic and never transfer another clinic's number; the
    # verification that matters is that the mark is GONE afterwards, not that
    # the arithmetic looked right. Both halves are cropped by the same amount:
    # a framing difference between before and after would be the same
    # correlated-with-the-label artifact in another form.
    bottom_crop_frac: float = 0.0


CLINICS: dict[str, ClinicConfig] = {
    "drkolker": ClinicConfig(
        slug="drkolker",
        consent_ref="drkolker-agreement-2026-08",
        base_url="https://drkolker.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"],
        kind="drkolker",
    ),
    "drdanielbarrett": ClinicConfig(
        slug="drdanielbarrett",
        consent_ref="barrett-agreement-2026-08",
        base_url="https://www.drdanielbarrett.com",
        gallery_paths=[
            # Captain's scope: ONLY the breast-augmentation-with-implants
            # section. The transgender section and every other procedure
            # section are explicitly excluded.
            "/los-angeles-before-after-photos/breast-augmentation-with-implants",
        ],
        kind="drdanielbarrett",
    ),
    "sanantonio": ClinicConfig(
        slug="sanantonio",
        consent_ref="sanantonio-agreement-2026-08",
        base_url="https://sanantonioplasticsurgery.com",
        gallery_paths=[
            # Captain's scope: ONLY the breast augmentation gallery. The
            # breast-augmentation-with-lift gallery and every other procedure
            # gallery are explicitly excluded.
            "/before-after-photos/breast-augmentation/",
        ],
        kind="sanantonio",
    ),
    # -- 2026-08 batch: 20 more consented clinics (CONSENTS-20-2026-08-11) --
    "harrington": ClinicConfig(
        slug="harrington", consent_ref="harrington-agreement-2026-08",
        base_url="https://www.harringtonplasticsurgery.com",
        gallery_paths=["/photo-gallery/breast-augmentation/"], kind="harrington"),
    "lakeshore": ClinicConfig(
        slug="lakeshore", consent_ref="lakeshore-agreement-2026-08",
        base_url="https://www.lakeshoreplasticsurgery.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="influx_swiper"),
    "drjeremyhunt": ClinicConfig(
        slug="drjeremyhunt", consent_ref="drjeremyhunt-agreement-2026-08",
        base_url="https://www.drjeremyhunt.com.au",
        gallery_paths=["/gallery/breast-surgery/breast-implants-gallery/"],
        kind="drjeremyhunt"),
    "allure": ClinicConfig(
        slug="allure", consent_ref="allure-agreement-2026-08",
        base_url="https://www.allureesthetic.com",
        gallery_paths=["/photos-and-stories/photo-gallery/breast-augmentation/"],
        kind="allure"),
    "drtavakoli": ClinicConfig(
        slug="drtavakoli", consent_ref="drtavakoli-agreement-2026-08",
        base_url="https://www.drtavakoli.com.au",
        gallery_paths=["/galleries/breast-augmentation-mammoplasty-round/"],
        kind="drtavakoli"),
    "sixsurgery": ClinicConfig(
        slug="sixsurgery", consent_ref="sixsurgery-agreement-2026-08",
        base_url="https://torontosurgery.com",
        gallery_paths=["/breast-augmentation-gallery"], kind="sixsurgery"),
    "drmiroshnik": ClinicConfig(
        slug="drmiroshnik", consent_ref="drmiroshnik-agreement-2026-08",
        base_url="https://www.drmiroshnik.com.au",
        gallery_paths=["/gallery/breast-augmentation-implants/"], kind="drmiroshnik"),
    "drrohrich": ClinicConfig(
        slug="drrohrich", consent_ref="drrohrich-agreement-2026-08",
        base_url="https://drrohrich.com",
        gallery_paths=["/photographs/breast-augmentation/"], kind="drrohrich"),
    "privateclinic": ClinicConfig(
        slug="privateclinic", consent_ref="privateclinic-agreement-2026-08",
        base_url="https://www.theprivateclinic.co.uk",
        # Category-scoped listing (not the guessed /before-after-photos/ URL,
        # which redirects into an unrelated single case): the only reliable
        # way to scope to augmentation-only, per recon.
        gallery_paths=["/cosmetic-surgery/breast-surgery/breast-augmentation/before-after-images/"],
        kind="privateclinic"),
    "mitchellbrown": ClinicConfig(
        slug="mitchellbrown", consent_ref="mitchellbrown-agreement-2026-08",
        # Canonicalizes to torontoplasticsurgery.com; images are cross-domain.
        base_url="https://www.torontoplasticsurgery.com",
        gallery_paths=["/gallery/breast-augmentation/"], kind="mitchellbrown"),
    "wny": ClinicConfig(
        slug="wny", consent_ref="wny-agreement-2026-08",
        base_url="https://www.wnyplasticsurgery.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="wny",
        # Caption burned into the AFTER image only - 11.5x asymmetry, and the
        # clinic this whole class of defect is named after in AGENTS.md. Mark top
        # measured 51px from the bottom; 60 leaves a margin. Costs 0 pairs to the
        # 400px floor - every half is 508px on its short side after the crop.
        # This crop is also what CLOSED the `wny-after-only-watermark` question
        # (captain, 2026-08-19): a corner or edge-band mark is cropped rather than
        # tolerated, so the mark itself and the label leak are settled together.
        bottom_crop_px=60),
    "austinweston": ClinicConfig(
        slug="austinweston", consent_ref="austinweston-agreement-2026-08",
        base_url="https://www.austin-weston.com",
        gallery_paths=["/before-after-photos-reston-va/breast/breast-augmentation/"],
        kind="austinweston"),
    "skplastic": ClinicConfig(
        slug="skplastic", consent_ref="skplastic-agreement-2026-08",
        base_url="https://www.skplasticsurgery.com",
        gallery_paths=["/before-and-after/breast-augmentation"], kind="skplastic"),
    "heavenly": ClinicConfig(
        slug="heavenly", consent_ref="heavenly-agreement-2026-08",
        base_url="https://www.heavenlyplasticsurgery.com",
        gallery_paths=["/photo-gallery/photo-gallery-breast/breast-augmentation-gallery/"],
        kind="heavenly"),
    "mya": ClinicConfig(
        slug="mya", consent_ref="mya-agreement-2026-08",
        base_url="https://www.mya.co.uk",
        gallery_paths=["/breast-procedures/breast-enlargement/before-and-after"], kind="mya"),
    "charlotte": ClinicConfig(
        slug="charlotte", consent_ref="charlotte-agreement-2026-08",
        base_url="https://www.charlotteplasticsurgery.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="charlotte"),
    "marina": ClinicConfig(
        slug="marina", consent_ref="marina-agreement-2026-08",
        base_url="https://www.marinaplasticsurgery.com",
        gallery_paths=["/before-after-gallery-los-angeles/breast-augmentation/"],
        kind="influx_swiper"),
    "drgrover": ClinicConfig(
        slug="drgrover", consent_ref="drgrover-agreement-2026-08",
        base_url="https://www.drgrover.com",
        gallery_paths=["/gallery/breast-procedures/breast-augmentation/"], kind="drgrover"),
    "basu": ClinicConfig(
        slug="basu", consent_ref="basu-agreement-2026-08",
        base_url="https://www.basuplasticsurgery.com",
        gallery_paths=["/gallery/breast-surgery/breast-augmentation/"], kind="basu"),
    "drteitelbaum": ClinicConfig(
        slug="drteitelbaum", consent_ref="drteitelbaum-agreement-2026-08",
        base_url="https://www.drteitelbaum.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="drteitelbaum"),
    # -- 2026-08-25 batch: prospected clinics consented 2026-08-25 --
    # clinic-corpus/CONSENT-2026-08-25-PROSPECTED-CLINICS.md, row 12.
    "aips": ClinicConfig(
        slug="aips", consent_ref="aips-agreement-2026-08-25",
        # www. is the canonical host; the bare domain 301s to it.
        base_url="https://www.aiplasticsurgery.com",
        # The practice publishes one gallery per procedure. Breast Lift,
        # Breast Lift + Implants, Breast Reduction and Breast Revision are
        # separate galleries and are all out of scope: the combined
        # augmentation-with-lift cases live under /breast-lift/photos/.
        gallery_paths=["/breast-augmentation/photos/"], kind="aips"),
    # -- 2026-08-25 batch: prospected clinics, consent executed 2026-08-25 --
    # (clinic-corpus/CONSENT-2026-08-25-PROSPECTED-CLINICS.md; section 2 grants
    # AI/ML use including model training and derivative works). That instrument
    # is a USE consent and grants no access, so the crawl still honours
    # robots.txt: mwps allows /gallery/ and publishes no crawl-delay.
    "mwps": ClinicConfig(
        slug="mwps", consent_ref="mwps-agreement-2026-08-25",
        base_url="https://www.mountainwestplasticsurgery.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="mwps"),
    # -- 2026-08-15 batch: 12 newly consented clinics, all Etna Interactive --
    # Consent executed 2026-08-15; recorded in clinic-corpus/CONSENT-STATUS.md
    # with the instrument filed beside it. Section 2 of that instrument grants
    # AI/ML use including model training and derivative works.
    # One parser (kind='etna') serves every one of them.
    "camp": ClinicConfig(
        slug="camp", consent_ref="camp-agreement-2026-08-15",
        base_url="https://www.campplasticsurgery.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="etna",
        endpoint_grant="clinic-corpus/CONSENT-ENDPOINT-GRANT-2026-08-18.md",
        # "STEVEN CAMP MD PLASTIC SURGERY", bottom-right, on the AFTER half only
        # - 7.4x asymmetry. Mark top measured 98px from the bottom on the tall
        # groups and 81px on the 478px group; 110 covers both with a margin.
        bottom_crop_px=110),
    "colville": ClinicConfig(
        slug="colville", consent_ref="colville-agreement-2026-08-15",
        base_url="https://www.craigcolvillemd.com",
        gallery_paths=["/photo-gallery/breast-procedures/breast-augmentation/"],
        kind="etna", endpoint_grant="clinic-corpus/CONSENT-ENDPOINT-GRANT-2026-08-18.md"),
    "roth": ClinicConfig(
        slug="roth", consent_ref="roth-agreement-2026-08-15",
        base_url="https://www.jjrothmd.com",
        gallery_paths=["/before-after/breast/breast-augmentation/"], kind="etna",
        # "Jeffrey J. Roth, M.D., F.A.C.S." script wordmark, bottom-right, on the
        # AFTER half only - 16.9x high-pass-average asymmetry, the strongest in
        # the corpus. Mark top measured 162px from the bottom across every height
        # group; 175 leaves a margin. Costs 0 pairs to the 400px floor.
        bottom_crop_px=175),
    "kochcarlisle": ClinicConfig(
        slug="kochcarlisle", consent_ref="kochcarlisle-agreement-2026-08-15",
        base_url="https://www.kochandcarlisle.com",
        gallery_paths=["/photo-gallery/breast-procedures/breast-augmentation/"],
        kind="etna", endpoint_grant="clinic-corpus/CONSENT-ENDPOINT-GRANT-2026-08-18.md"),
    "wmips": ClinicConfig(
        slug="wmips", consent_ref="wmips-agreement-2026-08-15",
        base_url="https://www.wmips.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="etna"),
    "southeastern": ClinicConfig(
        slug="southeastern", consent_ref="southeastern-agreement-2026-08-15",
        base_url="https://www.se-plasticsurgery.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="etna"),
    "northraleigh": ClinicConfig(
        slug="northraleigh", consent_ref="northraleigh-agreement-2026-08-15",
        base_url="https://www.northraleighplasticsurgery.com",
        gallery_paths=["/before-and-after/breast/breast-augmentation/"], kind="etna"),
    "ablavsky": ClinicConfig(
        slug="ablavsky", consent_ref="ablavsky-agreement-2026-08-15",
        base_url="https://www.ablavskyplasticsurgery.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="etna",
        endpoint_grant="clinic-corpus/CONSENT-ENDPOINT-GRANT-2026-08-18.md"),
    "hasen": ClinicConfig(
        slug="hasen", consent_ref="hasen-agreement-2026-08-15",
        base_url="https://www.drhasen.com",
        gallery_paths=["/gallery/breast-enhancement/breast-augmentation/"], kind="etna"),
    "curtsinger": ClinicConfig(
        slug="curtsinger", consent_ref="curtsinger-agreement-2026-08-15",
        base_url="https://www.lukecurtsingermd.com",
        gallery_paths=["/gallery/plastic-surgery/breast-augmentation/"], kind="etna"),
    "coastal": ClinicConfig(
        slug="coastal", consent_ref="coastal-agreement-2026-08-15",
        base_url="https://www.bostoncoastalplasticsurgery.com",
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="etna"),
    # thecenterforcosmeticsurgery.net is the only one of the twelve that does
    # not serve the Etna AI-access robots block. Its robots.txt is a
    # Cloudflare-managed default carrying 'User-agent: ClaudeBot / Disallow: /'
    # and 'Content-Signal: ai-train=no'. That was escalated rather than decided
    # here, and the captain ruled on 2026-08-15 to collect: the practice holds
    # executed AI-training consent as the rights holder, and its website's
    # machine signal was simply never updated to match. Collection still runs
    # under the same politeness contract as the other eleven - 2s delay, the
    # descriptive clinic-corpus-scraper UA (which the site's 'User-agent: *'
    # group allows). The admin-ajax gallery endpoint was off limits on that run
    # and is now open under the 2026-08-18 grant recorded in `endpoint_grant`
    # below, which is a separate permission from the consent instrument.
    # -- 2026-08-25 batch: prospected clinics consented 2026-08-25 --
    # clinic-corpus/CONSENT-2026-08-25-PROSPECTED-CLINICS.md, row 13.
    "choice": ClinicConfig(
        slug="choice", consent_ref="choice-agreement-2026-08-25",
        base_url="https://www.choiceaesthetics.uk",
        gallery_paths=["/gallery/breast/breast-augmentation"], kind="choice",
        # The composite's caption band prints BEFORE under the left half and
        # AFTER under the right. Measured over all 52 published composites:
        # the band starts 50-53px from the bottom and its gold text tops out
        # at 53px; 60 clears both with margin on every one of them, leaving
        # 455x441 halves that stay above ingest.py's 400px floor.
        bottom_crop_px=60),
    "tccs": ClinicConfig(
        slug="tccs", consent_ref="tccs-agreement-2026-08-15",
        base_url="https://www.thecenterforcosmeticsurgery.net",
        gallery_paths=["/gallery/breast-surgery/breast-augmentation/"], kind="etna",
        endpoint_grant="clinic-corpus/CONSENT-ENDPOINT-GRANT-2026-08-18.md",
        # 130px clears the logo on 742 of 743 measured before halves (its top
        # edge sits 122px from the bottom at the median, 124px at p95) with a
        # small margin. See ClinicConfig.bottom_crop_px for why this matters.
        bottom_crop_px=130),
    # -- 2026-08-25 batch: prospected clinics with executed AI-training consent
    #    (CONSENT-2026-08-25-PROSPECTED-CLINICS.md) --
    # sculpted.com's robots.txt (last modified 2023-11-22) names GPTBot,
    # ChatGPT-User, CCBot, anthropic-ai, Claude-Web and Google-Extended and
    # disallows each; it publishes NO 'User-agent: *' group, so it states no
    # rule for this scraper's own descriptive UA, and no Crawl-delay. Neither
    # the robots file nor any response header carries a Content-Signal.
    "sculpted": ClinicConfig(
        slug="sculpted", consent_ref="sculpted-agreement-2026-08-25",
        base_url="https://sculpted.com",
        gallery_paths=["/gallery/breast-implants/"], kind="sculpted"),
    # -- 2026-08-25 batch: prospected clinics, consent executed 2026-08-25 --
    # (CONSENT-2026-08-25-PROSPECTED-CLINICS.md). One clinic per collection
    # task; the Page 1 Solutions family parser lives in page1solutions.py.
    "bandy": ClinicConfig(
        slug="bandy", consent_ref="bandy-agreement-2026-08-25",
        base_url="https://www.drbandy.com",
        gallery_paths=["/before-after-photos/breast-augmentation/"],
        kind="page1solutions"),
    # 2026-08-25 batch (CONSENT-2026-08-25-PROSPECTED-CLINICS.md). Parser and
    # every gallery-specific constant live in arps_gallery.py.
    #
    # gallery_paths is informational for this clinic: the walk is paginated and
    # scoped to the consent signatory's own cases, so arps_gallery builds each
    # listing URL itself (arps_listing_url) rather than fetching this path.
    "arps": ClinicConfig(
        slug="arps", consent_ref="arps-agreement-2026-08-25",
        base_url="https://arplasticsurgery.com.au",
        gallery_paths=["/breast-augmentation-gallery/"], kind="arps",
        # "(c) Dr Eddie Cheng" (some exports "(c) DR Eddie Cheng AR Plastic
        # Surgery") burned into the bottom band of EVERY image - both halves of
        # every pair, so it is not a label leak, but a corner or edge mark is
        # cropped rather than tolerated (captain, 2026-08-19). Measured on the
        # per-size mean of all 186 published images: the mark's top edge sits
        # 3.8%-7.2% of the frame's width above the bottom across the six export
        # sizes; 10% clears the worst by a third. Verified by looking at the
        # cropped bottom edge of all 186, and by the mean's high-pass peak in
        # the bottom band falling from 3.4x-28x the body baseline to ~1x.
        # Costs 0 pairs to the 400px floor: every image is portrait, so the
        # short edge is the width and a bottom crop does not touch it.
        bottom_crop_frac=0.10),
    # -- 2026-08-25 batch: consent executed for 19 prospected clinics; see
    # clinic-corpus/CONSENT-2026-08-25-PROSPECTED-CLINICS.md (Blaine is row 8).
    "blaine": ClinicConfig(
        slug="blaine", consent_ref="blaine-agreement-2026-08-25",
        base_url="https://blaineplasticsurgery.com",
        gallery_paths=["/before-and-after/breast-procedures/breast-augmentation/"],
        kind="blaine"),
    # -- 2026-08-25 batch: clinics consented in
    # clinic-corpus/CONSENT-2026-08-25-PROSPECTED-CLINICS.md --
    # Page 1 Solutions platform; scripts/page1_solutions.py is the parser.
    "ncps": ClinicConfig(
        slug="ncps", consent_ref="ncps-agreement-2026-08-25",
        # drgregpark.com 301s to the www host, which is also where /files/
        # serves the images; base_url points at the canonical host directly.
        base_url="https://www.drgregpark.com",
        gallery_paths=["/before-after-gallery-san-diego/breast-augmentation/"],
        kind="page1"),
}


# ---------------------------------------------------------------------------
# Shared data model
# ---------------------------------------------------------------------------


@dataclass
class CaseSpecs:
    """Structured implant/patient specs parsed from a case's text."""

    summary: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    age: int | None = None
    gender: str = ""
    height: str = ""
    weight_lbs: int | None = None
    left_cc: float | None = None
    right_cc: float | None = None
    brand: str = "unknown"
    shape: str | None = None
    profile: str | None = None
    months_post_op: float | None = None
    # Optional chart/frame fields (ingest.py VALID_PLACEMENTS/VALID_INCISIONS,
    # NUMERIC_RANGES). Set only by a parser that read them off a spec chart, so
    # 'None' means undocumented rather than 'unknown'. height_cm/weight_kg are
    # schema-ready numbers rather than the verbatim height/weight above,
    # because only the parser knows whether the clinic published a single
    # figure or a bucket - sixsurgery publishes '100 - 149 lbs', and turning
    # that range floor into a weight_kg would invent a value it never stated.
    placement: str | None = None
    incision: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None


@dataclass
class ImagePair:
    key: str  # pair key within the case, e.g. 'front'/'oblique'/'side' or 'pair1'
    before_url: str
    after_url: str
    view_hint: str | None = None  # page-documented view label, if any
    # True when before_url/after_url point at the same side-by-side composite
    # image (left half before, right half after) that must be split on save.
    split_composite: bool = False
    # Fraction of the composite's HEIGHT to trim off its BOTTOM before
    # splitting, for a gallery that burns an edge watermark there (mwps).
    # A fraction rather than a pixel count because the mark is rendered at a
    # fixed proportion of the frame, not a fixed size. Applied to the whole
    # composite, so both halves are cropped identically and stay dimension- and
    # framing-matched; a watermark centred on the split seam lands on both
    # halves, so cropping one side only would be a label leak.
    composite_bottom_frac: float = 0.0
    # Set (with before_url == after_url) when before_url/after_url point at a
    # shared multi-panel grid composite (e.g. drrohrich's 2x2, teitelbaum's
    # 2x3): grid_shape is (rows, cols); before_cell/after_cell are (row, col)
    # of this pair's two panels within that grid.
    grid_shape: tuple[int, int] | None = None
    before_cell: tuple[int, int] | None = None
    after_cell: tuple[int, int] | None = None
    # A split_composite whose two halves are laid out inside a printed
    # presentation frame: composite_border px of that frame on all four outer
    # edges and composite_gutter px on EACH side of the midpoint, trimmed
    # before the split. Both halves lose the same amount, so their dimensions
    # stay matched. 0/0 (the default) splits the raw image, which is what every
    # composite clinic before sculpted publishes.
    composite_border: int = 0
    composite_gutter: int = 0


@dataclass
class CaseData:
    case_id: str
    source_url: str
    pairs: list[ImagePair] = field(default_factory=list)
    specs: CaseSpecs = field(default_factory=CaseSpecs)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fetching (polite, cached, sequential)
# ---------------------------------------------------------------------------


# A long walk over one host eventually meets a transient refusal - a reset, a
# read timeout, or a 429/5xx from an edge that has decided we are going too
# fast. Retrying a few times with a widening pause is both the robust and the
# polite response; without it a single reset ends a multi-hundred-page
# enumeration and the shortfall looks like missing data rather than a blip.
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
# A CDN saying this one image is not there. Anything else - a 403 from a WAF, a
# 5xx that outlived the retries - is the site refusing the run, not a gap in the
# gallery, and must not be tolerated per pair.
MISSING_STATUS = {404, 410}


class PoliteFetcher:
    def __init__(self, cache_dir: Path, delay: float = 2.0, offline: bool = False):
        self.cache_dir = cache_dir
        self.delay = delay
        self.offline = offline
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request = 0.0
        self.requests_made = 0
        self.retries_made = 0

    def _sleep_until_allowed(self, extra: float = 0.0) -> None:
        wait = self.delay + extra - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, cache_key: str) -> bytes:
        return self._request("GET", url, cache_key, None)

    def is_cached(self, cache_key: str) -> bool:
        """Whether the next fetch of `cache_key` would be a replay, not a fetch.

        A caller that reconciles against a number READ from a response needs to
        know which it got: a replayed entry is as old as the cache, and a count
        checked against it is verified against a snapshot rather than against
        the site as it stands now.
        """
        return (self.cache_dir / cache_key).exists()

    def post_form(self, url: str, fields: dict[str, str], cache_key: str,
                  delay: float | None = None,
                  validate=None, retry_waits: tuple[float, ...] = ()) -> bytes:
        """Cached multipart POST, on the same politeness contract as `get`.

        Only the gallery case-list endpoint uses this, and only for a clinic
        whose `ClinicConfig.endpoint_grant` names the site owner's grant. It is
        a POST because that endpoint only answers one: the gallery's own script
        sends `action`/`first_returned_position`/`case_count` as `FormData`
        alongside the listing's filter form, and a GET of the same parameters
        returns the WordPress admin-ajax `0` body rather than a result set.

        `delay` raises the inter-request floor above the run's own. The endpoint
        is a database query behind an admin path rather than a static page, and
        the grant that opened it asks for a courteous pace in exchange, so it is
        fetched more slowly than the case pages are.

        `validate` is called on freshly fetched bytes BEFORE they are cached, and
        may raise to reject them. That ordering is the point: this endpoint
        answers 200 with an error page when its backend fails, and caching that
        would replay the failure as data on every later run. A rejected response
        is retried on the `retry_waits` schedule and, if every attempt is
        rejected, the exception is raised rather than swallowed.
        """
        return self._request("POST", url, cache_key, fields, delay=delay,
                             validate=validate, retry_waits=retry_waits)

    def _request(self, method: str, url: str, cache_key: str,
                 fields: dict[str, str] | None,
                 delay: float | None = None,
                 validate=None, retry_waits: tuple[float, ...] = ()) -> bytes:
        path = self.cache_dir / cache_key
        if path.exists():
            data = path.read_bytes()
            if validate is not None:
                validate(data)
            return data
        if self.offline:
            raise FileNotFoundError(f"offline mode and no cache entry for {url}")
        floor = self.delay if delay is None else max(self.delay, delay)
        backoff = floor - self.delay
        # The two retry allowances are counted separately, because they answer
        # to different failures on different schedules. A transport blip sharing
        # one counter with the validate path spent a validate retry that never
        # happened and made the FIRST rejected body back off at retry_waits[1]
        # instead of retry_waits[0] - a schedule the caller never asked for.
        transport_failures = 0
        validate_failures = 0
        while True:
            self._sleep_until_allowed(backoff)
            try:
                if method == "POST":
                    resp = self.session.post(
                        url, files={k: (None, v) for k, v in (fields or {}).items()},
                        timeout=180)
                else:
                    resp = self.session.get(url, timeout=60)
                self._last_request = time.monotonic()
                self.requests_made += 1
                if (resp.status_code in RETRY_STATUS
                        and transport_failures < MAX_ATTEMPTS - 1):
                    raise requests.exceptions.RetryError(
                        f"HTTP {resp.status_code}")
                resp.raise_for_status()
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.RetryError) as exc:
                self._last_request = time.monotonic()
                transport_failures += 1
                if transport_failures >= MAX_ATTEMPTS:
                    raise
                self.retries_made += 1
                backoff = max(5.0, floor - self.delay,
                              self.delay * 2 ** transport_failures)
                print(f"  retry {transport_failures}/{MAX_ATTEMPTS - 1} in "
                      f"{backoff:.0f}s after {type(exc).__name__} on {url}")
                continue
            data = resp.content
            if validate is not None:
                try:
                    validate(data)
                except Exception as exc:
                    # NOT cached: a rejected body is a failure, and a cached
                    # failure is replayed as data forever.
                    if validate_failures >= len(retry_waits):
                        raise
                    wait = retry_waits[validate_failures]
                    validate_failures += 1
                    self.retries_made += 1
                    backoff = wait - self.delay
                    print(f"  retry {validate_failures}/{len(retry_waits)} in "
                          f"{wait:.0f}s after "
                          f"{type(exc).__name__} on {url}: {exc}")
                    continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data


def crop_bottom(data: bytes, rows: int) -> bytes:
    """Trim `rows` pixels off the bottom of an encoded image.

    Re-encodes, which is what every stage of this pipeline already does; the
    corpus's last-mile builder re-encodes again on the way out.
    """
    from PIL import Image

    if rows <= 0:
        return data
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if rows >= im.height:
            raise ValueError(f"bottom crop of {rows}px exceeds image height {im.height}")
        out = im.crop((0, 0, im.width, im.height - rows))
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=95)
        return buf.getvalue()


def emitted_image_name(stem: str, url: str, bottom_crop_px: int) -> str:
    """The filename one half of a pair is written under.

    An uncropped half keeps the source URL's own extension, because this corpus
    legitimately mixes .jpg/.jpeg/.png/.webp and a scan that globs `before.*`
    depends on the name being true. A CROPPED half cannot: `crop_bottom` re-encodes
    to JPEG, so writing it under the source's extension would put JPEG bytes in a
    `before.webp`. The extension follows the encoding, never the source.
    """
    if bottom_crop_px:
        return f"{stem}.jpg"
    ext = Path(urlsplit(url).path).suffix or ".jpg"
    return f"{stem}{ext.lower()}"


def image_cache_key(clinic: str, url: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    name = re.sub(r"[^\w.()-]+", "_", name)
    digest = hashlib.sha256(url.encode()).hexdigest()[:8]
    return f"images/{clinic}/{urlsplit(url).netloc}_{digest}_{name}"


# ---------------------------------------------------------------------------
# Shared spec helpers
# ---------------------------------------------------------------------------


# Volumes may be published in cc or in grams. Anatomical/shaped implants are
# specified in grams by their manufacturers, cohesive gel density is ~0.97 g/cc
# (a smaller error than the averaging volume_cc() already applies to asymmetric
# pairs), and clinics use the two units interchangeably for the same implant -
# drmiroshnik case64 reads '255cc' in prose and is published as '255g-...jpg'.
# A gram figure is therefore recorded as volume_cc unconverted. Captain ruling
# 2026-08-14 (`ba-viz-emit-backlog` report section 2), same class as the
# Natrelle model-number decoder.
# The trailing \b is what makes a bare 'g' safe to accept, but the unit is often
# pluralised ('filled to 300ccs', 'Silicone gels 270ccs.'), so every spelling
# takes an optional 's' - without it drrohrich and charlotte silently lose the
# cc figures they have always parsed. Longest alternatives first.
#
# Grams also measure what a combined procedure REMOVED, which is not an implant
# volume: sanantonio case 24139 reads '...came to the 457cc implant... 442 grams
# of tissue plus 225 mL of lipoaspirate was removed', and counting the 442 makes
# volume_cc the 450 average of two unrelated numbers. Gram figures qualified as
# excised tissue are therefore not volumes. 'cc' needs no such guard - clinics
# describe excised tissue in grams and mL, never in cc.
_GRAM_UNIT = r"(?:grams?|gms?|grs?|gs?)\b(?!\s*(?:of\s+)?(?:tissue|fat|skin|lipoaspirate))"
VOLUME_UNIT = rf"(?:ccs?\b|{_GRAM_UNIT})"


# 'on the right', 'on her right', 'on right', 'in the right', '355cc for the
# right' - the phrasings clinics actually use to attach a volume to a side in
# narrative prose. The side word must not be followed by another word that
# makes it an adjective ('on the right side' is a side marker, 'on the right
# track' is not).
#
# The second alternative is the side word attached directly to the noun:
# '... a 480cc in the smaller right breast and 390cc implant in the larger
# left breast' (choice). The determiner form cannot reach that, because an
# adjective sits between 'the' and the side word - and widening the
# determiner form to skip an arbitrary word would also swallow 'on the way
# left'. Requiring the noun 'breast' is the tighter reading anyway: it is
# exactly the ambiguity the sentence-break cut below exists to guard against,
# since a bare 'on the right' often points at the right-hand PHOTOGRAPH.
#
# The two alternatives are one regex rather than two passes so the markers are
# visited in text order, which is what lets each one read only the text since
# the previous one. A clinic routinely writes both forms in one sentence:
# charlotte 33's '350cc for the right breast, 360cc for the left' needs the
# noun form for the first volume and the determiner form for the second.
TRAILING_SIDE_RE = re.compile(
    r"\b(?:(?:on|in|for)\s+(?:the\s+|her\s+|his\s+)?(left|right)\b"
    r"|(left|right)\s+breasts?\b)", re.I)


def _parse_fill_side(segment: str) -> float | None:
    """Final volume for one breast from '270 filled to 285cc' or '185cc'/'185g'."""
    m = re.search(rf"filled\s+to\s*(\d+(?:\.\d+)?)\s*{VOLUME_UNIT}", segment, re.I)
    if m:
        return float(m.group(1))
    m = re.search(rf"(\d+(?:\.\d+)?)\s*{VOLUME_UNIT}", segment, re.I)
    return float(m.group(1)) if m else None


# Implant volumes only: schema bounds are 100-1000cc. Thousands-grouped numbers
# ('1,600cc of fat' from combined lipo cases) are parsed in full and then
# dropped by the range filter so they never pollute implant volumes.
CC_RE = re.compile(rf"(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s*{VOLUME_UNIT}", re.I)


def _cc_numbers(text: str) -> list[float]:
    values = []
    for m in CC_RE.finditer(text):
        cc = float(m.group(1).replace(",", ""))
        if 100 <= cc <= 1000:
            values.append(cc)
    return values


def parse_fill_volumes(text: str) -> tuple[float | None, float | None]:
    """(left_cc, right_cc) from fill text.

    Handles: 'Right: 185cc Mini Motiva; Left: 170cc Mini Motiva',
    'L 240cc, R 265cc', 'R 270 filled to 285cc, L 300 filled to 285cc',
    '300cc (R) 270cc (L)', '350cc on the right. 325cc on the left.',
    and symmetric values like '270cc saline implants' or '250cc Bilateral ...'.
    """
    left = right = None

    def assign(side: str, cc: float) -> None:
        nonlocal left, right
        if side.startswith("l"):
            left = cc
        else:
            right = cc

    # Suffix markers: '300cc (R)', '320 (L)', '350cc on the right'.
    for m in re.finditer(
            rf"(\d+(?:\.\d+)?)\s*{VOLUME_UNIT}\s*(?:on the\s+)?\((left|right|l|r)\)", text, re.I):
        assign(m.group(2).lower(), float(m.group(1)))
    for m in re.finditer(r"(\d{3})\s*\((left|right|l|r)\)", text, re.I):
        assign(m.group(2).lower(), float(m.group(1)))
    # Trailing side markers with words between the volume and the side:
    # '385cc gummy bear implant on her right', '330cc gel on right',
    # '360 cc breast implant in the right', '300cc filled to 340cc on the right
    # side'. Each marker takes the text since the PREVIOUS marker as its
    # segment, so the second side reads its own volume instead of re-reading
    # the first one; _parse_fill_side then prefers a 'filled to' final volume
    # over the shell size quoted beside it.
    # The segment is also cut at the previous sentence break, because 'on the
    # right' often points at the right-hand PHOTOGRAPH rather than the right
    # breast: drrohrich 96 reads '...using 275 cc saline filled implants with a
    # breast fold incision. She is show here 3 years post operatively on the
    # right.' Without the cut that sentence steals the volume onto one side and
    # loses the bilateral reading.
    prev_end = 0
    for m in TRAILING_SIDE_RE.finditer(text):
        segment = re.split(r"[.;]", text[prev_end:m.start()])[-1]
        prev_end = m.end()
        cc = _parse_fill_side(segment)
        if cc is not None and 100 <= cc <= 1000:
            assign((m.group(1) or m.group(2)).lower(), cc)
    # Prefix markers: 'Right: 185cc ...', 'R 270 filled to 285cc'.
    if left is None and right is None:
        side_re = re.compile(r"\b(left|right|l|r)\b\s*:?\s*([^;,.]*)", re.I)
        for m in side_re.finditer(text):
            side, segment = m.group(1).lower(), m.group(2)
            cc = _parse_fill_side(segment)
            if cc is not None:
                assign(side, cc)
    if left is None and right is None:
        ccs = _cc_numbers(text)
        if len(ccs) == 1:
            left = right = ccs[0]
        elif len(ccs) >= 2:
            # Narrative order is not guaranteed to be left/right; record both
            # without assigning sides.
            left, right = ccs[0], ccs[1]
    return left, right


def classify_brand_shape_profile(specs: CaseSpecs, haystack: str) -> None:
    lower = haystack.lower()
    for keyword, brand in BRAND_KEYWORDS:
        if keyword in BRAND_WORD_ONLY:
            hit = re.search(rf"\b{re.escape(keyword)}\b", lower) is not None
        else:
            hit = keyword in lower
        if hit:
            specs.brand = brand
            break
    if re.search(r"\bround\b", lower):
        specs.shape = "round"
    # 'anatomic' with a trailing \b never matched the word clinics actually use:
    # 'anatomical'. 52 of drmiroshnik's 146 cases were recorded shape=unknown
    # because of it. Captain ruling 2026-08-14.
    elif re.search(r"\b(teardrop|anatomic\w*|shaped)\b", lower):
        specs.shape = "teardrop"
    for pattern, profile in PROFILE_PATTERNS:
        if pattern.search(haystack):
            specs.profile = profile
            break
    # Same anchoring as the brand loop above, and for the same reason: this
    # gate is what turns a false Motiva into a fabricated profile, because
    # MOTIVA_PROFILE_PATTERNS decodes the bare words 'full'/'demi'/'mini'.
    if specs.profile is None and re.search(r"\bmotiva\b", lower):
        for pattern, profile in MOTIVA_PROFILE_PATTERNS:
            if pattern.search(haystack):
                specs.profile = profile
                break


def classify_placement_incision(specs: CaseSpecs, chart_text: str) -> None:
    """Set specs.placement/incision from a case's CHART text.

    Chart text only - never the clinic's narrative. Marina's prose explains the
    options ('behind the muscle (dual plane) or over the pectoral muscle
    (subglandular placement)') before naming the one it used, so a keyword hit
    in narrative is not evidence of what this patient received.
    """
    for pattern, placement in PLACEMENT_PATTERNS:
        if pattern.search(chart_text):
            specs.placement = placement
            break
    for pattern, incision in INCISION_PATTERNS:
        if pattern.search(chart_text):
            specs.incision = incision
            break


# 5'3, 5'3", 5’10 - the only height spellings in the corpus that state a single
# unambiguous figure. A range ('5.0” - 5.5”', sixsurgery) or a bare number with
# no documented unit deliberately does not convert.
FEET_INCHES_RE = re.compile(r"^(\d)\s*['’]\s*(\d{1,2})?\s*(?:\"|”|''|in\.?)?$")


def height_to_cm(height: str) -> float | None:
    """Schema height_cm from a verbatim feet/inches height, or None."""
    m = FEET_INCHES_RE.match(height.strip())
    if m is None:
        return None
    inches = int(m.group(1)) * 12 + int(m.group(2) or 0)
    cm = round(inches * 2.54, 1)
    return cm if 120 <= cm <= 220 else None


def inches_to_cm(height_inches: str) -> float | None:
    """Schema height_cm from a bare inches figure, or None.

    mwps's chart publishes height as a plain integer number of inches
    ('Height: 65'), which its own narrative spells out ('standing 62 inches
    tall'); height_to_cm's feet/inches form cannot read that.
    """
    m = re.fullmatch(r"\s*(\d{2,3})(?:\s*(?:in|inches|\"))?\s*", height_inches)
    if m is None:
        return None
    cm = round(int(m.group(1)) * 2.54, 1)
    return cm if 120 <= cm <= 220 else None


def pounds_to_kg(weight_lbs: float) -> float | None:
    """Schema weight_kg from a pounds figure, or None if out of schema range."""
    kg = round(weight_lbs * 0.45359237, 1)
    return kg if 30 <= kg <= 250 else None


def volume_cc(specs: CaseSpecs) -> int | None:
    """Average final volume, per schema ('record the average and note it')."""
    values = [v for v in (specs.left_cc, specs.right_cc) if v is not None]
    if not values:
        return None
    return int(round(sum(values) / len(values)))


# ---------------------------------------------------------------------------
# drkolker parser
# ---------------------------------------------------------------------------

KOLKER_GALLERY_PATH = "/gallery/breast/breast-augmentation/"
KOLKER_VIEW_ALT_RE = re.compile(
    r"(Before|After)\s+Image\s+Patient\s+\w+\s+(Front|Oblique|Side)\s+View", re.I
)


def kolker_list_cases(listing_html: str) -> list[str]:
    """Zero-padded case directory names from the gallery listing page."""
    cases = set(re.findall(re.escape(KOLKER_GALLERY_PATH) + r"(\d+)/", listing_html))
    return sorted(cases, key=int)


def kolker_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    case = CaseData(case_id=case_id, source_url=source_url)

    # The thumbnail strip (div.swiper-container-thumbs) duplicates every image
    # with buggy alt text (all 'After'); only the main gallery container is
    # authoritative for the before/after split.
    soup = BeautifulSoup(case_html, "html.parser")
    gallery = soup.select_one("div.procedure-gallery__swiper-gallery")
    views: dict[str, dict[str, str]] = {}
    if gallery is not None:
        for img in gallery.find_all("img"):
            src = img.get("src", "")
            m = KOLKER_VIEW_ALT_RE.search(img.get("alt", ""))
            if not m or f"{KOLKER_GALLERY_PATH}{case_id}/" not in src:
                continue
            phase, view = m.group(1).lower(), m.group(2).lower()
            views.setdefault(view, {}).setdefault(phase, src)
    for view in ("front", "oblique", "side"):
        phases = views.get(view, {})
        if "before" in phases and "after" in phases:
            case.pairs.append(ImagePair(key=view, before_url=phases["before"],
                                        after_url=phases["after"], view_hint=view))
        elif phases:
            case.warnings.append(f"view {view}: missing before or after image")

    specs = CaseSpecs()
    idx = case_html.find('<div class="patient-details')
    if idx >= 0:
        block = BeautifulSoup(case_html[idx : idx + 6000], "html.parser")
        container = block.find("div", class_="patient-details")
        if container is not None:
            for p in container.find_all("p"):
                strong = p.find("strong")
                if strong:
                    label = strong.get_text(strip=True).rstrip(":")
                    value = p.get_text(strip=True)
                    value = value[len(strong.get_text(strip=True)) :].strip()
                    specs.fields[label] = value
                elif not specs.summary:
                    text = p.get_text(strip=True)
                    if text:
                        specs.summary = text

    haystack = " ".join([specs.summary, *specs.fields.values()])
    m = re.search(r"(\d+)-year-old", specs.summary)
    if m:
        specs.age = int(m.group(1))
    age_field = specs.fields.get("Patient Age", "")
    if specs.age is None and age_field.isdigit():
        specs.age = int(age_field)
    m = re.search(r"\((\d+['’]\d+\s*(?:[\"”])?)\s*,\s*(\d+)\s*lbs", specs.summary)
    if m:
        specs.height = m.group(1).replace("’", "'").replace("”", '"').strip()
        specs.weight_lbs = int(m.group(2))

    fill = specs.fields.get("Breast Implant fill", "")
    if not fill:
        # Some cases use other labels (e.g. 'Implants: 140cc bilaterally').
        fill = next((v for k, v in specs.fields.items()
                     if "implant" in k.lower() and re.search(r"\d+\s*cc", v)), "")
    if fill:
        specs.left_cc, specs.right_cc = parse_fill_volumes(fill)
    if specs.left_cc is None and specs.right_cc is None and specs.summary:
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)

    classify_brand_shape_profile(specs, haystack)
    case.specs = specs
    return case


# ---------------------------------------------------------------------------
# drdanielbarrett parser
# ---------------------------------------------------------------------------


def _barrett_image_index(filename: str, case_id: str) -> int | None:
    """Image sequence index from a Barrett CDN filename.

    Observed conventions: 'Patient #69212 (3).webp' -> 3, '50342 BAM (5).jpg'
    -> 5, '50342 BAM.jpg' -> None (base image), '11.webp' -> 11, '665421.webp'
    (case id + 1 digit) -> 1. Bare long numbers that are not the case id
    ('10924.webp' under case 42901) are base images too -> None. The caller
    assigns index 1 to at most one None (base) image.
    """
    name = unquote(filename)
    m = re.search(r"\((\d+)\)", name)
    if m:
        return int(m.group(1))
    stem = name.rsplit(".", 1)[0].strip()
    m = re.search(r"(\d+)$", stem)
    if not m:
        return None
    digits = m.group(1)
    if digits == case_id:
        return None  # bare 'Patient #<case>.webp' base image
    if digits.startswith(case_id) and len(digits) > len(case_id):
        return int(digits[len(case_id) :])
    if len(digits) <= 2:
        return int(digits)
    return None  # long bare number unrelated to the sequence -> base image


def barrett_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """All case items embedded in one Barrett category page."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for item in soup.select("div.before-after_items"):
        case_el = item.select_one(".before-after_details-case")
        if case_el is None:
            continue
        m = re.search(r"(\d+)", case_el.get_text(" ", strip=True))
        if not m:
            continue
        case = CaseData(case_id=m.group(1), source_url=source_url)

        # --- specs ---
        specs = CaseSpecs()
        for lst in item.select(".before-after_details-list"):
            text = lst.get_text(" ", strip=True)
            if not text:
                continue
            if m_age := re.match(r"(\d+)\s*year old", text, re.I):
                specs.age = int(m_age.group(1))
            elif text.lower() in ("female", "male"):
                specs.gender = text
            elif re.match(r"^\d+['’]\d+", text):
                specs.height = text.replace("’", "'")
            elif m_w := re.match(r"(\d+)\s*lbs", text, re.I):
                specs.weight_lbs = int(m_w.group(1))
            else:
                specs.fields.setdefault("Other", text)
        details_el = item.select_one(".before-after_details-list-details")
        if details_el is not None:
            specs.summary = details_el.get_text(" ", strip=True)
        if specs.summary:
            specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
        classify_brand_shape_profile(specs, specs.summary)
        procedures = list(dict.fromkeys(
            p.get_text(" ", strip=True) for p in
            item.select(".before-after_procedures-list .w-dyn-item p")))
        if procedures:
            specs.fields["Procedures Performed"] = "; ".join(procedures)
        case.specs = specs

        # --- images: unique URLs in document order ---
        urls: list[str] = []
        for img in item.find_all("img"):
            if (img.get("alt") or "").isdigit() and img.get("src"):
                if img["src"] not in urls:
                    urls.append(img["src"])
        # Explicit before/after roles from the tagged thumbnail slider.
        tagged: dict[str, str] = {}  # url -> 'before'|'after'
        thumb = item.select_one("div.slider.w-slider")
        if thumb is not None:
            for slide in thumb.select(".w-slide"):
                img, tag = slide.find("img"), slide.select_one(".tag")
                if img is not None and tag is not None and img.get("src"):
                    role = tag.get_text(strip=True).lower()
                    if role in ("before", "after"):
                        tagged[img["src"]] = role

        indexed = [(_barrett_image_index(u.rsplit("/", 1)[-1], case.case_id), u)
                   for u in urls]
        # At most one base (index-less) image per case takes sequence index 1.
        taken = {i for i, _ in indexed if i is not None}
        resolved = []
        for idx, url in indexed:
            if idx is None:
                if 1 not in taken:
                    idx, taken = 1, taken | {1}
                else:
                    idx = None  # second base image: sequence ambiguous
            resolved.append((idx, url))
        indices = [i for i, _ in resolved]
        clean = (len(urls) >= 2 and len(urls) % 2 == 0
                 and None not in indices and len(set(indices)) == len(indices))
        pairs: list[tuple[str, str]] = []
        if clean:
            ordered = [u for _, u in sorted(resolved, key=lambda t: t[0])]
            pairs = list(zip(ordered[::2], ordered[1::2]))
            # Verify against the explicitly tagged thumbnails.
            role_of = {}
            for before, after in pairs:
                role_of[before], role_of[after] = "before", "after"
            for url, role in tagged.items():
                if url in role_of and role_of[url] != role:
                    case.warnings.append(
                        "filename-index pairing contradicts tagged thumbnail; "
                        "falling back to tagged pair only")
                    pairs = []
                    break
        if not pairs:
            before = [u for u, r in tagged.items() if r == "before"]
            after = [u for u, r in tagged.items() if r == "after"]
            if len(before) == 1 and len(after) == 1:
                pairs = [(before[0], after[0])]
            elif urls:
                case.warnings.append("could not establish before/after pairing")
        for n, (before, after) in enumerate(pairs, 1):
            case.pairs.append(ImagePair(key=f"pair{n}", before_url=before,
                                        after_url=after))
        if not case.pairs:
            case.warnings.append("no usable image pairs")
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# sanantonio parser (BRAG book plugin; composite before|after images)
# ---------------------------------------------------------------------------

SANANTONIO_GALLERY_PATH = "/before-after-photos/breast-augmentation/"
# Case slugs always start with a digit ('24004', '23818-2'); this also keeps
# the stale 'page/2' pagination link and sibling galleries (e.g.
# breast-augmentation-with-lift/) out of the listing matches.
SANANTONIO_CASE_RE = re.compile(re.escape(SANANTONIO_GALLERY_PATH) + r"(\d[\d-]*)/")


def sanantonio_list_cases(listing_html: str) -> list[str]:
    """Unique case URL slugs from the gallery listing page, document order."""
    slugs = []
    for m in SANANTONIO_CASE_RE.finditer(listing_html):
        if m.group(1) not in slugs:
            slugs.append(m.group(1))
    return slugs


def sanantonio_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    case = CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")
    detail = soup.select_one("div.brag-book-gallery-case-detail-view")
    if detail is None:
        case.warnings.append("no brag-book case detail view found")
        return case

    # The BRAG book display case number (data-case-id) differs from the
    # WordPress URL slug; the slug is the unique key, the display number is
    # kept as a documented field for cross-referencing the clinic site.
    display_id = detail.get("data-case-id", "")

    specs = CaseSpecs()
    if display_id:
        specs.fields["Gallery Case"] = f"#{display_id}"
    labels = detail.select(".brag-book-gallery-info-label")
    values = detail.select(".brag-book-gallery-info-value")
    for label_el, value_el in zip(labels, values):
        label, value = label_el.get_text(strip=True), value_el.get_text(strip=True)
        if not label or not value:
            continue
        if label == "Age":
            m = re.match(r"(\d+)", value)
            if m:
                specs.age = int(m.group(1))
        elif label == "Photo Taken":
            m = re.search(r"(\d+(?:\.\d+)?)\s*months?\s*post[- ]op", value, re.I)
            if m:
                specs.months_post_op = float(m.group(1))
            else:
                specs.fields[label] = value
        elif label == "Implant Size":
            # Bare per-implant volume in cc (e.g. '310'); kept as a raw field
            # as well so the documented text survives in notes.
            specs.fields[label] = value
            m = re.match(r"(\d+(?:\.\d+)?)\s*(?:cc)?$", value, re.I)
            if m and 100 <= float(m.group(1)) <= 1000:
                specs.left_cc = specs.right_cc = float(m.group(1))
        else:
            # Height/Weight units are not documented on the page: record the
            # values verbatim instead of inventing units.
            specs.fields[label] = value
    notes_el = detail.select_one(".case-notes-body")
    if notes_el is not None:
        specs.summary = notes_el.get_text(" ", strip=True)

    # Narrative fallbacks for stats the structured grid omits (documented
    # text only; nothing is inferred beyond what the page states).
    if specs.age is None:
        m = re.search(r"\b(\d{2})[ -]year[ -]old\b", specs.summary, re.I)
        if m:
            specs.age = int(m.group(1))
    if specs.months_post_op is None:
        m = re.search(r"\b(?:postop|post-op)\s+(\d+(?:\.\d+)?)\s*months?\b",
                      specs.summary, re.I) or re.search(
            r"\b(\d+(?:\.\d+)?)\s*months?\s*(?:postop|post-op)\b",
            specs.summary, re.I)
        if m:
            specs.months_post_op = float(m.group(1))

    # Narrative cc only when the structured grid did not provide one.
    if specs.left_cc is None and specs.right_cc is None and specs.summary:
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
    haystack = " ".join([specs.summary, *specs.fields.values()])
    classify_brand_shape_profile(specs, haystack)
    procedures = list(dict.fromkeys(
        b.get_text(" ", strip=True) for b in detail.select(".procedure-badge")))
    if procedures:
        specs.fields["Procedures Performed"] = "; ".join(procedures)
    case.specs = specs

    # Composite before|after images, one per (unlabeled) angle, in the
    # thumbnail track's data-image-index order.
    thumbs = detail.select("div.brag-book-gallery-thumbnail-item")
    indexed = []
    for thumb in thumbs:
        url = thumb.get("data-processed-url", "")
        if not url:
            img = thumb.find("img")
            url = img.get("src", "") if img is not None else ""
        if not url:
            continue
        m = re.match(r"(\d+)$", thumb.get("data-image-index", ""))
        indexed.append((int(m.group(1)) if m else len(indexed), url))
    seen = set()
    for _, url in sorted(indexed):
        if url in seen:
            continue
        seen.add(url)
        case.pairs.append(ImagePair(key=f"angle{len(seen)}", before_url=url,
                                    after_url=url, split_composite=True))
    if not case.pairs:
        case.warnings.append("no usable image pairs")
    return case


# ---------------------------------------------------------------------------
# harrington parser (WordPress custom; ordinal-view single images)
# ---------------------------------------------------------------------------

HARRINGTON_GALLERY_PATH = "/photo-gallery/breast-augmentation/"


def harrington_list_cases(listing_html: str) -> list[str]:
    slugs = []
    for m in re.finditer(re.escape(HARRINGTON_GALLERY_PATH) + r"(\d[\d-]*)/", listing_html):
        if m.group(1) not in slugs:
            slugs.append(m.group(1))
    return slugs


def harrington_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    """5 single-view images per case via data-before/data-after anchor attrs.

    Views are ordinal ('view-0'..'view-4') with no front/oblique/side
    semantics documented anywhere on the page; every view label comes from
    visual-inspection annotations, keyed by pair key 'view1'..'view5'.
    """
    case = CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")
    ol = soup.select_one("div.bot-thumb-sec ol[class*='casethumb-c-']")
    for i, li in enumerate(ol.select("li") if ol is not None else [], 1):
        a = li.find("a")
        if a is None or not a.get("data-before") or not a.get("data-after"):
            continue
        case.pairs.append(ImagePair(key=f"view{i}", before_url=a["data-before"],
                                    after_url=a["data-after"]))
    if not case.pairs:
        case.warnings.append("no usable image pairs")

    specs = CaseSpecs()
    detail = soup.select_one("div.case-detail-left .case-detail-text")
    if detail is not None:
        for p in detail.find_all("p"):
            strong = p.find("strong")
            if strong is not None:
                label = strong.get_text(strip=True).rstrip(":")
                value = p.get_text(strip=True)[len(strong.get_text(strip=True)):].strip()
                if value:
                    specs.fields[label] = value
            elif not specs.summary:
                text = p.get_text(strip=True)
                if text:
                    specs.summary = text
    age_field = specs.fields.get("Age", "")
    if age_field.isdigit():
        specs.age = int(age_field)
    if specs.age is None:
        m = re.search(r"in (?:her|his) (\d0)s\b", specs.summary, re.I)
        if m:
            specs.age = int(m.group(1))
    specs.height = specs.fields.get("Height", "")
    weight_field = specs.fields.get("Weight", "")
    m = re.match(r"(\d+)", weight_field)
    if m:
        specs.weight_lbs = int(m.group(1))
    left = specs.fields.get("Implant Size Left", "")
    right = specs.fields.get("Implant Size Right", "")
    left_ccs, right_ccs = _cc_numbers(left), _cc_numbers(right)
    if left_ccs:
        specs.left_cc = left_ccs[0]
    if right_ccs:
        specs.right_cc = right_ccs[0]
    if specs.left_cc is None and specs.right_cc is None and specs.summary:
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
    haystack = " ".join([specs.summary, *specs.fields.values()])
    classify_brand_shape_profile(specs, haystack)
    case.specs = specs
    return case


# ---------------------------------------------------------------------------
# influx_swiper parser (lakeshore, marina: Influx legacy template, generic
# Before/After span labels, no view labels -> positional pairs)
# ---------------------------------------------------------------------------


def influx_swiper_list_cases(listing_html: str, gallery_path: str) -> list[str]:
    ids = []
    for m in re.finditer(re.escape(gallery_path) + r"(\d+)/", listing_html):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return sorted(ids, key=int)


# The Influx template renders one patient-details block in three markups, and
# the same case page mixes them: one <p> per 'Label: value' (the majority),
# one <li> per field inside a single wrapper <p>, and the 'bare-value' layout
# that drops the labels entirely and prints each value in its own <p>
# ('Patient#: n/a', 'Full Profile', '400cc', 'Submuscular', ...). Fields are
# therefore recovered by scanning for these known labels anywhere in a line -
# one line can carry the whole block - and a line with no label is classified
# by its content instead of being discarded. Vocabulary measured over all 122
# lakeshore and 132 marina cached case pages; an unrecognised 'Label: value'
# line still falls back to a plain split, so a new label is kept, not dropped.
INFLUX_FIELD_LABELS = (
    "Age", "Description", "Gender", "Height", "Height#", "Implant Cohesivity",
    "Implant Placement", "Implant Profile", "Implant Shape", "Implant Texture",
    "Implant Type", "Implant Type#", "Implant volume", "Incision", "Patient#",
    "Post-Op Time", "Procedure", "Procedure Description", "Weight", "Weight#",
)
# Longest label first so 'Height#'/'Implant Type#'/'Procedure Description' win
# over the shorter labels they contain.
INFLUX_LABEL_RE = re.compile(
    r"\b(" + "|".join(re.escape(label) for label in
                      sorted(INFLUX_FIELD_LABELS, key=len, reverse=True))
    + r")\s*:\s*", re.I)
# The bare-value layout still prints the labelled placeholders, so 'n/a' is the
# absence of a value, not a value.
INFLUX_PLACEHOLDER_VALUES = {"n/a", "na", "n.a.", "-", "--"}
# These fields hold the clinic's narrative under a label, so they are prose,
# not chart: they never feed placement/incision. Marina case 7463's description
# walks through dual-plane AND subglandular before naming the one it used.
INFLUX_NARRATIVE_LABELS = {"description", "procedure description"}
INFLUX_BARE_HEIGHT_RE = re.compile(r"^(\d)\s*['’]\s*(\d{1,2})?\s*(?:\"|”)?$")
INFLUX_BARE_WEIGHT_RE = re.compile(r"^(\d{2,3})\s*(?:lbs?|pounds?)\.?$", re.I)
# A line of digits and separators only ('339 & 371'): a spec whose unit the
# clinic never printed. Recognised so it does not masquerade as prose, and
# deliberately NOT read as cc - see CC_RE's unit requirement.
INFLUX_BARE_NUMERIC_RE = re.compile(r"^[\d\s&,.+/-]+$")
INFLUX_BARE_SPEC_RE = re.compile(
    r"\b(profile|round|teardrop|anatomic\w*|shaped|smooth|textured)\b", re.I)
# Prose runs long; a bare spec value is a couple of words.
INFLUX_BARE_SPEC_MAX_WORDS = 4


def _influx_detail_lines(detail) -> list[str]:
    """One text line per innermost <p>/<li> of the patient-details block.

    The template wraps the whole block in a <p class='text-center lead'> that
    the parser is served as a PARENT of the real <p>/<li> elements, so its own
    text is every field concatenated; taking only the innermost elements drops
    that duplicate and keeps one spec per line.
    """
    lines = []
    for element in detail.find_all(["p", "li"]):
        if element.find(["p", "li"]) is not None:
            continue
        text = element.get_text(" ", strip=True)
        if text:
            lines.append(text)
    return lines


def _influx_split_fields(line: str) -> tuple[list[tuple[str, str]], str]:
    """(fields, leftover) for one detail line.

    Each known label ends the previous field's value, so a line holding the
    whole block ('Procedure: Breast augmentation Implant Type: Silicone
    Implant volume: 345cc ...') yields every field instead of one field that
    swallowed the rest. 'leftover' is the label-less text ahead of the first
    field, i.e. the whole line when the layout published no labels at all.
    """
    matches = list(INFLUX_LABEL_RE.finditer(line))
    if not matches:
        label, sep, value = line.partition(": ")
        if sep and value:
            return [(label.strip(), value.strip())], ""
        return [], line
    fields = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        fields.append((match.group(1).strip(), line[match.end():end].strip()))
    return fields, line[:matches[0].start()].strip()


def _influx_is_bare_spec(line: str) -> bool:
    """True when a label-less line is a single spec value rather than prose.

    Marina publishes a narrative paragraph where lakeshore's bare-value layout
    publishes one value per line, and the two must not be confused: the
    narrative has to keep flowing to specs.summary so parse_fill_volumes()
    still reads the sided volumes out of it.
    """
    if len(line.split()) > INFLUX_BARE_SPEC_MAX_WORDS:
        return False
    return bool(
        INFLUX_BARE_NUMERIC_RE.match(line)
        or INFLUX_BARE_HEIGHT_RE.match(line)
        or INFLUX_BARE_WEIGHT_RE.match(line)
        or _cc_numbers(line)
        or INFLUX_BARE_SPEC_RE.search(line)
        or any(p.search(line) for p, _ in PLACEMENT_PATTERNS + INCISION_PATTERNS)
    )


def influx_swiper_parse_case(case_html: str, case_id: str, source_url: str,
                             gallery_path: str) -> CaseData:
    """Single-view images labeled only 'Before'/'After' by a sibling span.

    No front/oblique/side labels exist anywhere on the page (unlike
    drkolker's alt-text convention), so images are paired positionally
    (before immediately followed by after, in document order) and every
    view label comes from visual-inspection annotations.

    The patient-details spec block has three markups on the same site; see
    INFLUX_FIELD_LABELS above for that contract and how it is read.
    """
    case = CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")
    tagged: list[tuple[str, str]] = []
    for slide in soup.select("div.swiper-slide"):
        if slide.find_parent(class_="thumbs-mobile") is not None or "thumbs-mobile" in (
                slide.get("class") or []):
            continue
        wrap = slide.select_one("div.procedure-gallery__image")
        if wrap is None:
            continue
        img = wrap.find("img")
        # The 'Before'/'After' label is a sibling <span> of the image
        # wrapper within the same slide, not a descendant of it.
        span = slide.find("span")
        if img is None or span is None:
            continue
        src = img.get("src", "")
        phase = span.get_text(strip=True).lower()
        if src and phase in ("before", "after"):
            tagged.append((phase, src))
    i = 0
    while i < len(tagged) - 1:
        if tagged[i][0] == "before" and tagged[i + 1][0] == "after":
            case.pairs.append(ImagePair(key=f"pair{len(case.pairs) + 1}",
                                        before_url=tagged[i][1], after_url=tagged[i + 1][1]))
            i += 2
        else:
            i += 1
    if not case.pairs:
        case.warnings.append("no usable image pairs")

    specs = CaseSpecs()
    detail = soup.select_one("div.patient-details")
    bare_specs: list[str] = []
    if detail is not None:
        prose: list[str] = []
        for line in _influx_detail_lines(detail):
            fields, leftover = _influx_split_fields(line)
            for label, value in fields:
                if value and value.lower() not in INFLUX_PLACEHOLDER_VALUES:
                    specs.fields[label] = value
            if leftover:
                (bare_specs if _influx_is_bare_spec(leftover) else prose).append(leftover)
        if prose:
            specs.summary = prose[0]
    m = re.search(r"(\d+)[\s-]*(?:yr|year)s?[\s-]*old", specs.summary, re.I)
    if m:
        specs.age = int(m.group(1))
    m = re.search(r"(\d+['’]\d+)\"?\s*,?\s*(\d+)\s*lbs", specs.summary, re.I)
    if m:
        specs.height = m.group(1).replace("’", "'")
        specs.weight_lbs = int(m.group(2))
    if specs.age is None:
        age_field = specs.fields.get("Age", "") or specs.fields.get("Patient Age", "")
        if age_field.isdigit():
            specs.age = int(age_field)
    if not specs.height:
        specs.height = specs.fields.get("Height", "") or next(
            (line for line in bare_specs if INFLUX_BARE_HEIGHT_RE.match(line)), "")
        specs.height = specs.height.replace("’", "'")
    if specs.weight_lbs is None:
        # The labelled layout prints 'Weight: 137' with no unit; the same
        # clinic's bare-value layout prints '120 lbs', so pounds is the
        # gallery's own documented unit, not an assumption about the number.
        m = re.match(r"(\d+)", specs.fields.get("Weight", "")) or next(
            (m for m in (INFLUX_BARE_WEIGHT_RE.match(line) for line in bare_specs)
             if m), None)
        if m:
            specs.weight_lbs = int(m.group(1))
    specs.height_cm = height_to_cm(specs.height)
    if specs.weight_lbs is not None:
        specs.weight_kg = pounds_to_kg(specs.weight_lbs)
    vol_ccs = _cc_numbers(specs.fields.get("Implant volume", ""))
    if vol_ccs:
        # A case documenting two volumes ('405 cc (left side), 445 cc (right
        # side)') gets both, not the left one twice: collapsing onto vol_ccs[0]
        # made volume_cc() report the left side where the schema asks for the
        # average.
        specs.left_cc = vol_ccs[0]
        specs.right_cc = vol_ccs[1] if len(vol_ccs) > 1 else vol_ccs[0]
    else:
        # Bare-value layout: the volume is its own unlabelled line. Sided
        # spellings ('R-450cc, L-485cc') keep their sides.
        volume_line = next((line for line in bare_specs if _cc_numbers(line)), "")
        if volume_line:
            specs.left_cc, specs.right_cc = parse_fill_volumes(volume_line)
    if specs.left_cc is None and specs.summary:
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
    haystack = " ".join([specs.summary, *bare_specs, *specs.fields.values()])
    classify_brand_shape_profile(specs, haystack)
    chart = [value for label, value in specs.fields.items()
             if label.lower() not in INFLUX_NARRATIVE_LABELS]
    classify_placement_incision(specs, " ".join([*bare_specs, *chart]))
    case.specs = specs
    return case


# ---------------------------------------------------------------------------
# austinweston parser (Influx S3-backed; positional before/after, narrative)
# ---------------------------------------------------------------------------


def austinweston_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    case = CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")
    gallery = soup.select_one("div.procedure-gallery__swiper-gallery")
    urls = []
    for wrap in (gallery.select("div.procedure-gallery__image") if gallery is not None else []):
        if wrap.find_parent(class_="thumbs-mobile") is not None:
            continue
        img = wrap.find("img")
        src = img.get("src", "") if img is not None else ""
        if src:
            urls.append(src)
    for idx, (before, after) in enumerate(zip(urls[::2], urls[1::2]), 1):
        case.pairs.append(ImagePair(key=f"pair{idx}", before_url=before, after_url=after))
    if not case.pairs:
        case.warnings.append("no usable image pairs")

    specs = CaseSpecs()
    container = soup.select_one("div.procedure-gallery")
    li = container.select_one("ul li") if container is not None else None
    if li is not None:
        specs.summary = li.get_text(" ", strip=True)
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
        classify_brand_shape_profile(specs, specs.summary)
    case.specs = specs
    return case


# ---------------------------------------------------------------------------
# charlotte parser (Influx Growthstack; per-view stitched composites)
# ---------------------------------------------------------------------------


def charlotte_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    """3 stitched before|after composite images (one per view angle), each
    needing its own horizontal-midpoint split. Views are not labeled."""
    case = CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")
    urls: list[str] = []
    for slide in soup.select("div.swiper-slide"):
        classes = slide.get("class", [])
        if not any(c.startswith("gallery-image-") for c in classes):
            continue
        img = slide.find("img")
        src = img.get("src", "") if img is not None else ""
        if src and src not in urls:
            urls.append(src)
    for idx, url in enumerate(urls, 1):
        case.pairs.append(ImagePair(key=f"view{idx}", before_url=url, after_url=url,
                                    split_composite=True))
    if not case.pairs:
        case.warnings.append("no usable image pairs")

    specs = CaseSpecs()
    for p in soup.select("div.gallery-patient-details div.gallery-description p"):
        span = p.find("span", class_="description-item")
        if span is None:
            continue
        label = span.get_text(strip=True).rstrip(":").upper()
        value = p.get_text(" ", strip=True)[len(span.get_text(strip=True)):].strip()
        if value:
            specs.fields[label] = value
    for label, value in specs.fields.items():
        if "AGE" in label and value.strip().isdigit():
            specs.age = int(value.strip())
    haystack = " ".join(specs.fields.values())
    if haystack:
        specs.left_cc, specs.right_cc = parse_fill_volumes(haystack)
        classify_brand_shape_profile(specs, haystack)
    case.specs = specs
    return case


# ---------------------------------------------------------------------------
# mwps parser (Mountain West Plastic Surgery; Influx Growthstack, Astro build,
# 'gallery-stitched-subcategory': every slide is a before|after composite)
# ---------------------------------------------------------------------------

MWPS_GALLERY_IMG_RE = re.compile(r"/_static_/gallery/")
# Every composite carries a translucent "MW MOUNTAIN WEST PLASTIC SURGERY" mark
# (circle monogram over two lines of type) centred on the split seam at the
# bottom of the frame, so an equal piece of it lands on EACH half - cropping
# one side only would leave the mark on one half of a pair, which is a label
# leak. It is cropped, never masked, and the crop is applied to the whole
# composite before the split so the halves stay dimension- and framing-matched.
#
# The mark is SCALED TO THE FRAME, not stamped at a fixed pixel size. Measured
# by eye off a row ruler at 2-3x zoom on three heights, the top of the circle
# sits at 133/644, 118/568 and 103/499 of the way up from the bottom - 0.2065,
# 0.2077, 0.2064. 0.22 covers that with ~6% headroom.
# Do not re-derive this by aligning a residual on the bottom edge across the
# gallery: that population is dominated by one height (53 of 114 composites are
# 1500x499) and reports a plausible-looking constant ~100px offset, which
# silently leaves the logo's top on every image taller than ~515 - i.e. on
# every composite that clears the 400px floor.
MWPS_WATERMARK_CROP_BOTTOM_FRAC = 0.22

# Purity screen (captain ruling: pure breast augmentation only). Read off the
# case's own Description, never the URL slug. Two traps, both real here:
#   - the newer cases wrap the case in practice-marketing prose, so 'a wide
#     range of cosmetic and RECONSTRUCTIVE surgery procedures' (case 168) and
#     'an UPLIFTed confidence' (case 188) must NOT exclude a pure case: the
#     patterns are anchored to breast-procedure wording and to word starts.
#   - an implant EXCHANGE reads as an augmentation unless 'removed/replaced'
#     is caught (cases 113, 121, 128): the before photo is of a patient who
#     already has implants, which is not the before this corpus means.
MWPS_IMPURE_PATTERNS = [
    (re.compile(r"\bmastopexy\b", re.I), "mastopexy"),
    (re.compile(r"\blift(s|ed|ing)?\b", re.I), "breast lift"),
    (re.compile(r"\breduction\b", re.I), "reduction"),
    (re.compile(r"\brevision\b", re.I), "revision"),
    (re.compile(r"\bexplant\w*\b", re.I), "explant"),
    (re.compile(r"\bremov\w*\b[^.]{0,60}\bimplant", re.I), "implant removal"),
    (re.compile(r"\breplac\w*\b[^.]{0,60}\bimplant", re.I), "implant exchange"),
    (re.compile(r"\bimplants?\b[^.]{0,60}\breplac\w*", re.I), "implant exchange"),
    (re.compile(r"\bmommy makeover\b", re.I), "mommy makeover"),
    (re.compile(r"\bfat (transfer|graft\w*)\b", re.I), "fat transfer"),
    (re.compile(r"\bbreast reconstruction\b", re.I), "breast reconstruction"),
    (re.compile(r"\babdominoplasty\b|\btummy tuck\b", re.I), "abdominoplasty"),
    (re.compile(r"\bliposuction\b", re.I), "liposuction"),
]
# Volume: the newer chart publishes a BARE number under a labelled implant-size
# field ('Implant Size Left: 415'), which counts per the captain's volume rule;
# a bare number in free prose does not (case 92's 'Smooth round 385 Implants'
# stays unrecorded), which is why prose only ever goes through parse_fill_volumes.
MWPS_VOLUME_LABELS = {"implant size left": "left", "implant size right": "right"}
MWPS_BARE_CC_RE = re.compile(r"^\s*(\d{2,4})(?:\s*(?:cc|ml))?\s*$", re.I)
MWPS_MONTHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*months?", re.I)


def mwps_list_cases(listing_html: str, gallery_path: str) -> list[str]:
    """Numbered case subpages linked from the single (unpaginated) listing."""
    ids: list[str] = []
    for m in re.finditer(re.escape(gallery_path) + r"(\d+)/", listing_html):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return sorted(ids, key=int)


def mwps_impure_reason(text: str) -> str | None:
    """The combined/revision procedure a case's own description names, or None."""
    for pattern, reason in MWPS_IMPURE_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def mwps_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    """One case page: N stitched before|after composites plus a spec block.

    Images. The gallery is a 'stitched' subcategory, so EVERY slide is a whole
    before|after composite that needs its own midpoint split. The slides still
    carry the template's `gallery-image-before`/`gallery-image-after` classes
    from the un-stitched layout and those classes are meaningless here - case
    162 publishes three composites classed before/after/before. Reading them as
    a before/after tagging (as influx_swiper legitimately does on its own
    template) would pair two unrelated views. Order is the clinic's own view
    order and is not documented, so views come from visual-inspection
    annotations, keyed by the image stem ('6-01').

    Specs. Two generations share one block. The older cases publish only
    Patient Age/Patient Gender as labelled fields and put everything else in a
    terse free-text Description ("...high profile round implants; 350cc"); the
    newer ones publish a labelled chart (Height in inches, Weight Before in lb,
    Implant Size Left/Right, Post Op Time) alongside a long marketing narrative.
    Neither Description is a chart, so neither feeds placement/incision - a
    narrative that names a plane is prose, per CLAUDE.md.
    """
    case = CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")

    specs = CaseSpecs()
    detail = soup.select_one("div.gallery-patient-details div.gallery-description")
    if detail is not None:
        heading = detail.select_one("h2.about-patient-description")
        if heading is not None:
            nxt = heading.find_next_sibling()
            if nxt is not None and nxt.name == "p":
                specs.summary = nxt.get_text(" ", strip=True)
        for p in detail.select("p"):
            span = p.find("span", class_="about-patient-title")
            if span is None:
                continue
            label = span.get_text(strip=True).rstrip(":")
            value = p.get_text(" ", strip=True)[len(span.get_text(strip=True)):].strip()
            if label and value:
                specs.fields[label] = value

    reason = mwps_impure_reason(specs.summary)
    if reason is not None:
        case.warnings.append(
            f"excluded: not a pure breast augmentation ({reason})")
        case.specs = specs
        return case

    for slide in soup.select("div.swiper-slide"):
        img = slide.find("img")
        if img is None:
            continue
        src = img.get("src", "")
        if not src or not MWPS_GALLERY_IMG_RE.search(src):
            continue
        if any(p.before_url == src for p in case.pairs):
            continue
        case.pairs.append(ImagePair(
            key=Path(urlsplit(src).path).stem,
            before_url=src, after_url=src, split_composite=True,
            composite_bottom_frac=MWPS_WATERMARK_CROP_BOTTOM_FRAC))
    if not case.pairs:
        case.warnings.append("no usable image pairs")

    age = specs.fields.get("Patient Age", "")
    if age.isdigit():
        specs.age = int(age)
    specs.gender = specs.fields.get("Patient Gender", "")
    for label, side in MWPS_VOLUME_LABELS.items():
        value = next((v for k, v in specs.fields.items() if k.lower() == label), "")
        m = MWPS_BARE_CC_RE.match(value)
        if m and 100 <= int(m.group(1)) <= 1000:
            setattr(specs, f"{side}_cc", float(m.group(1)))
    if specs.left_cc is None and specs.right_cc is None:
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
    elif specs.left_cc is None or specs.right_cc is None:
        # One side labelled and the other absent: the schema wants the average
        # of what was published, not a side silently doubled.
        only = specs.left_cc if specs.left_cc is not None else specs.right_cc
        specs.left_cc = specs.right_cc = only

    m = re.search(r"(\d+['’]\s?\d{0,2})\"?", specs.summary)
    if m:
        specs.height = m.group(1).replace("’", "'").replace(" ", "")
        specs.height_cm = height_to_cm(specs.height)
    if specs.height_cm is None:
        specs.height_cm = inches_to_cm(specs.fields.get("Height", ""))
    m = re.search(r"(\d{2,3})\s*lbs?\b", specs.summary, re.I)
    if m:
        specs.weight_lbs = int(m.group(1))
    elif MWPS_BARE_CC_RE.match(specs.fields.get("Weight Before", "")):
        specs.weight_lbs = int(specs.fields["Weight Before"].strip())
    if specs.weight_lbs is not None:
        specs.weight_kg = pounds_to_kg(specs.weight_lbs)
    m = MWPS_MONTHS_RE.search(specs.fields.get("Post Op Time", ""))
    if m:
        specs.months_post_op = float(m.group(1))

    classify_brand_shape_profile(specs, specs.summary)
    case.specs = specs
    return case

# ---------------------------------------------------------------------------
# allure parser (no listing index; discovered by walking Next links)
# ---------------------------------------------------------------------------


def allure_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    """Owl-carousel case page; positional before/after (first/second image
    in each carousel item), narrative caption, no view labels."""
    case = CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")
    carousel = soup.select_one("div#sync1.owl-carousel")
    if carousel is not None:
        for idx, item in enumerate(carousel.select("div.item"), 1):
            feats = item.select("div.feat2 img")
            if len(feats) < 2:
                continue
            before_src = feats[0].get("src", "")
            after_src = feats[1].get("src", "")
            if before_src and after_src:
                case.pairs.append(ImagePair(
                    key=f"pair{idx}",
                    before_url=urljoin(source_url, before_src),
                    after_url=urljoin(source_url, after_src)))
    if not case.pairs:
        case.warnings.append("no usable image pairs")

    specs = CaseSpecs()
    desc = soup.select_one("div.desc p.text-center")
    if desc is not None:
        specs.summary = desc.get_text(" ", strip=True)
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
        classify_brand_shape_profile(specs, specs.summary)
    case.specs = specs
    return case


def allure_next_case_id(case_html: str) -> str | None:
    soup = BeautifulSoup(case_html, "html.parser")
    next_a = soup.select_one("span.right.next a[href]")
    if next_a is None:
        return None
    m = re.search(r"(\d+)/?$", next_a["href"].rstrip("/"))
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# drtavakoli parser (Bricks page builder; single inline listing)
# ---------------------------------------------------------------------------

TAVAKOLI_TITLE_RE = re.compile(r"^Breast Augmentation\b", re.I)


def drtavakoli_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """Real case composites (2026/03 uploads) carry a clean 'Breast
    Augmentation ...' title; older 2024 filler images are excluded by that
    same title-format check. Each composite is a single before|after
    2-up image; view is unlabeled."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    seen_ids: set[str] = set()
    for li in soup.select("ul.brxe-image-gallery li.bricks-layout-item"):
        a = li.select_one("figure a[data-lightbox-caption][data-pswp-src]")
        if a is None:
            continue
        caption = a.get("data-lightbox-caption", "").strip()
        if not TAVAKOLI_TITLE_RE.match(caption):
            continue
        src = a.get("data-pswp-src", "")
        if not src:
            continue
        case_id = li.get("data-id") or hashlib.sha256(src.encode()).hexdigest()[:8]
        if case_id in seen_ids:
            continue
        seen_ids.add(case_id)
        case = CaseData(case_id=case_id, source_url=source_url)
        case.pairs.append(ImagePair(key="pair1", before_url=src, after_url=src,
                                    split_composite=True))
        specs = CaseSpecs()
        specs.summary = caption
        specs.left_cc, specs.right_cc = parse_fill_volumes(caption)
        # Profile codes (THPX/TMPX/HP) are undocumented shorthand outside
        # plain-English 'High/Moderate Profile' wording, which
        # classify_brand_shape_profile already matches; codes are never
        # decoded, per the drkolker 'Mini Motiva' precedent.
        classify_brand_shape_profile(specs, caption)
        case.specs = specs
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# drjeremyhunt parser (single inline paginated listing; per-view composites)
# ---------------------------------------------------------------------------

DRJEREMYHUNT_SIZE_SUFFIX_RE = re.compile(r"-\d+x\d+(?=\.\w+$)")


def drjeremyhunt_full_res(url: str) -> str:
    """Strip the WordPress '-WIDTHxHEIGHT' resize suffix to reach the
    original upload (confirmed against the fancybox href in recon)."""
    return DRJEREMYHUNT_SIZE_SUFFIX_RE.sub("", url)


def drjeremyhunt_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """Each case is 'div.patientgallery-box' paired with a sibling image
    holder one level up (shared 'div.bt-row' ancestor); images are
    before|after composites, view from the '-45'/'-front'/'-side' filename
    slug (this clinic's only per-image angle indicator)."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for box in soup.select("div.patientgallery-box"):
        num_el = box.select_one("h3.patientgallery-title span.patientgallery-number")
        case_id = num_el.get_text(strip=True) if num_el is not None else f"case{len(cases) + 1}"
        # Both the caption ('.patientgallery-dp') and the image slider live
        # outside 'div.patientgallery-box' itself, as siblings under the
        # shared per-case 'bt-row' ancestor.
        row = box.find_parent(class_="bt-row")
        cap_el = row.select_one("p.patientgallery-dp") if row is not None else None
        caption = cap_el.get_text(" ", strip=True) if cap_el is not None else ""
        slider = row.select_one(".bt-firstcol .slider-holder6") if row is not None else None
        case = CaseData(case_id=case_id, source_url=source_url)
        # Each case's fancybox group has a unique id ('gallery1'..'galleryN'),
        # not a shared 'gallery1'; matching any data-fancybox anchor is
        # sufficient since 'slider' is already scoped to this one case.
        for img in (slider.select('a[data-fancybox] picture.patientgallery-img '
                                  'img[data-src]') if slider is not None else []):
            thumb = img.get("data-src", "")
            if not thumb:
                continue
            full = drjeremyhunt_full_res(thumb)
            fname = full.rsplit("/", 1)[-1].lower()
            if "-45" in fname:
                view_hint = "oblique"
            elif "-front" in fname:
                view_hint = "front"
            elif "-side" in fname:
                view_hint = "side"
            else:
                view_hint = None
            key = view_hint or f"pair{len(case.pairs) + 1}"
            case.pairs.append(ImagePair(key=key, before_url=full, after_url=full,
                                        split_composite=True, view_hint=view_hint))
        if not case.pairs:
            continue
        specs = CaseSpecs()
        specs.summary = caption
        m = re.search(r"(\d+)\s*yo", caption, re.I)
        if m:
            specs.age = int(m.group(1))
        specs.left_cc, specs.right_cc = parse_fill_volumes(caption)
        classify_brand_shape_profile(specs, caption)
        case.specs = specs
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# sixsurgery parser (Webflow; structured filterable spec fields)
# ---------------------------------------------------------------------------

SIXSURGERY_CASE_RE = re.compile(r"Composite\s+(\d+)", re.I)


def sixsurgery_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """2 single-view images per case (document order: before, then after,
    confirmed by visual inspection), rich structured field grid. View is
    unlabeled (one clinical angle per case, not documented).

    Some entries interleave a 'sensitive content' eye icon
    (img.hide-eye-image) inside each .blurred-img-container, so the photos
    must be selected by img.blurred-img rather than by position - matching
    every img made the icon the 'after' image for 37 of 58 cases.
    """
    soup = BeautifulSoup(listing_html, "html.parser")
    cases: dict[str, CaseData] = {}
    for i, wrapper in enumerate(soup.select("div.gallery-entry-wrapper"), 1):
        imgs = wrapper.select(
            ".dallery-entry-imgs-wrapper .blurred-img-container img.blurred-img")
        if len(imgs) < 2:
            continue
        before_src, after_src = imgs[0].get("src", ""), imgs[1].get("src", "")
        if not before_src or not after_src:
            continue
        m = SIXSURGERY_CASE_RE.search(unquote(before_src))
        case_id = m.group(1) if m else f"case{i}"
        # One patient's angles are separate gallery entries carrying the same
        # case number; they are one case with several pairs, not several cases
        # with a colliding id (a colliding id makes every pair after the first
        # unemittable, and makes one annotation key mean two different cases).
        case = cases.get(case_id)
        if case is None:
            case = cases[case_id] = CaseData(case_id=case_id, source_url=source_url)
        case.pairs.append(ImagePair(key=f"pair{len(case.pairs) + 1}",
                                    before_url=before_src, after_url=after_src))
        if case.specs.left_cc is not None or case.specs.fields:
            continue  # specs already captured from this case's first entry

        specs = CaseSpecs()
        bmi_value = wrapper.select_one(".bmi-value") or wrapper.select_one(".bmitext")
        if bmi_value is not None:
            ccs = _cc_numbers(bmi_value.get_text(strip=True))
            if ccs:
                specs.left_cc = specs.right_cc = ccs[0]
        for row in wrapper.select(".age-wrapper"):
            label_el = row.select_one(".gallery-description-medium-text")
            value_el = row.select_one(".gallery-description-regular-text")
            if label_el is not None and value_el is not None:
                label = label_el.get_text(strip=True).rstrip(":")
                value = value_el.get_text(strip=True)
                if value:
                    specs.fields[label] = value
        specs.height = specs.fields.get("Height", "")
        m = re.match(r"(\d+)", specs.fields.get("Weight", ""))
        if m:
            specs.weight_lbs = int(m.group(1))
        haystack = " ".join(specs.fields.values())
        classify_brand_shape_profile(specs, haystack)
        case.specs = specs
    return list(cases.values())


# ---------------------------------------------------------------------------
# drmiroshnik parser (single inline listing; per-view composites)
# ---------------------------------------------------------------------------


def drmiroshnik_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """Each front/side image is itself a before|after 2-up composite
    (confirmed by visual inspection); cases are consecutive slides sharing
    identical caption text. View comes from the filename suffix where
    regular ('-front'/'-side'); irregular suffixes fall back to unlabeled."""
    soup = BeautifulSoup(listing_html, "html.parser")
    groups: list[tuple[str, list[str]]] = []
    current_caption: str | None = None
    current: list[str] = []
    for slide in soup.select("article.the-gallery div.slideset div.big-slide"):
        img = slide.select_one("img.gallery_large_single_landscape")
        cap_el = slide.select_one("span.caption")
        caption = cap_el.get_text(" ", strip=True) if cap_el is not None else ""
        src = img.get("src", "") if img is not None else ""
        if not src:
            continue
        if caption != current_caption and current:
            groups.append((current_caption or "", current))
            current = []
        current_caption = caption
        current.append(src)
    if current:
        groups.append((current_caption or "", current))

    cases = []
    for i, (caption, srcs) in enumerate(groups, 1):
        case = CaseData(case_id=f"case{i}", source_url=source_url)
        for src in srcs:
            fname = src.rsplit("/", 1)[-1].lower()
            m = re.search(r"patient-\d+-([a-z-]+?)(?:-\d+)?\.jpg$", fname)
            suffix = m.group(1) if m else ""
            if suffix.startswith("front"):
                view_hint = "front"
            elif suffix:
                view_hint = "side"
            else:
                view_hint = None
            case.pairs.append(ImagePair(key=suffix or f"pair{len(case.pairs) + 1}",
                                        before_url=src, after_url=src,
                                        split_composite=True, view_hint=view_hint))
        if not case.pairs:
            continue
        specs = CaseSpecs()
        specs.summary = caption
        m = re.match(r"(\d+)\s*yo", caption, re.I)
        if m:
            specs.age = int(m.group(1))
        specs.left_cc, specs.right_cc = parse_fill_volumes(caption)
        classify_brand_shape_profile(specs, caption)
        case.specs = specs
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# drrohrich parser (NextGEN Gallery; 2x2 grid composites)
# ---------------------------------------------------------------------------


def drrohrich_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """One 2x2 grid image per case: front-before|front-after (row 0),
    side-before|side-after (row 1), confirmed by visual inspection. Yields
    2 pairs per case from the single fetched image."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for div in soup.select('div[id^="ngg-image-"]'):
        a = div.select_one("a.ngg-fancybox[data-src]")
        if a is None:
            continue
        case_id = div.get("id", "").replace("ngg-image-", "") or a.get("data-image-id", "")
        src = a.get("data-src") or a.get("href", "")
        if not case_id or not src:
            continue
        case = CaseData(case_id=case_id, source_url=source_url)
        case.pairs.append(ImagePair(key="front", before_url=src, after_url=src,
                                    grid_shape=(2, 2), before_cell=(0, 0), after_cell=(0, 1),
                                    view_hint="front"))
        case.pairs.append(ImagePair(key="side", before_url=src, after_url=src,
                                    grid_shape=(2, 2), before_cell=(1, 0), after_cell=(1, 1),
                                    view_hint="side"))
        specs = CaseSpecs()
        caption = a.get("data-description") or a.get("title") or ""
        specs.summary = caption
        m = re.search(r"(\d+)\s*year\s*old", caption, re.I)
        if m:
            specs.age = int(m.group(1))
        specs.left_cc, specs.right_cc = parse_fill_volumes(caption)
        classify_brand_shape_profile(specs, caption)
        case.specs = specs
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# wny parser (Etna Interactive; case chain walked via prev/next links)
# ---------------------------------------------------------------------------

WNY_IMAGE_RE = re.compile(
    r'(?:https?:)?//images\.wnyplasticsurgery\.com/content/images/[\w-]+-view-(\d+)-detail\.jpg')
WNY_CASE_LINK_RE = re.compile(r"/gallery/breast/breast-augmentation/(\d+)/")


def wny_seed_cases(listing_html: str) -> list[str]:
    return sorted(set(WNY_CASE_LINK_RE.findall(listing_html)), key=int)


def wny_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    """3 side-by-side before|after composites (one per view), matched by
    the Etna 'view-N-detail' filename convention; view is not labeled."""
    case = CaseData(case_id=case_id, source_url=source_url)
    seen = set()
    ordered = []
    for m in WNY_IMAGE_RE.finditer(case_html):
        url = m.group(0)
        if url in seen:
            continue
        seen.add(url)
        ordered.append((int(m.group(1)), url))
    for view_num, url in sorted(ordered):
        full = url if url.startswith("http") else f"https:{url}"
        case.pairs.append(ImagePair(key=f"view{view_num}", before_url=full, after_url=full,
                                    split_composite=True))
    if not case.pairs:
        case.warnings.append("no usable image pairs")

    specs = CaseSpecs()
    soup = BeautifulSoup(case_html, "html.parser")
    desc = soup.select_one(".case-description") or soup.select_one(".case-card-description")
    if desc is not None:
        specs.summary = desc.get_text(" ", strip=True)
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
        classify_brand_shape_profile(specs, specs.summary)
    case.specs = specs
    return case


def wny_next_case_ids(case_html: str) -> list[str]:
    soup = BeautifulSoup(case_html, "html.parser")
    ids = []
    for a in soup.select("a.case-details-prev[href], a.case-details-next[href]"):
        m = WNY_CASE_LINK_RE.search(a.get("href", ""))
        if m:
            ids.append(m.group(1))
    return ids


# ---------------------------------------------------------------------------
# etna parser family (Etna Interactive photo gallery; 12 clinics, 2026-08-15)
# ---------------------------------------------------------------------------
#
# One gallery product serves every clinic in the 2026-08-15 consented batch, so
# this is one parser configured twelve times rather than twelve parsers. The
# platform fixes four things across all of them:
#
#   * Case pages live at <gallery_path><case-id>/ and link their neighbours
#     through a.case-details-prev / a.case-details-next. Walking that chain from
#     the 12 cases the listing renders reaches the whole gallery; the listing
#     also embeds its own case count as EII_GALLERY_JS ... "state":{"total":N},
#     which collect_cases() uses as an independent completeness check.
#     The gallery's own "load more" calls admin-ajax.php under /wp-admin/, which
#     every one of these robots.txt files disallows - it is never requested.
#     The sitemap does NOT enumerate case pages (verified on se-plasticsurgery:
#     post/page/eii_* sitemaps carry zero gallery case URLs), so the chain plus
#     the declared total is the enumeration contract.
#   * Photos are side-by-side before|after composites at
#     //images.<host>/content/images/<procedure>-<case>-<view>-detail.jpg,
#     split at the horizontal midpoint. Some Etna sites serve the identical
#     asset from S3 instead, so the match is on the '-detail.<ext>' suffix on
#     any host, not on a CDN path.
#   * The image filename's leading slug is the case's OWN procedure, which is
#     how a mixed gallery is screened: case 159 in lukecurtsingermd's breast
#     augmentation gallery publishes 'mommy-makeover-159-front-detail.jpg', and
#     case 483 in ablavsky's publishes 'lower-circumferential-body-lift-483-'.
#     Both are excluded by the standing captain ruling on combined procedures.
#   * The spec block is a single .case-description div.
#
# What the platform does NOT fix is the shape of that spec block, and assuming
# it did is the lakeshore mistake (218 pairs lost to a single-layout parser).
# Five layouts are published across these clinics and are all handled here; see
# ETNA_FIELD_LABELS and etna_parse_description() below.

# Etna publishes each view either positionally ('view-1') or by name
# ('left-oblique'). A named token documents the view - and, for the lateral
# views, the laterality - in the clinic's own filename, which is the only
# laterality source AGENTS.md accepts without a visual call. Named tokens
# follow the corpus convention derived from heavenly's Left-Oblique filenames:
# 'left' means the patient's LEFT side faces the camera.
ETNA_VIEW_TOKENS = {
    "front": "front",
    "anterior": "front",
    "left-oblique": "oblique-left",
    "oblique-left": "oblique-left",
    "right-oblique": "oblique-right",
    "oblique-right": "oblique-right",
    "left-side": "side-left",
    "side-left": "side-left",
    "left-lateral": "side-left",
    "right-side": "side-right",
    "side-right": "side-right",
    "right-lateral": "side-right",
}
# Tokens that name a real photograph the schema has no view for. Recorded as a
# skip reason rather than silently dropped, so the per-clinic accounting can say
# why a fetched image produced no pair.
#
# 'front-arms-raised' and 'bent-forward' are deliberately here rather than
# mapped onto 'front': they are different POSES, and folding them into front
# would both mislabel the pose and collide with the case's real front view.
ETNA_NON_SCHEMA_VIEWS = {
    "back", "rear", "posterior", "front-arms-raised", "bent-forward",
    "arms-raised", "bending-forward",
}
ETNA_DETAIL_RE = re.compile(
    r"(?://|https?://)[A-Za-z0-9.\-]+/[A-Za-z0-9./\-]*?"
    r"(?P<procedure>[a-z0-9]+(?:-[a-z0-9]+)*?)-(?P<case>\d+)-"
    r"(?P<view>[a-z]+(?:-[a-z0-9]+)*)-detail\.(?P<ext>jpg|jpeg|png|webp)", re.I)
ETNA_CASE_LINK_RE = re.compile(r"/(\d+)/?$")
ETNA_TOTAL_RE = re.compile(r'"total"\s*:\s*(\d+)')
# The gallery slug that counts as pure breast augmentation. Everything else in
# these galleries is a combined procedure (mommy makeover, augmentation with
# lift/mastopexy, body lift), whose after photograph shows a change the implants
# did not cause - excluded by captain ruling.
ETNA_PURE_PROCEDURE = "breast-augmentation"
# Backstop for the chain walk. It traverses off-category pages to reach the
# other components, so a practice's whole gallery is in scope; this only stops
# a pathological crawl, and the declared-total check reports any shortfall.
ETNA_MAX_PAGES = 4000


def etna_list_seed_cases(listing_html: str, gallery_path: str) -> list[str]:
    """Case ids rendered directly into the listing page (12 on every clinic)."""
    ids = []
    for m in re.finditer(re.escape(gallery_path) + r"(\d+)/", listing_html):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return sorted(ids, key=int)


def etna_declared_total(listing_html: str) -> int | None:
    """The gallery's own case count, embedded as EII_GALLERY_JS state.total."""
    m = re.search(r"EII_GALLERY_JS.{0,2000}?" + ETNA_TOTAL_RE.pattern,
                  listing_html, re.S)
    return int(m.group(1)) if m else None


# --- The gallery case-list endpoint (opened by the 2026-08-18 access grant) ---
#
# Each Etna listing renders 12 cases into HTML and serves every further case only
# through the gallery plugin's own `admin-ajax.php` action, which these robots.txt
# files disallow. The 2026-08-15 run therefore stopped at 556 of 1299 declared
# cases and recorded the shortfall rather than working around it. On 2026-08-18
# the practices granted access to that endpoint (see ClinicConfig.endpoint_grant),
# so it is now the enumeration route for the clinics that hold a grant.
#
# The request contract is read off the gallery's own public script,
# content/plugins/etna-photo-gallery/dist/js/etna-photo-gallery-category-results.js:
#
#   POST <EII_GALLERY_JS.CATEGORY_RESULTS.url>?action=<...action>
#   multipart body: every field of the listing's #filter-cases-form (of which only
#   the hidden category_id matters), plus action, first_returned_position (1-based)
#   and case_count.
#   200 JSON: {"_html": base64 of a .category-cases fragment, "env": {"state": {...}}}
#
# Two measured properties of the endpoint shape the code below.
#
# 1. `case_count` is capped. Measured on tccs: 12, 24, 50, 56, 64 and 75 all return
#    exactly that many cards; 100 and 600 return 8. So a request for "all of them"
#    silently under-returns rather than erroring, which is why the walk pages at a
#    conservative size and reconciles against the declared total instead of
#    trusting one big request.
# 2. The result ORDER is not the rendered listing's order and is not stable across
#    different `case_count` values - the first 12 of a count=24 response are not the
#    12 a count=12 response returns. Positions are therefore treated as a way to
#    sweep the set, never as stable case identity: cases are deduplicated by URL and
#    the sweep is checked against `state.total`, with a second sweep at a different
#    page size when the first comes up short.
ETNA_AJAX_URL_RE = re.compile(
    r'EII_GALLERY_JS.{0,4000}?"url"\s*:\s*"(?P<url>[^"]+admin-ajax\.php)"'
    r'\s*,\s*"action"\s*:\s*"(?P<action>[^"]+)"', re.S)
# Conservative page size: comfortably under the measured cap, so a cap that differs
# per clinic cannot silently truncate a sweep.
ETNA_AJAX_PAGE_SIZE = 50
# A second sweep at a different page size only helps because the order depends on
# the size; using the same size again would just repeat the first sweep.
ETNA_AJAX_RETRY_PAGE_SIZE = 24
# The endpoint answers HTTP 200 with an error paragraph in place of the case
# cards when the gallery's own backend fails:
#
#   <div class="category-cases"><p class="error">We are currently experiencing
#   technical difficulties. Administrators have been notified...</p></div>
#
# Measured on tccs on 2026-08-19: nine 50-case pages served, then this at
# position 451; a second sweep then served eight 24-case pages and hit it at
# position 193. It is intermittent and it recovers, so it is a failure to retry
# rather than a ceiling to accept.
#
# Reading it as "no more cases" is the dangerous failure, and it is the one that
# happens by default: the response is a 200 carrying a well-formed envelope whose
# `state` still counts up (`showing: 500`, `cases_remaining: 82`), so an empty
# card list looks exactly like the end of the set. That silently turned 582
# declared cases into 450 and reported success. It must never be cached either -
# a cached error is replayed as data on every later run.
ETNA_AJAX_ERROR_RE = re.compile(
    r'<p[^>]*class="[^"]*\berror\b[^"]*"', re.I)
ETNA_AJAX_DELAY = 3.0
# Backing off is the courteous response to a site telling us its backend is
# struggling, and the access grant asks for exactly that in return for the
# permission. Retries wait minutes, not seconds.
ETNA_AJAX_ERROR_BACKOFF = (60.0, 180.0, 420.0)


class EtnaEndpointError(RuntimeError):
    """The gallery endpoint answered 200 with its backend-failure page.

    `paths` carries whatever the sweep had already collected when it gave up.
    Those cases are real and were served successfully; discarding them because a
    LATER page failed would throw away most of a gallery over its tail - on tccs
    that is 450 of 582 cases lost to a failure at position 451.
    """

    def __init__(self, message: str, paths: list[str] | None = None):
        super().__init__(message)
        self.paths = paths or []


def etna_ajax_config(listing_html: str) -> tuple[str, str] | None:
    """The endpoint URL and action the listing's own script would POST to."""
    m = ETNA_AJAX_URL_RE.search(listing_html)
    if m is None:
        return None
    return m.group("url").replace("\\/", "/"), m.group("action")


def etna_filter_fields(listing_html: str) -> dict[str, str]:
    """Hidden fields of the listing's #filter-cases-form.

    Only `category_id` is carried. The form's other controls are the Gender/Age/
    provider filters, which the browser submits at their unset defaults and which
    would narrow the result set if sent with a value - the opposite of what an
    enumeration wants.
    """
    soup = BeautifulSoup(listing_html, "html.parser")
    form = soup.find(id="filter-cases-form")
    fields = {}
    if form is not None:
        for inp in form.select('input[type="hidden"][name]'):
            if inp.get("name") == "category_id" and inp.get("value"):
                fields["category_id"] = inp["value"]
    return fields


def etna_decode_ajax(payload: bytes) -> tuple[dict, str]:
    """Split an endpoint response into its state dict and its HTML fragment.

    `_html` is base64 of UTF-8 bytes; the gallery's script decodes it the long way
    round (atob + decodeURIComponent) purely because it is running in a browser.

    Raises EtnaEndpointError if the fragment is the gallery's backend-failure page
    rather than a card list, so a failure can never be mistaken for the end of the
    set. See ETNA_AJAX_ERROR_RE.
    """
    data = json.loads(payload.decode("utf-8", "replace"))
    html = base64.b64decode(data.get("_html", "")).decode("utf-8", "replace")
    if ETNA_AJAX_ERROR_RE.search(html):
        raise EtnaEndpointError(
            BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    return data.get("env", {}).get("state", {}), html


class EtnaAjaxPage(NamedTuple):
    """One endpoint response: the case paths it named, and how big it was.

    `cards` is the count of cards the response actually carried, BEFORE any were
    dropped for an unparseable href, because it is the page-size signal the sweep
    reads: a page shorter than the one asked for is the end of the set. Measuring
    that on the filtered list instead makes a single unrecognised card in a full
    page look like the end of the gallery, which stops the sweep at that position
    and reports a short collection as a finished one.
    """

    paths: list[str]
    cards: int


def etna_ajax_page(payload_html: str) -> EtnaAjaxPage:
    """Case page paths from one endpoint response, in the order it returned them.

    The card's `href` is the case's OWN canonical gallery URL, which is not always
    in the category that was asked for: tccs's breast-augmentation category returns
    cards pointing at /gallery/mommy-makeover/mommy-makeover/240/. That is the
    practice filing a combined procedure under breast augmentation as a tag, and it
    is why the declared total for a category is not a count of pure cases. The
    paths are returned as published; scoping them to the category is the caller's
    job, and the image-filename procedure screen still runs on top of that.
    """
    soup = BeautifulSoup(payload_html, "html.parser")
    cards = soup.select(".case-card-inner[href]")
    paths = []
    for card in cards:
        path = urlsplit(card["href"]).path
        if not ETNA_CASE_LINK_RE.search(path.rstrip("/") + "/"):
            continue
        # Deliberately NOT deduplicated here. How many cards a page returned is
        # what tells the sweep whether that page was short, and collapsing a
        # repeat inside one page would make a full page look like the end of the
        # set. Deduplication happens across the whole sweep instead.
        paths.append(path.rstrip("/") + "/")
    return EtnaAjaxPage(paths, len(cards))


def etna_endpoint_sweep(fetcher: "PoliteFetcher", slug: str, endpoint: str,
                        action: str, fields: dict[str, str], total: int,
                        page_size: int, tag: str) -> list[str]:
    """One pass over the category, page_size cases at a time, in path order seen.

    Stops at the declared total, or early on an empty or short page - a page that
    returns fewer cards than asked for is the end of the set, and continuing past
    it just asks the server for positions that do not exist.
    """
    paths: list[str] = []
    position = 1
    while position <= total:
        body = dict(fields)
        body.update({"action": action,
                     "first_returned_position": str(position),
                     "case_count": str(page_size)})
        key = f"{slug}_ajax_{tag}_{position}_{page_size}.json"
        # `validate` runs on the cached path as well as the fresh one, so it is
        # the single decode of this page: it keeps the result rather than the
        # sweep decoding the same bytes a second time, and reading it inside the
        # try is what puts a malformed page on the partial-progress path below
        # instead of past it.
        decoded: dict = {}

        def keep_decoded(data: bytes) -> None:
            decoded["page"] = etna_decode_ajax(data)

        try:
            fetcher.post_form(f"{endpoint}?action={action}", body, key,
                              delay=ETNA_AJAX_DELAY, validate=keep_decoded,
                              retry_waits=ETNA_AJAX_ERROR_BACKOFF)
            state, html = decoded["page"]
        except FileNotFoundError:
            # Offline mode with no cached response for this position. The sweep
            # ends here rather than the run: an offline re-parse of the cache is
            # how this corpus proves a shared parser's blast radius, and it has to
            # keep working against a cache taken before the sweep existed.
            print(f"  {slug}: endpoint sweep {tag} is cache-bounded at "
                  f"position {position}")
            break
        except EtnaEndpointError as exc:
            # Every retry was refused. The sweep is INCOMPLETE, not finished, and
            # says so: the caller reconciles against the declared total, and a
            # truncated sweep reported as a clean one is exactly how 582 declared
            # cases became a confident 450. The pages that DID succeed are carried
            # out on the exception rather than discarded.
            print(f"  WARN {slug}: endpoint sweep {tag} ABANDONED at position "
                  f"{position} - the gallery backend is failing ({exc}). "
                  f"{len(paths)} case path(s) collected before that point are "
                  f"kept.")
            raise EtnaEndpointError(str(exc), paths) from exc
        except (ValueError, OSError) as exc:
            # The backend-failure page is not the only way this endpoint fails: a
            # truncated body raises JSONDecodeError, a bad base64 fragment raises
            # binascii.Error, and a connection reset or a persistent 5xx raises a
            # requests error (both ValueError and OSError subclasses). All of them
            # used to escape past the handler above and abort the clinic run,
            # discarding exactly the pages EtnaEndpointError.paths exists to keep.
            print(f"  WARN {slug}: endpoint sweep {tag} ABANDONED at position "
                  f"{position} - {type(exc).__name__}: {exc}. "
                  f"{len(paths)} case path(s) collected before that point are "
                  f"kept.")
            raise EtnaEndpointError(f"{type(exc).__name__}: {exc}", paths) from exc
        page = etna_ajax_page(html)
        for path in page.paths:
            if path not in paths:
                paths.append(path)
        # `state.showing` is a CUMULATIVE counter, not this page's size, so it can
        # never signal a short page. Only the cards actually returned can - all of
        # them, including any this parser could not read a case id out of.
        if page.cards < page_size:
            break
        # The state block is the failing backend's own output, so it is untrusted
        # input: a full page returned with a `next_returned_position` that does not
        # advance would re-request the same cache key forever, and after the first
        # pass that is a pure cache read - no network call, no politeness sleep and
        # no output. A silent hang, not a visible failure. The position only ever
        # moves forward, by at least one page.
        try:
            advertised = int(state.get("next_returned_position", 0))
        except (TypeError, ValueError):
            advertised = 0
        position = max(advertised, position + page_size)
    return paths


def endpoint_grant_document(cfg: ClinicConfig,
                            grant_root: Path | None = None) -> Path:
    """The site owner's case-list endpoint grant, verified present on disk.

    A grant is a document, not a string. Checking only that `endpoint_grant` is
    set means a stale or mistyped path opens the endpoint exactly as wide as a
    real one, and this corpus's standing lesson is that a documented permission is
    only intent until something on disk enforces it. Relative paths resolve
    against `grant_root` (the corpus root the run is given), so the check is made
    against the same tree the grant was filed in.
    """
    if not cfg.endpoint_grant:
        raise PermissionError(
            f"{cfg.slug}: no endpoint access grant on file; the gallery case-list "
            f"endpoint must not be requested for this clinic")
    path = Path(cfg.endpoint_grant)
    if not path.is_absolute():
        path = Path(grant_root if grant_root is not None else Path.cwd()) / path
    if not path.is_file():
        raise PermissionError(
            f"{cfg.slug}: endpoint access grant {path} is not on disk; the "
            f"gallery case-list endpoint must not be requested without it")
    return path


def etna_endpoint_case_paths(cfg: ClinicConfig, fetcher: "PoliteFetcher",
                             listing_html: str, total: int,
                             grant_root: Path | None = None) -> list[str]:
    """Every case path the gallery's own case-list endpoint will name.

    Refuses outright unless the clinic's `endpoint_grant` document is present: the
    grant is what makes this request permitted at all, and a caller must not be
    able to reach the endpoint by passing a slug the grant does not cover.
    """
    endpoint_grant_document(cfg, grant_root)
    config = etna_ajax_config(listing_html)
    if config is None:
        print(f"  WARN {cfg.slug}: listing declares no gallery endpoint")
        return []
    endpoint, action = config
    fields = etna_filter_fields(listing_html)
    if "category_id" not in fields:
        print(f"  WARN {cfg.slug}: listing declares no category_id; "
              f"the endpoint would return some other category")
        return []
    paths: list[str] = []
    for tag, page_size in (("a", ETNA_AJAX_PAGE_SIZE),
                           ("b", ETNA_AJAX_RETRY_PAGE_SIZE)):
        # The order depends on the page size, so the second sweep is a genuinely
        # different traversal of the same set rather than a repeat. It is only
        # spent when the first comes up short, since it costs the site another
        # full pass.
        if tag != "a" and len(paths) >= total:
            break
        incomplete = False
        try:
            found = etna_endpoint_sweep(fetcher, cfg.slug, endpoint, action,
                                        fields, total, page_size, tag)
        except EtnaEndpointError as exc:
            # Already reported by the sweep. Keep the pages it did get, and still
            # try the other traversal: the failure is intermittent, and it lands
            # at a different position under a different page size.
            found, incomplete = exc.paths, True
        added = [p for p in found if p not in paths]
        paths.extend(added)
        print(f"  {cfg.slug}: endpoint sweep {tag.upper()} "
              f"{'ABANDONED, ' if incomplete else ''}added {len(added)} "
              f"case path(s); {len(paths)} of {total} declared")
    return paths


def etna_gallery_root(gallery_path: str) -> str:
    """The path prefix the prev/next chain is scoped to.

    The chain is NOT scoped to the procedure category: kochandcarlisle's breast
    augmentation cases link on into /photo-gallery/body-procedures/liposuction-
    fat-transfer-brazilian-butt-lift/, and ablavsky's into mommy-makeover,
    implant-removal-and-replacement and lower-circumferential-body-lift. It is
    scoped to the practice's gallery root, so that is what the walk follows -
    dropping an off-category link instead ends the walk there, which cost 128
    of 152 camp cases and 69 of 81 kochandcarlisle cases on the first run.
    """
    return "/" + gallery_path.strip("/").split("/")[0] + "/"


def etna_category_paths(index_html: str, gallery_root: str) -> list[str]:
    """Category listing paths linked from the gallery index page.

    The prev/next chain is a set of DISCONNECTED components, not one path over
    the gallery, so seeding only from the target category strands most of it:
    kochandcarlisle's 12 rendered seeds reach a 67-page component holding just
    those 12 of its 81 augmentation cases, and the walk then legitimately ends.
    The remaining cases are only offered through the gallery's own
    admin-ajax.php 'load more', which every one of these robots.txt files
    disallows and which this scraper never calls. Seeding the walk from every
    category listing instead puts it into the other components, which is the
    same case-chain route - just entered at more than one point.
    """
    paths = []
    for m in re.finditer(r'href="([^"]+)"', index_html):
        path = urlsplit(m.group(1)).path
        if not path.startswith(gallery_root):
            continue
        parts = [p for p in path[len(gallery_root):].split("/") if p]
        if len(parts) == 2 and not parts[-1].isdigit():
            candidate = f"{gallery_root}{parts[0]}/{parts[1]}/"
            if candidate not in paths:
                paths.append(candidate)
    return paths


def etna_next_case_paths(case_html: str, gallery_root: str) -> list[str]:
    """Neighbour case page paths from the prev/next chain links.

    Returns full paths rather than bare ids, because the chain crosses
    categories and an id is only unique together with its category.
    """
    soup = BeautifulSoup(case_html, "html.parser")
    paths = []
    for a in soup.select("a.case-details-prev[href], a.case-details-next[href]"):
        path = urlsplit(a.get("href", "")).path
        if not path.startswith(gallery_root) or not ETNA_CASE_LINK_RE.search(
                path.rstrip("/") + "/"):
            continue
        path = path.rstrip("/") + "/"
        if path not in paths:
            paths.append(path)
    return paths


# Chart labels published inside .case-description, measured across all 720
# cached case pages of the 2026-08-15 batch. Scanned for ANYWHERE in a line
# rather than split at the first ': ', because northraleigh concatenates its
# fields with no delimiter at all ('Approach: InframammaryPlacement:
# SubfascialImplant type: Smooth round...'): splitting at the first ': ' there
# yields one field whose value swallows the rest of the chart, which is the
# lakeshore failure mode exactly.
ETNA_FIELD_LABELS = (
    "After Photos Taken", "Approach", "Bra Size", "Cup Size", "Height",
    "Implant Placement", "Implant Profile", "Implant Shape", "Implant Size",
    "Implant Size (Left)", "Implant Size (Right)", "Implant Style",
    "Implant Type", "Implant size", "Incision", "Left", "Patient Age",
    "Patient Height", "Patient Weight", "Placement", "Procedure", "Right",
    "Size", "Weight",
)
# Longest label first so 'Implant Size (Left)' wins over 'Implant Size', and
# 'Patient Height' over 'Height'.
#
# A plain \b before the label is not enough. northraleigh runs its fields
# together with no separator, so the next label begins mid-word
# ('...InframammaryPlacement: Subfascial...'): there is no word boundary
# between 'y' and 'P', \bPlacement never matches, and the first field's value
# swallows the entire rest of the chart. A label may therefore also start at a
# lowercase-to-uppercase transition. That second alternative has ignorecase
# switched off with (?-i:...) on purpose - under re.I the class [a-z] also
# matches capitals, which would let a label start between any two letters.
ETNA_LABEL_RE = re.compile(
    r"(?:(?<![A-Za-z0-9])|(?-i:(?<=[a-z])(?=[A-Z])))("
    + "|".join(re.escape(label) for label in
               sorted(ETNA_FIELD_LABELS, key=len, reverse=True))
    + r")\s*:\s*", re.I)
ETNA_EMPTY_DESCRIPTIONS = re.compile(
    r"^\s*no case details (?:for this patient|available)\.?\s*$", re.I)
# Fields whose label names the value as an implant size/volume. Per the
# 2026-08-15 units ruling, a bare number or an ml figure INSIDE one of these
# reads as cc; the same bare number in free prose does not.
ETNA_VOLUME_LABELS = {
    "implant size", "implant size (left)", "implant size (right)",
    "implant volume", "size", "left", "right",
}
ETNA_SIDED_VOLUME_LABELS = {
    "implant size (left)": "left", "implant size (right)": "right",
    "left": "left", "right": "right",
}
# Labels holding the clinic's own narrative. They never feed placement/incision,
# per the marina precedent where the prose walks through dual-plane AND
# subglandular before naming the one used.
ETNA_NARRATIVE_LABELS = {"procedure", "description"}
ETNA_BARE_VOLUME_RE = re.compile(
    r"^(\d{2,4}(?:\.\d+)?)\s*(?:cc|ccs|ml|mls|g|gm|gms|grams?)?\.?$", re.I)


def _etna_description_lines(desc) -> list[str]:
    """One text line per <br>-separated run inside the .case-description block.

    The block is published either as <p> paragraphs, as one <p> whose fields are
    separated by <br>, or as bare text directly in the div (northraleigh), so
    the split is on <br> and block boundaries rather than on element type.
    """
    for br in desc.find_all("br"):
        br.replace_with("\n")
    lines = []
    for block in desc.find_all(["p", "li", "div"]) or [desc]:
        if block.find(["p", "li"]) is not None:
            continue
        lines.extend(block.get_text(" ", strip=False).split("\n"))
    if not lines:
        lines = desc.get_text(" ", strip=False).split("\n")
    return [re.sub(r"\s+", " ", line).strip()
            for line in lines if line and line.strip()]


def _etna_split_fields(line: str) -> tuple[list[tuple[str, str]], str]:
    """(fields, leftover prose) for one description line.

    Each known label ends the previous field's value, so a line carrying a whole
    undelimited chart yields every field instead of one that swallowed the rest.
    """
    matches = list(ETNA_LABEL_RE.finditer(line))
    if not matches:
        return [], line
    fields = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        fields.append((match.group(1).strip(), line[match.end():end].strip()))
    return fields, line[:matches[0].start()].strip()


def _etna_labelled_volume(label: str, value: str) -> float | None:
    """cc figure from a field whose own label names it as an implant size.

    A bare number or an ml figure counts here because the label supplies the
    unit ('Implant Size: 350'); grams are recorded unconverted per the standing
    units ruling. Outside such a label this returns nothing - a bare number in
    free prose is not a volume.
    """
    if label.lower() not in ETNA_VOLUME_LABELS:
        return None
    # 'Left: 420 cc Filled to 480 cc' - the final volume is what was implanted.
    sided = _parse_fill_side(value)
    if sided is not None and 100 <= sided <= 1000:
        return sided
    m = ETNA_BARE_VOLUME_RE.match(value.strip())
    if m:
        cc = float(m.group(1))
        return cc if 100 <= cc <= 1000 else None
    return None


def etna_parse_description(desc, specs: CaseSpecs) -> None:
    """Fill specs from a .case-description block, whatever layout it uses.

    Five layouts are published across the twelve clinics and this handles all
    of them, because assuming one is how lakeshore silently lost 218 pairs:

      A. Labelled, <strong>Label:</strong>value separated by <br>
         (se-plasticsurgery: 'Implant Size (Left): 325cc').
      B. Labelled, <br>-separated, no <strong>, with a sided sub-block
         (jjrothmd: 'Placement: Submuscular' ... 'Left: 420 cc Filled to 480 cc').
      C. Labelled with NO delimiter between fields at all
         (northraleigh: 'Approach: InframammaryPlacement: Subfascial...').
      D. Narrative prose carrying the volume in a sentence
         (campplasticsurgery, craigcolvillemd, drhasen, bostoncoastal).
      E. Short prose with no labels ('370cc Mentor MPX gel implant', wmips).

    Layout F - 'No case details for this patient.' (ablavsky) - and a case page
    with no .case-description element at all (lukecurtsingermd) both leave specs
    empty, which drops the case at the volume gate rather than inventing one.
    """
    prose_parts: list[str] = []
    for line in _etna_description_lines(desc):
        if ETNA_EMPTY_DESCRIPTIONS.match(line):
            continue
        fields, leftover = _etna_split_fields(line)
        if leftover:
            prose_parts.append(leftover)
        for label, value in fields:
            if not value:
                continue
            key = label.lower()
            if key in ETNA_NARRATIVE_LABELS:
                prose_parts.append(f"{label}: {value}")
                continue
            specs.fields.setdefault(label, value)
            cc = _etna_labelled_volume(label, value)
            if cc is None:
                continue
            side = ETNA_SIDED_VOLUME_LABELS.get(key)
            if side == "left":
                specs.left_cc = cc
            elif side == "right":
                specs.right_cc = cc
            elif specs.left_cc is None and specs.right_cc is None:
                specs.left_cc = specs.right_cc = cc
    specs.summary = " ".join(prose_parts).strip()


# --- Purity: the image slug is necessary but NOT sufficient ---
#
# The captain's standing ruling excludes anything that is not a pure implant
# augmentation - mommy makeover and augmentation-with-lift above all - because
# the after photograph shows a change the implants did not cause.
#
# The 2026-08-15 run screened on the image filename's procedure slug alone. On
# the cases the case-list endpoint reached, that is not enough: 140 cases whose
# photographs all publish as `breast-augmentation-<case>-...` describe another
# procedure in their own text, and some of them plainly had one - tccs case 11
# "underwent mastopexy (breast lift)", kochcarlisle 507 "a breast augmentation
# with a breast lift. She also received a tummy tuck".
#
# A keyword scan over the block is equally wrong, and wrong in the expensive
# direction. It is the marina precedent from AGENTS.md: a narrative that names a
# procedure may be discussing it rather than reporting it. Two whole families of
# false positive appear here:
#
#   * Surgeon boilerplate. Every colville case ends with the surgeon's bio - "I
#     specialize in breast augmentation, breast lift, and breast reduction
#     surgeries" - which flags all 27 of its otherwise-clean cases.
#   * Negation. tccs 11283 reads "wanted larger implants (DD+ bra size) but no
#     lift"; 11284 reads "Although a lift was discussed, she was comfortable
#     with...". Both are pure augmentations that name a lift.
#
# So evidence is taken per SENTENCE, and a sentence counts only when it says
# THIS PATIENT had the other procedure: an other-procedure term, a patient
# subject, an affirmative surgical verb, and no negation or hypothetical. A
# sentence whose subject is the practice ("I specialize in", "we offer") is
# never evidence about a patient.
# 'lifting weights' and 'weight lifting' are not a mastopexy. camp case 789
# ("frequent exercise and lifting weights") and tccs 12167 ("CrossFit and weight
# lifting") are both pure augmentations that a bare \blift\b excludes.
ETNA_GYM_LIFT_RE = re.compile(
    r"\b(?:weight[- ]?lifting|lifting weights|lifting heavy|heavy lifting|"
    r"lifts weights|powerlifting|weightlifting)\b", re.I)
ETNA_OTHER_PROCEDURE_RE = re.compile(
    r"\b(mommy makeover|mastopexy|breast lift|breast lifts|lift|lifting|"
    r"breast reduction|reduction|tummy tuck|abdominoplasty|liposuction|lipo|"
    r"fat transfer|fat grafting|brazilian butt lift|bbl|body lift|"
    r"implant exchange|implant revision|revision|capsulectomy|explant|"
    r"removal and replacement|reconstruction)\b", re.I)
# Everything else is EXCLUDED. The first cut of this screen required an
# affirmative surgical verb ("underwent", "received", "performed a") before it
# would exclude, and that let real combined cases through - kochcarlisle 647
# ("fat injections, which were taken during her Tummy Tuck procedure"),
# ablavsky 239 ("a combined procedure of a breast lift and a breast augmentation"),
# tccs 931 ("Breast Augmentation with Nipple Reduction"), and camp 101, whose
# narrative reads "Camp peformed a breast augmentation and tummy tuck" - a typo
# in the clinic's own prose was enough to defeat a verb list.
#
# No verb list survives contact with free prose, and the two error directions are
# not symmetric: excluding a pure augmentation costs pairs, while including a
# mommy makeover teaches the model that implants produce a flat abdomen. So the
# default is exclusion, and only two things earn a sentence a pass - it is the
# practice advertising itself, or it says the procedure did not happen.
# The practice-subject list is deliberately broad: these galleries append a
# surgeon bio and a call-to-action to every case, and reading those as clinical
# fact would exclude entire clinics.
ETNA_PRACTICE_SUBJECT_RE = re.compile(
    r"\b(i specialize|i specialise|we specialize|we specialise|specialize in|"
    r"specialise in|specialized in|experience in breast|my experience in|"
    r"specializing in|specialises in|specializes in|specialization|specialisation|"
    r"my specialty|my speciality|specialty in|speciality in|my expertise|"
    r"expertise in|knowledge and expertise|menu of cosmetic|cosmetic services|"
    r"we offer|we provide|we perform|we pride|our practice|our office at|"
    r"board certif\w*|certification|professor|medical degree|residenc\w*|"
    r"if you have similar|if you are interested|may be right for you|"
    r"patients seeking|trust in the blend|our services|personalized care plans|"
    r"please call|schedule your|fill out the form|to see if you are a candidate|"
    r"learn more|are available on case)\b", re.I)
# A cue above excuses a sentence only while the sentence is about the practice.
# One that points at THIS case is reporting the surgery, whatever cue it also
# carries, and is never boilerplate. A bio clause and a clinical fact share a
# sentence more often than the cue list suggests: colville 555 opens "My
# technical proficiencies, honed at ... where I completed general surgery and
# plastic surgery residencies," and then names
# "this complex case involving implant removal with a full capsulectomy ... and
# mastopexy". Whitelisting on the credential there admits a combined procedure on
# the strength of the surgeon's CV, which is the same shape of error as excusing
# a whole sentence on a negation that governs a different mention.
ETNA_CASE_DEIXIS_RE = re.compile(
    r"\bthis\s+(?:\w+\s+){0,2}"
    r"(?:case|patient|procedure|surgery|operation|result|results)\b", re.I)
# Named but not done: discussed, declined, avoided, ruled out, deferred, offered
# as an option, or done instead. tccs case 11283 reads "wanted larger implants
# (DD+ bra size) but no lift" and 11284 "Although a lift was discussed, she was
# comfortable with..." - both are pure augmentations that name a lift, and both
# are the marina precedent from AGENTS.md: a narrative that names a procedure may
# be explaining the options rather than reporting this patient's.
#
# A negation is scoped to the MENTION it governs, never to the sentence. Scanning
# the whole sentence for a negation token and passing on a hit reverses the
# screen's own default: any sentence carrying one is whitelisted no matter what
# else it reports, so `She underwent a "mommy makeover" including an
# abdominoplasty and breast augmentation.` (tccs 240) read as pure on the strength
# of "including an", and `She underwent a breast augmentation and a mastopexy, but
# did not want liposuction.` read as pure on the strength of a negation covering a
# DIFFERENT procedure. So every other-procedure mention in a sentence has to be
# excused on its own, and one unexcused mention excludes the case.
#
# Cues are directional, because that is what separates a named-but-not-done
# mention from a done one. "which she had considered for years" sits AFTER the
# lift it describes and does not unmake it; "she considered a lift" sits before
# one that never happened. Likewise "tummy tuck instead of a staged approach" is
# a tummy tuck, while "instead of a lift" is not a lift.
ETNA_NEGATION_BEFORE_RE = re.compile(
    r"\b(no lift|not (?:a |an |the )?(?:lift|need|require|candidate)|without|"
    r"declined|declines|refused|avoid(?:ed|ing|s)?|"
    r"discuss(?:ed|ing|es)?|explore(?:d|s)? options|explore|"
    r"options include|"
    r"consider(?:ed|ing|s)?|would (?:have )?(?:require|need|likely)|"
    r"may help|might help|can be done|could be done|"
    r"should she|if she (?:wants|wishes)|"
    r"offered the option|option of|elected to have the|"
    r"instead of|rather than|as opposed to|"
    r"did not|didn't|does not|doesn't|was not|were not|"
    r"chose not|opted not|opted against|elected not|decided against|"
    r"but no|no need|unnecessary|was told (?:she|he|they)|told her that|"
    r"recommend(?:ed)? against|able to avoid)\b", re.I)
ETNA_NEGATION_AFTER_RE = re.compile(
    r"\b(discuss(?:ed|ing|es)?|declined|declines|refused|without|"
    r"avoid(?:ed|ing|s)?|able to avoid|"
    r"or a combination|alone, or|in the future|at a later time|"
    r"unnecessary|not (?:required|needed|necessary|performed|done)|"
    r"may help|might help|"
    r"was (?:not|never) (?:performed|done|needed|required))\b", re.I)
# How far a cue may sit from the mention it governs. Measured over every cached
# Etna case description (1181 of them): the widest real gap before a mention is
# tccs 224's "offered the option of a breast augmentation alone, or augmentation
# in conjunction with a lift" at 77 characters, and the widest after one is tccs
# 696's "breast lift ... but she was adamant about avoiding" at 59. Widening past
# these re-admits nothing; narrowing below them drops pure augmentations.
ETNA_NEGATION_BEFORE_WINDOW = 80
ETNA_NEGATION_AFTER_WINDOW = 72
ETNA_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def etna_mention_is_negated(sentence: str, start: int, end: int) -> bool:
    """Whether the other-procedure mention at [start:end) is named but not done."""
    before = sentence[max(0, start - ETNA_NEGATION_BEFORE_WINDOW):start]
    if ETNA_NEGATION_BEFORE_RE.search(before):
        return True
    after = sentence[end:end + ETNA_NEGATION_AFTER_WINDOW]
    return bool(ETNA_NEGATION_AFTER_RE.search(after))


def etna_combined_procedure_evidence(description: str) -> str | None:
    """The sentence showing this case is not a pure implant augmentation.

    Returns None only when every sentence naming another procedure is either the
    practice advertising itself (and not, in the same breath, reporting on THIS
    case) or names procedures that every one of its mentions says did not happen.
    Anything else excludes the case. See the comment block above for why the
    default is exclusion rather than inclusion.
    """
    for sentence in ETNA_SENTENCE_SPLIT_RE.split(description or ""):
        scanned = ETNA_GYM_LIFT_RE.sub(" ", sentence)
        if (ETNA_PRACTICE_SUBJECT_RE.search(scanned)
                and not ETNA_CASE_DEIXIS_RE.search(scanned)):
            continue
        for m in ETNA_OTHER_PROCEDURE_RE.finditer(scanned):
            if not etna_mention_is_negated(scanned, m.start(), m.end()):
                return sentence.strip()
    return None


def etna_parse_case(case_html: str, case_id: str, source_url: str,
                    gallery_path: str) -> CaseData:
    """One Etna case: side-by-side composites plus a .case-description block."""
    case = CaseData(case_id=case_id, source_url=source_url)

    # Every view is published in several encodings of the SAME photograph
    # (.jpg and .webp), so the pair is keyed on the view token, not the URL -
    # keying on the URL emits each view twice. JPEG is preferred because it is
    # what the rest of the pipeline re-encodes to anyway.
    ext_rank = {"jpg": 0, "jpeg": 1, "png": 2, "webp": 3}
    best: dict[str, tuple[int, str]] = {}
    procedures: set[str] = set()
    skipped_views: set[str] = set()
    for m in ETNA_DETAIL_RE.finditer(case_html):
        if m.group("case") != case_id:
            continue
        url = m.group(0)
        if not url.startswith("http"):
            url = "https:" + url
        procedures.add(m.group("procedure").lower())
        token = m.group("view").lower()
        if token in ETNA_NON_SCHEMA_VIEWS:
            skipped_views.add(token)
            continue
        rank = ext_rank.get(m.group("ext").lower(), 9)
        if token not in best or rank < best[token][0]:
            best[token] = (rank, url)
    for token in sorted(skipped_views):
        case.warnings.append(
            f"view '{token}': published photograph has no schema view; skipped")
    for token, (_, url) in sorted(best.items()):
        case.pairs.append(ImagePair(
            key=token, before_url=url, after_url=url, split_composite=True,
            view_hint=ETNA_VIEW_TOKENS.get(token)))

    # The filename's procedure slug is the case's own procedure, and a gallery
    # that lists a combined case still names it honestly there.
    if procedures and not any(p == ETNA_PURE_PROCEDURE for p in procedures):
        case.warnings.append(
            "not pure breast augmentation (published as "
            + "/".join(sorted(procedures)) + "); excluded by captain ruling")
        case.pairs = []
    if not case.pairs and not case.warnings:
        case.warnings.append("no usable image pairs")

    specs = CaseSpecs()
    soup = BeautifulSoup(case_html, "html.parser")
    desc = soup.select_one(".case-description") or soup.select_one(
        ".case-card-description")
    if desc is None:
        case.warnings.append("no .case-description block published")
    else:
        etna_parse_description(desc, specs)
        # The image slug is necessary but not sufficient. A combined case whose
        # photographs all publish as `breast-augmentation-<case>-...` is still a
        # combined case, and its after photograph shows a change the implants did
        # not cause. See etna_combined_procedure_evidence.
        evidence = etna_combined_procedure_evidence(desc.get_text(" ", strip=True))
        if evidence is not None:
            case.warnings.append(
                "not pure breast augmentation (the case's own text reads: "
                + f"{evidence!r}); excluded by captain ruling")
            case.pairs = []

    haystack = " ".join([specs.summary, *specs.fields.values()])
    age = specs.fields.get("Patient Age", "")
    if age.isdigit():
        specs.age = int(age)
    else:
        m = re.search(r"(\d{2})[- ]year[- ]old", haystack, re.I)
        if m:
            specs.age = int(m.group(1))
    height = specs.fields.get("Patient Height") or specs.fields.get("Height", "")
    if height:
        specs.height = height.replace("’", "'").replace("”", '"').strip()
        specs.height_cm = height_to_cm(specs.height)
    weight = specs.fields.get("Patient Weight") or specs.fields.get("Weight", "")
    m = re.match(r"^(\d{2,3})\s*(?:lbs?|pounds?)?\.?$", weight.strip(), re.I)
    if m:
        specs.weight_lbs = int(m.group(1))
        specs.weight_kg = pounds_to_kg(specs.weight_lbs)

    # Prose volumes only when the chart published none: the labelled fields are
    # the clinic's own statement, the narrative is a description of it.
    if specs.left_cc is None and specs.right_cc is None and specs.summary:
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)

    classify_brand_shape_profile(specs, haystack)
    # Chart text only - the narrative is deliberately excluded (AGENTS.md).
    classify_placement_incision(
        specs, " ".join(f"{k}: {v}" for k, v in specs.fields.items()))
    case.specs = specs
    return case


# ---------------------------------------------------------------------------
# privateclinic parser (WordPress; category-scoped paginated listing)
# ---------------------------------------------------------------------------


def privateclinic_parse_card(card, source_url: str) -> CaseData | None:
    """Cards on the category-scoped listing already carry the full spec
    block and a composite before|after image; no case-detail visit needed.
    Scoping to augmentation-only comes from fetching the dedicated
    breast-augmentation category listing URL, not from slug matching."""
    img = card.select_one("img")
    if img is None:
        return None
    src = img.get("src") or img.get("data-src") or ""
    if not src:
        return None
    m = re.search(r"-(\d+[a-zA-Z]{1,4})-[a-zA-Z-]*\.jpg", src)
    case_id = m.group(1).upper() if m else hashlib.sha256(src.encode()).hexdigest()[:8]
    case = CaseData(case_id=case_id, source_url=source_url)
    case.pairs.append(ImagePair(key="pair1", before_url=src, after_url=src,
                                split_composite=True))
    specs = CaseSpecs()
    desc = card.select_one("div.wp-block-group.is-style-description")
    if desc is not None:
        for field_div in desc.select("div.field"):
            strong = field_div.find("strong")
            if strong is None:
                continue
            label = strong.get_text(strip=True).rstrip(":")
            value = field_div.get_text(" ", strip=True)[len(strong.get_text(strip=True)):].strip()
            if value:
                specs.fields[label] = value
    size_field = specs.fields.get("Implant size", "")
    ccs = _cc_numbers(size_field if "cc" in size_field.lower() else f"{size_field}cc")
    if ccs:
        specs.left_cc = specs.right_cc = ccs[0]
    haystack = " ".join(specs.fields.values())
    classify_brand_shape_profile(specs, haystack)
    case.specs = specs
    return case


# ---------------------------------------------------------------------------
# mitchellbrown parser (RoyalSlider; cross-domain composite images)
# ---------------------------------------------------------------------------

MITCHELLBROWN_CASE_RE = re.compile(r'^(round-gel\d+|shaped-gel\d+|saline\d+)', re.I)


def mitchellbrown_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """Slides are grouped into cases by filename prefix; the 'ba-lift-case*'
    section (combo augmentation+lift) is excluded by the same prefix filter,
    since it is out of the implants-only scope."""
    soup = BeautifulSoup(listing_html, "html.parser")
    grouped: dict[str, list[tuple[str, str]]] = {}
    order = []
    for slide in soup.select("div.rsSlideRoot"):
        img = slide.select_one("img.rsImg")
        src = (img.get("data-src") or img.get("src")) if img is not None else None
        if not src:
            continue
        fname = src.rsplit("/", 1)[-1]
        m = MITCHELLBROWN_CASE_RE.match(fname)
        if not m:
            continue
        key = m.group(1).lower()
        h3 = slide.select_one("h3")
        caption = h3.get_text(" ", strip=True) if h3 is not None else ""
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append((src, caption))

    cases = []
    for key in order:
        urls_captions = grouped[key]
        case = CaseData(case_id=key, source_url=source_url)
        for idx, (url, _caption) in enumerate(urls_captions, 1):
            case.pairs.append(ImagePair(key=f"pair{idx}", before_url=url, after_url=url,
                                        split_composite=True))
        caption = next((c for _, c in urls_captions if c), "")
        specs = CaseSpecs()
        specs.summary = re.sub(r"\s+", " ", caption).strip()
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
        classify_brand_shape_profile(specs, specs.summary)
        case.specs = specs
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# skplastic parser (Webflow; 2x3 grid composites)
# ---------------------------------------------------------------------------


def skplastic_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """One 2x3 grid image per case: row 0 = before, row 1 = after; columns
    left-to-right = front/oblique/side (confirmed by visual inspection)."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for i, row in enumerate(soup.select("div.row.services.w-row"), 1):
        img = row.select_one(".left-column .image-block img")
        src = img.get("src", "") if img is not None else ""
        if not src:
            continue
        case = CaseData(case_id=f"case{i}", source_url=source_url)
        for col, view in enumerate(("front", "oblique", "side")):
            case.pairs.append(ImagePair(key=view, before_url=src, after_url=src,
                                        grid_shape=(2, 3), before_cell=(0, col),
                                        after_cell=(1, col), view_hint=view))
        specs = CaseSpecs()
        title_el = row.select_one(".right-column .case-description h6")
        body_el = row.select_one(".right-column .case-description p")
        parts = [el.get_text(" ", strip=True) for el in (title_el, body_el) if el is not None]
        specs.summary = " - ".join(p for p in parts if p)
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
        classify_brand_shape_profile(specs, specs.summary)
        case.specs = specs
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# heavenly parser (Beaver Builder; per-view composites, 2 filename eras)
# ---------------------------------------------------------------------------

HEAVENLY_OLD_RE = re.compile(
    r'Breast-Augmentation-(?:\d+cc-)?Before-(?:and-)?After-'
    r'(Left-Oblique|Right-Oblique|Front|Side)-[A-Z]{4}', re.I)
HEAVENLY_VIEW_MAP = {
    "front": "front", "side": "side",
    "left-oblique": "oblique-left", "right-oblique": "oblique-right",
}


def heavenly_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """Beaver Builder stacks each case as one-or-more '.fl-module-photo'
    blocks (composite before|after images) followed by a closing
    '.fl-module-rich-text' caption block. Older filenames document
    laterality directly; newer ones carry no view information at all."""
    soup = BeautifulSoup(listing_html, "html.parser")
    # The page has several 'fl-col-content' columns (header/footer/sidebar);
    # the gallery column is the one whose direct children include the
    # stacked photo/caption modules.
    candidates = soup.select("div.fl-col-content")
    col = max(candidates, key=lambda c: len(c.find_all(
        "div", class_="fl-module-photo", recursive=False)), default=None)
    cases = []
    if col is None:
        return cases
    current_photos: list[str] = []
    case_idx = 0
    for child in col.find_all("div", class_="fl-module", recursive=False):
        classes = child.get("class", [])
        if "fl-module-photo" in classes:
            img = child.select_one("img.fl-photo-img")
            src = (img.get("data-src") or img.get("src")) if img is not None else None
            if src:
                current_photos.append(src)
        elif "fl-module-rich-text" in classes and current_photos:
            case_idx += 1
            text_el = child.select_one(".fl-rich-text p")
            caption = text_el.get_text(" ", strip=True) if text_el is not None else ""
            case = CaseData(case_id=f"case{case_idx}", source_url=source_url)
            for idx, src in enumerate(current_photos, 1):
                fname = src.rsplit("/", 1)[-1].split("?")[0]
                m_old = HEAVENLY_OLD_RE.search(fname)
                view_hint = HEAVENLY_VIEW_MAP.get(m_old.group(1).lower()) if m_old else None
                case.pairs.append(ImagePair(key=f"pair{idx}", before_url=src, after_url=src,
                                            split_composite=True, view_hint=view_hint))
            specs = CaseSpecs()
            specs.summary = caption
            specs.left_cc, specs.right_cc = parse_fill_volumes(caption)
            classify_brand_shape_profile(specs, caption)
            case.specs = specs
            cases.append(case)
            current_photos = []
    return cases


# ---------------------------------------------------------------------------
# drgrover parser (single inline listing; numbered relative folders)
# ---------------------------------------------------------------------------


def drgrover_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """No spec text documented anywhere on the page; photos only."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for a in soup.select("div.index-gallery div.gallery-grid a.item[href]"):
        m = re.match(r"\.?/?(\d+)/?$", a.get("href", ""))
        if m is None:
            continue
        imgs = a.select("img.half")
        if len(imgs) < 2:
            continue
        before_src, after_src = imgs[0].get("src", ""), imgs[1].get("src", "")
        if not before_src or not after_src:
            continue
        case = CaseData(case_id=m.group(1), source_url=source_url)
        case.pairs.append(ImagePair(key="pair1",
                                    before_url=urljoin(source_url, before_src),
                                    after_url=urljoin(source_url, after_src)))
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# basu parser (Studio 3/DatoCMS; paginated listing, bare-URL originals)
# ---------------------------------------------------------------------------


def _datocms_bare_url(src: str) -> str:
    """Strip DatoCMS imgix query params (e.g. '?w=400') to reach the
    original upload; confirmed by comparing byte sizes in recon."""
    return src.split("?")[0] if src else src


def basu_parse_listing_page(listing_html: str, source_url: str) -> list[CaseData]:
    """No spec text documented anywhere on the page; photos only."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for a in soup.select("div.gallery-items-holder .partial_gallery_default_item_index "
                          ".item a.ba-holder"):
        imgs = a.select("picture img")
        if len(imgs) < 2:
            continue
        item = a.find_parent("div", class_="item")
        case_id_el = item.select_one(".bottom-content a.case-id") if item is not None else None
        case_id_m = re.search(r"#(\d+)", case_id_el.get_text(" ", strip=True)) \
            if case_id_el is not None else None
        case_id = (case_id_m.group(1) if case_id_m is not None
                  else hashlib.sha256(imgs[0].get("src", "").encode()).hexdigest()[:8])
        before_src = _datocms_bare_url(imgs[0].get("src", ""))
        after_src = _datocms_bare_url(imgs[1].get("src", ""))
        if not before_src or not after_src:
            continue
        case = CaseData(case_id=case_id, source_url=source_url)
        case.pairs.append(ImagePair(key="pair1", before_url=before_src, after_url=after_src))
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# drteitelbaum parser (Studio 3/DatoCMS; paginated listing, 2x3 grid)
# ---------------------------------------------------------------------------


def drteitelbaum_parse_listing_page(listing_html: str, source_url: str) -> list[CaseData]:
    """One 2x3 grid composite per case (row 0 = before, row 1 = after,
    columns = front/oblique/side, labels baked into the pixels and
    confirmed by visual inspection). No spec text documented."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for a in soup.select("div.partial_gallery_default_item_index a.item.single"):
        img = a.select_one("picture img")
        if img is None:
            continue
        src = _datocms_bare_url(img.get("src", ""))
        if not src:
            continue
        alt = img.get("alt", "")
        m = re.search(r"Patient\s+(\S+)", alt)
        case_id = (m.group(1) if m else
                  a.get("href", "").rstrip("/").rsplit("/", 1)[-1] or
                  hashlib.sha256(src.encode()).hexdigest()[:8])
        case = CaseData(case_id=case_id, source_url=source_url)
        for col, view in enumerate(("front", "oblique", "side")):
            case.pairs.append(ImagePair(key=view, before_url=src, after_url=src,
                                        grid_shape=(2, 3), before_cell=(0, col),
                                        after_cell=(1, col), view_hint=view))
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# mya parser (single inline listing; click-to-reveal pairs, imgix)
# ---------------------------------------------------------------------------

MYA_ID_RE = re.compile(r'-([A-Za-z]+-MYA\d+)-', re.I)


def mya_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for i, row in enumerate(soup.select("div.before-and-after div.flex.flex-row"), 1):
        slides = row.select("div.click-to-reveal-slide")
        if len(slides) < 2:
            continue
        before_img = slides[0].find("img")
        after_img = slides[1].find("img")
        if before_img is None or after_img is None:
            continue
        before_src, after_src = before_img.get("src", ""), after_img.get("src", "")
        if not before_src or not after_src:
            continue
        m = MYA_ID_RE.search(unquote(before_src))
        case_id = m.group(1) if m else f"pair{i}"
        case = CaseData(case_id=case_id, source_url=source_url)
        case.pairs.append(ImagePair(key="pair1", before_url=before_src, after_url=after_src))
        specs = CaseSpecs()
        row_text = row.get_text(" ", strip=True)
        if "cc" in row_text.lower():
            specs.summary = row_text
            specs.left_cc, specs.right_cc = parse_fill_volumes(row_text)
            classify_brand_shape_profile(specs, row_text)
        case.specs = specs
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# choice parser (Webflow lightbox gallery; one JSON manifest per case)
# ---------------------------------------------------------------------------

# The gallery segregates its procedures into sibling galleries of their own
# (/gallery/breast/mastopexy, /gallery/mummy-makeover, /gallery/breast/
# breast-reduction, /gallery/breast/fat-transfer-to-breasts, ...), so the
# augmentation listing is expected to be pure. Expected is not verified: the
# Etna batch lost 86 combined cases to exactly that assumption, so every
# narrative is screened on its own words. A term is a disqualifier only where
# it names a procedure this patient had - 'breast reduction' in a sentence
# about what she did NOT want is not one, which is what the negation guard is
# for.
CHOICE_COMBINED_RE = re.compile(
    r"\b(?:mastopexy|breast\s+(?:lift|uplift|reduction)|mummy\s+makeover|"
    r"mommy\s+makeover|abdominoplasty|tummy\s+tuck|liposuction|"
    r"fat\s+transfer|lipofilling|areola\s+reduction|"
    r"(?:implant|breast)\s+revision|explant)\b", re.I)
# 'she did not want a breast lift', 'without a mastopexy', 'rather than a
# breast reduction' - a mention that explicitly rules the procedure out.
CHOICE_NEGATION_RE = re.compile(
    r"\b(?:without|instead\s+of|rather\s+than|avoided|avoiding|declined|"
    r"not\s+want\w*|didn.t\s+want|no\s+need\s+for)\b", re.I)


def choice_screen_purity(text: str) -> str | None:
    """The combined-procedure term this narrative reports, or None if pure.

    Screens the CLINIC'S OWN TEXT, per the standing captain ruling; this
    gallery publishes no per-case slug or chart to screen instead.
    """
    for m in CHOICE_COMBINED_RE.finditer(text):
        # Scope the negation to this mention's own clause, not the whole
        # narrative: a later sentence that says 'without a lift' must not
        # clear an earlier sentence that reports one.
        clause = re.split(r"[.;]", text[:m.start()])[-1]
        if CHOICE_NEGATION_RE.search(clause):
            continue
        return m.group(0)
    return None


def choice_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """Webflow lightbox gallery, every case inline on one page.

    Each case is a `div.gallery-div` holding a `w-lightbox` anchor whose
    `script.w-json` manifest enumerates that case's images, and a
    `div.text-block-14` narrative. Reading the manifest rather than the
    thumbnail `<img>` matters twice over: the manifest lists EVERY view (the
    thumbnail shows one), and it carries the bare original URL while the
    thumbnail's `srcset` offers `-p-500`/`-p-800` downscales that would land
    under ingest.py's 400px floor once the composite is split.

    Every image is a side-by-side before|after composite (910x~502) finished
    with a caption band printing BEFORE under the left half and AFTER under
    the right. That band is a label leak in the most literal form available,
    so `bottom_crop_px` trims it off both halves - see ClinicConfig.

    Views are not documented anywhere on the page or in the filenames, so
    every view label comes from an annotations file.

    The listing republishes one case twice: cases 16 and 17 carry identical
    narratives and identical image basenames, differing only in serving the
    copies from the clinic's retired Webflow bucket (which now 403s). The
    page gives its own tell - both blocks reuse the lightbox id
    'lighttest16'. A duplicate is dropped rather than emitted, because
    build_dataset.py splits train/val BY PATIENT and one patient under two
    case ids defeats that split.
    """
    soup = BeautifulSoup(listing_html, "html.parser")
    cases: list[CaseData] = []
    seen_narratives: dict[str, str] = {}
    for i, block in enumerate(soup.select("div.gallery-div"), 1):
        manifest = block.find("script", class_="w-json")
        if manifest is None or not manifest.string:
            continue
        try:
            items = json.loads(manifest.string).get("items", [])
        except json.JSONDecodeError:
            continue
        text_el = block.select_one("div.text-block-14")
        narrative = text_el.get_text(" ", strip=True) if text_el is not None else ""
        case_id = f"case{i}"
        case = CaseData(case_id=case_id, source_url=source_url)

        key = re.sub(r"\s+", " ", narrative).strip().lower()
        if key and key in seen_narratives:
            case.warnings.append(
                f"duplicate case: identical narrative to {seen_narratives[key]}; "
                "not emitted (one patient under two case ids would defeat the "
                "by-patient train/val split)")
            case.specs.summary = narrative
            cases.append(case)
            continue
        if key:
            seen_narratives[key] = case_id

        combined = choice_screen_purity(narrative)
        if combined is not None:
            case.warnings.append(
                f"not pure breast augmentation (narrative reports {combined!r})")
            case.specs.summary = narrative
            cases.append(case)
            continue

        for n, item in enumerate(items, 1):
            url = item.get("url", "")
            if url:
                case.pairs.append(ImagePair(key=f"pair{n}", before_url=url,
                                            after_url=url, split_composite=True))
        specs = CaseSpecs()
        specs.summary = narrative
        specs.left_cc, specs.right_cc = parse_fill_volumes(narrative)
        classify_brand_shape_profile(specs, narrative)
        # 'above the muscle' / 'under the muscle' are prose, not this clinic's
        # documented placement value, so placement is deliberately left unset
        # (see PLACEMENT_PATTERNS).
        case.specs = specs
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# Page 1 Solutions parser (shared platform; see scripts/page1_solutions.py)
# ---------------------------------------------------------------------------


def page1_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    """One Page 1 Solutions case: labelled chart + positional Before/After singles.

    The markup contract, the case-key rule and the purity screen live in
    page1_solutions.py; this is the glue that turns them into CaseData. A case
    whose documented procedure is not a pure primary augmentation is returned
    with no pairs and a warning naming the rejection class, so the driver
    accounts for it instead of silently emitting it.
    """
    case = CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")
    entry = soup.select_one("div.patient-entry")
    if entry is None:
        case.warnings.append("no patient-entry block")
        return case
    content = entry.select_one("div.single-content")
    fields, narrative = p1.page1_split_chart(
        p1.page1_chart_lines(content) if content is not None else [])

    specs = CaseSpecs()
    specs.fields = dict(fields)
    specs.summary = narrative
    specs.gender = fields.get("Gender", "")
    if fields.get("Age", "").isdigit():
        specs.age = int(fields["Age"])
    specs.height = fields.get("Height", "")
    m = re.match(r"(\d+)", fields.get("Weight", ""))
    if m:
        specs.weight_lbs = int(m.group(1))
    m = re.match(r"(\d+(?:\.\d+)?)", fields.get("Months Post-Op", ""))
    if m:
        specs.months_post_op = float(m.group(1))
    specs.height_cm = height_to_cm(specs.height)
    if specs.weight_lbs is not None:
        specs.weight_kg = pounds_to_kg(specs.weight_lbs)
    # 'Left Implant Size: 410 cc' is a LABELLED implant-size field, so a bare
    # number there counts as a volume; a bare number in the narrative does not,
    # which is why the sides are read from the chart first and the narrative is
    # only the fallback (and parse_fill_volumes there still demands a unit).
    specs.left_cc = _page1_labelled_cc(fields.get("Left Implant Size", ""))
    specs.right_cc = _page1_labelled_cc(fields.get("Right Implant Size", ""))
    if specs.left_cc is None and specs.right_cc is None and narrative:
        specs.left_cc, specs.right_cc = parse_fill_volumes(narrative)
    elif specs.left_cc is None:
        specs.left_cc = specs.right_cc
    elif specs.right_cc is None:
        specs.right_cc = specs.left_cc
    # Shape/profile/brand come from the CHART's own labelled fields, not the
    # narrative and not the whole chart: the prose routinely names the implant
    # line ('Mentor MemoryShape ... anatomic (tear drop) shaped') while the
    # chart states what this patient received, and reading only the three
    # fields that document these keeps a value from leaking in from a
    # neighbouring one.
    profile_text, profile_is_sided = p1.page1_profile(fields)
    classify_brand_shape_profile(specs, " ".join([
        fields.get("Implant Type", ""),
        fields.get("Implant Shape", "") or fields.get("Shape", ""),
        "" if profile_is_sided else profile_text,
    ]))
    if specs.profile is None and not profile_is_sided:
        specs.profile = p1.page1_bare_profile(profile_text)
    chart_text = " ".join(fields.values())
    classify_placement_incision(specs, chart_text)
    case.specs = specs

    rejection = p1.page1_purity(fields, narrative)
    if rejection is not None:
        case.warnings.append(f"not pure augmentation ({rejection})")
        return case
    for i, (before_url, after_url) in enumerate(p1.page1_pair_urls(entry), 1):
        case.pairs.append(ImagePair(key=f"pair{i}", before_url=before_url,
                                    after_url=after_url))
    if not case.pairs:
        case.warnings.append("no usable image pairs")
    return case


def _page1_labelled_cc(value: str) -> float | None:
    """Volume from a labelled implant-size field ('410 cc', '215cc', '410')."""
    m = re.match(r"(\d+(?:\.\d+)?)", value.strip())
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# blaine parser (bespoke WordPress [gallery] shortcode + colorbox lightbox)
# ---------------------------------------------------------------------------
#
# Markup contract (blaineplasticsurgery.com/before-and-after/breast-procedures/
# breast-augmentation/). Every case is inline on ONE page - no pagination, no
# AJAX, and the gallery publishes no total of its own, so the enumerated figure
# count IS the gallery and there is nothing to reconcile it against:
#
#     <figure class="gallery-item">
#       <div class="gallery-icon landscape">
#         <a title="Case: #2132 Age: 20 Procedure: ..." href="<FULL ORIGINAL>">
#           <img src="<540px thumbnail>" alt="front view of a female patient
#                before and after Breast augmentation">
#       <figcaption class="wp-caption-text gallery-caption">
#         <p class="h3">Case #2132</p>
#         <ul><li><strong>Age:</strong> 20</li>
#             <li><strong>Procedure:</strong> Breast Augmentation with 300cc
#                 Sientra High Profile, Submuscular</li></ul>
#
# The <a href> is the full-resolution original; the <img src> is a 540px-wide
# WordPress thumbnail that would fail the ingest floor on every case, so only
# the href is ever used.
#
# Four things here are not guessable and each costs real pairs if assumed:
#
# 1. **'Case #N' is not a patient key.** The practice reused three numbers
#    across two generations of the gallery, so #36, #114 and #115 each carry
#    two DIFFERENT patients (e.g. #115 is both a 36-yo with 'Sientra 415 HP'
#    and the older '<surname>_115_view*' patient with '325 CC'). The patient key
#    is (case number, Procedure text) - the Procedure line is the clinic's own
#    per-patient chart line and differs between them - and the later group
#    takes a 'b'/'c' suffix, emitting as case '115b'. build_dataset.py splits
#    train/val BY PATIENT, so collapsing these would put one person in both
#    halves of the split.
#
# 2. **The Procedure field is not the whole disclosure.** Case #111 publishes
#    'Breast Augmentation with moderate plus profile 450cc silicone implants'
#    in Procedure while every one of its five images carries alt='Case #111
#    Mommy Makeover'. Screening the Procedure text alone admits it. The screen
#    therefore reads the whole figure - anchor title, image alt and caption -
#    per BLAINE_COMBINED_RE.
#
# 3. **Not every linked original is a two-panel before|after composite.** Two
#    cases publish a multi-timepoint STRIP - '#25 ... Patient at pre op, 1
#    month post op, 3 months post op, and 6 months post op' is four panels in
#    one file - and a midpoint split glues two timepoints into each half. The
#    clinic documents this in its own Procedure text, which is what
#    BLAINE_TIMEPOINT_STRIP_RE reads. (Measured backstop: no genuine 2-panel
#    composite in this gallery exceeds an aspect ratio of 3.6 and the two
#    strips are 4.3 and 5.5, so the two signals agree.)
#
# 4. **Most of the gallery states an implant size with no unit.** 229 of 287
#    figures carry an explicit 'cc'; the rest read '335 Sientra Moderate Plus'
#    or 'Sientra 330HP'. A bare number in free prose is not a documented volume
#    and 'Procedure' is not a labelled implant-size field, so those cases carry
#    no volume_cc and ingest.py rejects them on its required-field check.
#    Nothing is inferred to fill it.
#
# Views: the image alt names 'front' or 'side', and 'front' is taken as the
# clinic's own view label. 'side' is NOT - this gallery uses it for every
# non-front view, so a 5-view case reads front + 4x'side' covering both
# obliques and both sides, and it never states laterality. Every non-front view
# therefore comes from the visual-annotation pass, keyed by the image filename
# stem (the per-patient filenames are positional - '<surname>_115_viewA'..'viewE',
# '...-1'..'-5', 'blaine_beforeafter36a'..'e' - and a positional token
# documents nothing).

BLAINE_CASE_RE = re.compile(r"Case\s*#?\s*(\d+)", re.I)

# Combined procedures, excluded by the standing captain ruling. Matched against
# the whole figure text, not just Procedure - see note 2 above.
BLAINE_COMBINED_RE = re.compile(
    r"\b(?:lift|mastopex\w*|reduction|revision|explant\w*|capsulectomy|"
    r"lipo\w*|abdominoplasty|tummy\s+tuck|makeover|mastectomy|reconstruction|"
    r"fat\s+(?:transfer|graft\w*)|implant\s+(?:removal|exchange|replacement))\b",
    re.I)

# The clinic's own text for a multi-timepoint strip: a Procedure line that
# enumerates a pre-op plus two or more post-op timepoints in one figure.
BLAINE_TIMEPOINT_STRIP_RE = re.compile(
    r"\bpre[\s-]?op\b.*?\bpost[\s-]?op\b.*?\bpost[\s-]?op\b", re.I | re.S)

# The image alt's view word. Only 'front' maps to a schema view; see the note
# above for why 'side' cannot.
BLAINE_ALT_FRONT_RE = re.compile(r"\bfront\b", re.I)

# Two-letter projection abbreviations, decoded per the captain's 2026-08-19
# ruling (MP -> moderate, HP -> high). Matched CASE-SENSITIVELY and either as a
# standalone token or glued to the size ('Sientra 330HP', '455 MP+'), so no
# lowercase word can trip them. 'MP+' is tried first: this gallery writes both
# '450cc MP' and '455 MP+', and reading the second as plain moderate would lose
# the distinction the clinic drew. Mentor's 'Xtra' is deliberately absent - it
# is a product line, not a projection, and case #158 ('Moderate Plus Profile
# Xtra') takes moderate-plus from the spelled-out words instead.
BLAINE_PROFILE_ABBREVS = [
    (re.compile(r"(?:\b|(?<=\d))MP\s*\+"), "moderate-plus"),
    (re.compile(r"(?:\b|(?<=\d))MP\b"), "moderate"),
    (re.compile(r"(?:\b|(?<=\d))HP\b"), "high"),
]

# 'one month post-op' as well as '6 months post'. Only the spellings this
# gallery publishes; a week figure ('Time post op 1 week') is left unconverted
# rather than turned into a fraction of a month the clinic never stated.
BLAINE_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_BLAINE_NUM = r"(\d+|" + "|".join(BLAINE_WORD_NUMBERS) + r")"
BLAINE_POST_OP_RE = re.compile(
    r"\b" + _BLAINE_NUM + r"\s*(month|year)s?\b(?=[\s,]*post)", re.I)
BLAINE_POST_OP_TRAILING_RE = re.compile(
    r"\btime\s+post\s*-?\s*op\s*:?\s*" + _BLAINE_NUM + r"\s*(month|year)s?\b", re.I)

# Side markers as this gallery writes them, including the 'Rt'/'Lt' spelling
# ('(L) 415cc HP, (Rt) 440cc HP') the shared side alternation does not carry.
BLAINE_SIDE_RE = re.compile(r"\b(left|right|lt|rt|l|r)\b", re.I)
BLAINE_CLAUSE_SPLIT_RE = re.compile(r"[,;.]|\band\b", re.I)


def blaine_volumes(text: str) -> tuple[float | None, float | None]:
    """(left_cc, right_cc) from a Blaine Procedure line.

    The shared parse_fill_volumes() reads the phrasings other clinics publish;
    Blaine attaches the side AFTER the volume, inside the same clause -
    '400 CC silicone implant (right) and 450 CC silicone implant (left)',
    '475 CC left, 450 CC right round silicone gel implants', 'left breast
    saline implant filled to 400 CC, and right breast saline implant filled to
    500 CC' - which that helper reads as a prefix marker and pairs with the
    wrong number (case #117 comes out 450/None instead of 450/400). Clause
    scoping is what this gallery's own punctuation supports, so it is done here
    rather than by widening a helper five other clinics depend on.

    The UNIT rule is unchanged and still shared: only a figure carrying an
    explicit cc/ml (or gram) unit counts, via _parse_fill_side's VOLUME_UNIT.
    'Sientra 330HP' states no unit and yields nothing.
    """
    left = right = None
    loose: list[float] = []
    for clause in BLAINE_CLAUSE_SPLIT_RE.split(text):
        cc = _parse_fill_side(clause)
        if cc is None or not (100 <= cc <= 1000):
            continue
        sides = {m.group(1)[0].lower() for m in BLAINE_SIDE_RE.finditer(clause)}
        if sides == {"l"}:
            left = cc
        elif sides == {"r"}:
            right = cc
        else:
            # No side named, or both named in one clause: not attributable.
            loose.append(cc)
    if left is not None or right is not None:
        return left, right
    if len(loose) == 1:
        return loose[0], loose[0]
    if len(loose) >= 2:
        # Two volumes and no sides ('350cc and 280cc Sientra implants'):
        # recorded unattributed, exactly as the shared helper does.
        return loose[0], loose[1]
    return None, None


def blaine_months_post_op(text: str) -> float | None:
    """Months since surgery from the Procedure line, or None."""
    m = BLAINE_POST_OP_RE.search(text) or BLAINE_POST_OP_TRAILING_RE.search(text)
    if m is None:
        return None
    raw = m.group(1).lower()
    value = float(BLAINE_WORD_NUMBERS[raw]) if raw in BLAINE_WORD_NUMBERS else float(raw)
    return value * 12 if m.group(2).lower().startswith("year") else value


# The practice's own misspellings of values it DID document: 'inframmary' for
# the incision in cases #91/#92/#93, 'High Profle' for the projection in case
# #15. Repairing these for the matchers reads what the clinic wrote; it is not
# the same as inferring a value it never stated, and specs.summary still keeps
# the line verbatim so notes.md-style provenance survives into meta.json.
BLAINE_SPELLINGS = [
    (re.compile(r"\binframmary\b", re.I), "inframammary"),
    (re.compile(r"\bprofle\b", re.I), "profile"),
]


def blaine_normalise(text: str) -> str:
    for pattern, replacement in BLAINE_SPELLINGS:
        text = pattern.sub(replacement, text)
    return text


def blaine_parse_procedure(text: str) -> CaseSpecs:
    specs = CaseSpecs()
    specs.summary = text
    normalised = blaine_normalise(text)
    specs.left_cc, specs.right_cc = blaine_volumes(normalised)
    classify_brand_shape_profile(specs, normalised)
    if specs.profile is None:
        for pattern, profile in BLAINE_PROFILE_ABBREVS:
            if pattern.search(normalised):
                specs.profile = profile
                break
    # The Procedure line is a terse chart line naming THIS patient's placement
    # and incision ('submuscular inframammary Breast Augmentation with ...'),
    # not a narrative walking through the options, so it counts as chart text
    # for classify_placement_incision.
    classify_placement_incision(specs, normalised)
    specs.months_post_op = blaine_months_post_op(normalised)
    return specs


def blaine_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """One CaseData per (case number, Procedure text) group - see note 1."""
    soup = BeautifulSoup(listing_html, "html.parser")
    groups: dict[tuple[str, str], CaseData] = {}
    numbers_seen: dict[str, int] = {}
    for figure in soup.select("figure.gallery-item"):
        anchor = figure.select_one("div.gallery-icon a[href]")
        caption = figure.select_one("figcaption")
        if anchor is None or caption is None:
            continue
        heading = caption.find("p")
        m = BLAINE_CASE_RE.search(heading.get_text(" ", strip=True) if heading else "")
        if m is None:
            continue
        number = m.group(1)
        fields: dict[str, str] = {}
        for item in caption.select("li"):
            label = item.find("strong")
            if label is None:
                continue
            name = label.get_text(" ", strip=True)
            fields[name.rstrip(":").strip()] = item.get_text(
                " ", strip=True)[len(name):].strip()
        procedure = fields.get("Procedure", "").strip()
        img = figure.find("img")
        alt = (img.get("alt", "") if img is not None else "") or ""
        haystack = " ".join(
            (anchor.get("title", "") or "", alt, caption.get_text(" ", strip=True)))
        filename = unquote(anchor["href"]).rsplit("/", 1)[-1]

        key = (number, procedure)
        if key not in groups:
            seen = numbers_seen.get(number, 0)
            # 'b' for the second patient published under one case number, 'c'
            # for a third; the first keeps the bare number.
            case_id = number if not seen else number + chr(ord("a") + seen)
            numbers_seen[number] = seen + 1
            case = CaseData(case_id=case_id, source_url=source_url)
            case.specs = blaine_parse_procedure(procedure)
            if fields.get("Age", "").isdigit():
                case.specs.age = int(fields["Age"])
            for label, value in fields.items():
                if label not in ("Age", "Procedure") and value:
                    case.specs.fields[label] = value
            groups[key] = case
        case = groups[key]

        combined = BLAINE_COMBINED_RE.search(haystack)
        if combined is not None:
            case.warnings.append(
                f"{filename}: combined procedure ('{combined.group(0)}' in the "
                "figure text); excluded by the captain ruling on combined "
                "procedures")
            continue
        if BLAINE_TIMEPOINT_STRIP_RE.search(procedure):
            case.warnings.append(
                f"{filename}: Procedure text enumerates a pre-op plus multiple "
                "post-op timepoints, so this figure is a multi-panel strip and "
                "not a before|after composite; not split")
            continue
        stem = re.sub(r"[^\w-]+", "_", filename.rsplit(".", 1)[0])
        case.pairs.append(ImagePair(
            key=stem, before_url=anchor["href"], after_url=anchor["href"],
            split_composite=True,
            view_hint="front" if BLAINE_ALT_FRONT_RE.search(alt) else None))
    return list(groups.values())


# ---------------------------------------------------------------------------
# aips parser (Breakdance page builder; one inline listing, CSS-background pairs)
# ---------------------------------------------------------------------------

# Breakdance renders each before/after pair as a three-column grid row:
#
#   <div class="bde-div-<sec>-<n>">                       <- the row
#     <div class="bde-div-<sec>-<n+1>">                   <- BEFORE slot
#       <img alt="Before" src=".../bna_label_before.png">
#     <div class="bde-div-<sec>-<n+3>">                   <- AFTER slot
#       <img alt="After"  src=".../bna_label_after.png">
#     <div class="bde-div-<sec>-<n+5>"> <rich text>        <- spec chart
#
# The patient photo is NOT the <img>: the <img> is a transparent BEFORE/AFTER
# label overlay, and the photo is the slot div's `background-image`, declared
# in the page's inline <style> keyed by that div's `bde-div-*` class. So the
# stylesheet has to be read to find the images at all.
#
# One slot on the live page breaks that rule and publishes the photo directly
# as the slot's <img src> (with a descriptive alt instead of the label), so
# both spellings are supported; a real <img> wins over the background, because
# where both are present the <img> is what a viewer sees. That case matters:
# the one slot that does it (case 14's third after) ALSO carries a stale
# background left over from case 01, so reading the background there would
# silently emit another patient's photo as this patient's result.
AIPS_BG_RE = re.compile(
    r'\.breakdance\s+\.(bde-div-[\w-]+)\s*\{[^}]*?'
    r'background-image:\s*url\(\s*["\']?([^"\')]+)["\']?\s*\)')
AIPS_LABEL_IMAGE = "bna_label_"
# Case id and shoot token off the asset name: 'bna_braug_07_P9114754.jpg'.
# The digits must follow 'braug_' directly - the clinic's augmentation-WITH-
# LIFT cases are published as 'bna_braug_masto_NN_...' on a separate gallery,
# and a looser id pattern would read them as augmentation cases.
AIPS_ASSET_RE = re.compile(r'bna_braug_(\d+)_([^/]+?)(?:_\d+)?\.\w+$', re.I)
AIPS_FIELD_LABELS = {
    "AGE", "HEIGHT", "CHILDREN", "TYPE", "PROFILE",
    "SHELL", "SIZE", "INCISION", "PLANE",
}


def _aips_div_class(el) -> str | None:
    for name in el.get("class", []):
        if name.startswith("bde-div-"):
            return name
    return None


def _aips_slot_url(slot, backgrounds: dict[str, str]) -> str | None:
    """The patient photo in one before/after slot, or None."""
    img = slot.find("img")
    if img is not None:
        src = img.get("src", "")
        if src and AIPS_LABEL_IMAGE not in src:
            return src
    return backgrounds.get(_aips_div_class(slot) or "")


def aips_parse_specs(spec_el) -> CaseSpecs:
    """CaseSpecs from one '<p><strong>LABEL:</strong> value</p>' chart.

    Every case publishes the same nine labels and every one of them carries a
    value, so a missing or unknown label is a markup change worth noticing
    rather than something to absorb silently.

    Only SIZE, PROFILE, SHELL, INCISION, AGE and HEIGHT map onto the schema.
    TYPE (Silicone/Saline) is the fill material, which the schema does not
    record, and CHILDREN is parity - both are kept verbatim in the notes.

    PLANE deliberately does NOT become `placement`. 'Under Muscle' is prose
    covering both submuscular and dual-plane, and 'Above Muscle' covers both
    subglandular and subfascial, so decoding either into the schema's enum
    would invent a distinction the clinic never published. This is the same
    call the module already makes for 'over the muscle' (see
    classify_placement_incision).
    """
    specs = CaseSpecs()
    if spec_el is None:
        return specs
    for p in spec_el.find_all("p"):
        strong = p.find("strong")
        if strong is None:
            continue
        label = strong.get_text(" ", strip=True).rstrip(":").strip().upper()
        value = p.get_text(" ", strip=True)[len(strong.get_text(" ", strip=True)):]
        value = value.strip().strip(":").strip()
        if not label or not value:
            continue
        specs.fields[label.title()] = value
        if label == "AGE" and value.isdigit():
            specs.age = int(value)
        elif label == "HEIGHT":
            specs.height = value
            specs.height_cm = height_to_cm(value)
        elif label == "SIZE":
            specs.left_cc, specs.right_cc = parse_fill_volumes(value)
    # Chart text for the shared classifiers. SHELL ('Smooth, Round') carries
    # the shape and PROFILE the projection; INCISION is a documented chart
    # field, so classify_placement_incision may read it.
    chart = ". ".join(f"{k}: {v}" for k, v in specs.fields.items())
    classify_brand_shape_profile(specs, chart)
    classify_placement_incision(specs, chart)
    return specs


def aips_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """Every case of the whole gallery, inline on one page.

    Rows are grouped into cases by the case number in the asset filename, in
    first-appearance order; the page itself carries no case markup, no case
    ids, no pagination and no declared total.

    Views are not documented anywhere - not in the markup, not in the alt
    text, not in the filenames (which are the camera's own DCIM names) - so
    every pair needs a view annotation. Pair keys are therefore the before and
    after shoot tokens ('P5192216-P7213664'), which are stable across re-runs
    and unique within a case even where the clinic republishes one photo in
    two rows.
    """
    backgrounds = dict(AIPS_BG_RE.findall(listing_html))
    soup = BeautifulSoup(listing_html, "html.parser")
    cases: dict[str, CaseData] = {}
    for label_img in soup.select('img[alt="Before"]'):
        slot = label_img.parent
        row = slot.parent
        slots = row.find_all("div", recursive=False)
        if len(slots) < 2 or slots[0] is not slot:
            continue
        before_url = _aips_slot_url(slots[0], backgrounds)
        after_url = _aips_slot_url(slots[1], backgrounds)
        if not before_url or not after_url:
            continue
        m_before = AIPS_ASSET_RE.search(before_url)
        m_after = AIPS_ASSET_RE.search(after_url)
        if m_before is None or m_after is None:
            continue
        case_id, before_token = m_before.group(1), m_before.group(2)
        after_case_id, after_token = m_after.group(1), m_after.group(2)
        case = cases.get(case_id)
        if case is None:
            case = cases[case_id] = CaseData(case_id=case_id, source_url=source_url)
        # A row whose two halves belong to different patients is a page-build
        # error, not a pair. The clinic's own lift gallery has one (it pairs
        # augmentation case 13's 'before' with a mastopexy case's 'after'), so
        # this is a live failure mode rather than a hypothetical.
        if after_case_id != case_id:
            case.warnings.append(
                f"row {before_token}: after image belongs to case "
                f"{after_case_id}, not {case_id}; dropped")
            continue
        # The same photo republished in two rows means at most one of those
        # rows is a real pair, and the markup does not say which - only the
        # images do. Case 16 is the live example: its 'before' oblique is
        # published against both the front AFTER and the oblique AFTER,
        # because the clinic never published a front before at all. So the
        # rows are kept and flagged rather than guessed at or dropped: a pair
        # is only ever emitted once the view-annotation pass has looked at
        # both halves, which is exactly the judgement this needs.
        duplicate = next(
            (q for q in case.pairs
             if q.before_url == before_url or q.after_url == after_url), None)
        if duplicate is not None:
            case.warnings.append(
                f"row {before_token}-{after_token} republishes an image also "
                f"used by row {duplicate.key}; at most one of the two is a "
                f"real pair - check both halves before annotating either")
        case.pairs.append(ImagePair(key=f"{before_token}-{after_token}",
                                    before_url=before_url, after_url=after_url))
        if not case.specs.fields:
            spec_el = (slots[2].find(class_="bde-rich-text")
                       if len(slots) > 2 else None)
            specs = aips_parse_specs(spec_el)
            if specs.fields:
                missing = AIPS_FIELD_LABELS - {k.upper() for k in specs.fields}
                if missing:
                    case.warnings.append(
                        f"spec chart missing {sorted(missing)}")
                case.specs = specs
    for case in cases.values():
        if not case.specs.fields:
            case.warnings.append("no spec chart found for this case")
    return list(cases.values())


# ---------------------------------------------------------------------------
# sculpted parser (bespoke WordPress; paginated inline listing of composites)
# ---------------------------------------------------------------------------
#
# Sculpted (Gold Coast QLD, AU) publishes one `div.patientgallery-list` per
# page holding a wrapper div per case: a `.patientgallery-title`, a
# `.patientgallery-slider` of exactly three `<a data-fancybox>` links to the
# full-size images, and a `.patientgallery-captions` paragraph. Pagination is
# WordPress `/page/N`; the last page 200s with an EMPTY list rather than 404ing,
# so the walk terminates on "no case blocks", never on a status code.
#
# Two things about this gallery are not guessable and cost data if assumed:
#
# 1. THE PUBLISHED "Patient N" TITLE IS A DISPLAY INDEX, NOT THE CASE'S IDENTITY.
#    Page 1's "Patient 2" is served from `Breast-Implants-Patient_16_*.jpg`, and
#    the numbers run in opposite directions: display 1..13 maps to asset
#    14,16,7,12,11,9,19,8,5,4,3,N7,2. The display index is a position in a
#    reverse-chronological list and shifts the moment the practice publishes a
#    new case, so keying on it would silently re-point every pair id on a
#    re-scrape. The asset stem is baked into an immutable wp-content upload
#    path, so THAT is the case key; the display index is recorded in the notes.
#
# 2. THE ASSET STEM IS SPELLED THREE DIFFERENT WAYS across the 13 cases -
#    `Patient_7_Front`, `patient-19-front`, `patient_N7_Front` - so the stem is
#    matched case-insensitively over both separators and the number may carry a
#    non-numeric prefix ('N7'). All three spellings are pinned by fixtures.
#
# The view is documented twice and agreeably: in the filename token
# (Front/Angle/Side) and again in the alt text ('..., patient 16, front view').
# Angle is this gallery's word for oblique. Neither documents LATERALITY, so
# oblique/side pairs carry a bare view_hint and reach the corpus only with a
# visual-inspection annotation (CLAUDE.md's anchored-landmark rule); front
# needs none.
#
# Every image is a landscape side-by-side before|after composite split at the
# midpoint by the shared `split_composite_image`.

SCULPTED_ASSET_RE = re.compile(
    r"Breast-Implants-patient[-_]([A-Za-z]?\d+)[-_](front|angle|side)", re.I)
SCULPTED_VIEW_MAP = {"front": "front", "angle": "oblique", "side": "side"}
# WordPress writes its resized derivatives as '<stem>-768x432.jpg'; the
# fancybox href is already the bare original, but strip the suffix anyway so a
# markup change that starts linking a derivative cannot silently halve the
# resolution.
SCULPTED_SIZE_SUFFIX_RE = re.compile(r"-\d+x\d+(?=\.\w+$)")
SCULPTED_DISPLAY_RE = re.compile(r"patient\s*(\d+)", re.I)
SCULPTED_AGE_RE = re.compile(r"\b(\d{2})\s*(?:F\b|year[- ]old)", re.I)
SCULPTED_POSTOP_RE = re.compile(
    r"photos?\s+taken\s+at\s+(?:about\s+)?(\d+(?:\.\d+)?)\s*(week|month|year)s?\b", re.I)
# Captain ruling: pure breast augmentation only. Screened on the CASE TEXT, not
# the filename slug - every one of these images is published under the same
# `Breast-Implants-` prefix whatever the case actually was, so the slug carries
# no procedure information at all here and a slug screen would pass everything.
# A bare 'lift' is deliberately NOT a match - clinics write 'a natural lift' and
# 'lifted appearance' about what implants alone do - so a lift only counts when
# it is named as a procedure this patient had ('breast lift', 'mastopexy', or a
# lift conjoined to the augmentation: 'with a lift', 'and a lift', '+ lift').
SCULPTED_COMBINED_RE = re.compile(
    r"\b(?:mastopexy|breast\s+lift"
    r"|mommy\s+makeover|reduction|liposuction|lipo(?:filling|sculpture)?"
    r"|abdominoplasty|tummy\s+tuck|fat\s+transfer|explant|revision"
    r"|reconstruction|implant\s+(?:removal|exchange|replacement))\b"
    r"|(?:\b(?:with|and|plus)\s+(?:a\s+)?(?:breast\s+)?|\+\s*)lift\b", re.I)
# Months are the schema's unit; a documented figure in weeks or years is
# converted (the same class of unit conversion as height_to_cm/pounds_to_kg),
# and the verbatim sentence survives in specs.summary either way.
SCULPTED_POSTOP_UNIT_MONTHS = {"week": 7 / 30.44, "month": 1.0, "year": 12.0}

# Every composite is 1200x675 and carries a printed white presentation frame:
# ~8px on the left, right and top edges, ~4px at the bottom, and a ~9px white
# gutter straddling the midpoint. Measured across all 39 published images, not
# assumed - the trim below (10px outer, 12px each side of the midpoint) clears
# it on every one of the 78 halves, leaving no near-white band on any cropped
# edge. Halves come out 578x655, well clear of ingest.py's 400px floor, and the
# trim is symmetric so before and after stay dimension-matched. A fixed inset
# beats an adaptive one here: the backdrop is a near-white wall, so an
# auto-trim that chases "white" eats body pixels.
SCULPTED_BORDER_PX = 10
SCULPTED_GUTTER_PX = 12

# Per-case exclusions that the CASE TEXT cannot express, so they cannot be
# derived and are enumerated instead - each one is a finding from opening the
# published images at native resolution (evidence:
# ~/firstmate/data/ba-viz-collect-sculpted/report.md). Deleting an entry
# re-admits that case's three pairs on the next run.
SCULPTED_VISUAL_EXCLUSIONS = {
    # A deliberate mosaic block over an identifying mark on the lateral chest,
    # present in BOTH halves of all three views and sitting on the
    # inferolateral breast border in the oblique and side. censorship.py does
    # NOT catch it (it flags ten unrelated halves at this clinic and misses
    # this one), so the hold has to be named here. Standing rule for a censored
    # image is reject, not crop around it.
    "14": "mosaic censoring over an identifying mark on the body, all 3 views",
    # The published BEFORE photo shows a healed vertical/Wise-pattern mastopexy
    # scar set - periareolar, vertical and inframammary scars on both breasts.
    # The clinic's text names only 'Bilateral Breast Augmentation with 300cc
    # round implants', so the text purity screen passes it; the images say the
    # baseline is a previously lifted breast. Withheld rather than dropped: the
    # before->after delta really is implants only, so this is a captain call on
    # whether a secondary augmentation over a mastopexy belongs in the corpus.
    "n7": "before photo shows a pre-existing mastopexy scar set; withheld "
          "pending a captain ruling on secondary augmentation",
}


def sculpted_case_blocks(soup) -> list:
    """The per-case wrapper divs on one listing page, in published order."""
    return [slider.parent for slider in soup.select("div.patientgallery-slider")
            if slider.parent is not None]


def sculpted_parse_listing_page(listing_html: str, source_url: str) -> list[CaseData]:
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for block in sculpted_case_blocks(soup):
        title_el = block.select_one("div.patientgallery-title")
        caption_el = block.select_one("div.patientgallery-captions")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        caption = (" ".join(caption_el.get_text(" ", strip=True).split())
                   if caption_el else "")
        # The caption repeats its own display index as a 'Patient N :' prefix;
        # drop it so it cannot be read as part of the clinical description.
        body = caption.split(":", 1)[1].strip() if ":" in caption else caption

        pairs, stems, warnings = [], [], []
        for anchor in block.select("div.patientgallery-slider a[href]"):
            href = SCULPTED_SIZE_SUFFIX_RE.sub("", anchor["href"])
            m = SCULPTED_ASSET_RE.search(href.rsplit("/", 1)[-1])
            if m is None:
                warnings.append(f"unrecognised image filename {href.rsplit('/', 1)[-1]}")
                continue
            stems.append(m.group(1).lower())
            key = m.group(2).lower()
            pairs.append(ImagePair(key=key, before_url=href, after_url=href,
                                   view_hint=SCULPTED_VIEW_MAP[key],
                                   split_composite=True,
                                   composite_border=SCULPTED_BORDER_PX,
                                   composite_gutter=SCULPTED_GUTTER_PX))
        if not pairs:
            continue
        # One case, one patient: three views that disagree on the asset stem
        # would mean the slider mixes patients, which no pair id could describe
        # honestly. Report it rather than picking a majority.
        if len(set(stems)) != 1:
            warnings.append(
                f"slider mixes asset stems {sorted(set(stems))}; keying on the first")
        case_id = stems[0]

        specs = CaseSpecs(summary=body)
        if body:
            specs.left_cc, specs.right_cc = parse_fill_volumes(body)
            classify_brand_shape_profile(specs, body)
            m = SCULPTED_AGE_RE.search(body)
            if m:
                specs.age = int(m.group(1))
            m = SCULPTED_POSTOP_RE.search(body)
            if m:
                specs.months_post_op = round(
                    float(m.group(1)) * SCULPTED_POSTOP_UNIT_MONTHS[m.group(2).lower()], 1)
        display = SCULPTED_DISPLAY_RE.search(title)
        if display:
            specs.fields["published as"] = f"Patient {display.group(1)}"
        # A case whose text names a second procedure is dropped whole: its after
        # photograph shows a change the implants did not make.
        combined = SCULPTED_COMBINED_RE.search(body)
        if combined:
            warnings.append(
                f"not pure breast augmentation (case text names "
                f"{combined.group(0)!r}); case dropped")
            pairs = []
        elif case_id in SCULPTED_VISUAL_EXCLUSIONS:
            warnings.append(
                f"{SCULPTED_VISUAL_EXCLUSIONS[case_id]}; case dropped")
            pairs = []

        case = CaseData(case_id=case_id, source_url=source_url, pairs=pairs,
                        specs=specs, warnings=warnings)
        cases.append(case)
    return cases


def split_composite_image(data: bytes, border: int = 0, gutter: int = 0,
                          bottom_frac: float = 0.0) -> tuple[bytes, bytes]:
    """Split a side-by-side before|after composite into (before, after) JPEGs.

    The split is the exact horizontal midpoint. Raises ValueError for
    portrait/square images, where a left|right split cannot be assumed.

    `border` and `gutter` trim a printed presentation frame before the split:
    `border` px off each outer edge and `gutter` px off each side of the
    midpoint. Both halves lose exactly the same amount, so a trimmed pair stays
    dimension-matched; a trim that would leave nothing is refused rather than
    silently clamped. The default 0/0 is the raw midpoint split.

    `bottom_frac` trims that fraction of the composite's HEIGHT off its bottom
    before the split, so both halves lose exactly the same rows (see
    `ImagePair.composite_bottom_frac`). It composes with `border`/`gutter`.
    """
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(data))
    if img.width <= img.height:
        raise ValueError(
            f"composite image is not landscape ({img.width}x{img.height}); "
            "cannot assume a left|right before|after split")
    half = img.width // 2
    if border < 0 or gutter < 0:
        raise ValueError("composite border/gutter must not be negative")
    if border + gutter >= half:
        raise ValueError(
            f"composite trim (border={border}, gutter={gutter}) leaves no image "
            f"in a {img.width}x{img.height} composite")
    floor_y = img.height - math.ceil(img.height * bottom_frac)
    top, bottom = border, floor_y - border
    if bottom <= top:
        raise ValueError(
            f"composite trim (border={border}, bottom_frac={bottom_frac}) "
            f"leaves nothing of a {img.width}x{img.height} composite")
    out = []
    for box in ((border, top, half - gutter, bottom),
                (half + gutter, top, img.width - border, bottom)):
        buf = io.BytesIO()
        img.crop(box).convert("RGB").save(buf, format="JPEG", quality=95)
        out.append(buf.getvalue())
    return out[0], out[1]


def crop_fraction(data: bytes, box: tuple[float, float, float, float]) -> bytes:
    """Crop an image to a fractional (left, top, right, bottom) box.

    Fractions rather than pixels because a gallery can serve the same framing
    at several resolutions (drbandy publishes both a 655x491 export and the
    2560x1920 original), and the same fraction crops both to the same picture.
    """
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(data))
    left, top, right, bottom = box
    pixels = (round(left * img.width), round(top * img.height),
              round(right * img.width), round(bottom * img.height))
    buf = io.BytesIO()
    img.crop(pixels).convert("RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def postprocess_pair(cfg: ClinicConfig, before: bytes,
                     after: bytes) -> tuple[bytes, bytes, bool]:
    """Clinic-specific pixel work on a decoded pair; (before, after, applied).

    This is where a burnt-in watermark is cropped away. Both halves always get
    the identical transform: a crop applied to one half only would change the
    pair's geometry, and a watermark left on one half only is a label the model
    can read instead of the anatomy.
    """
    if cfg.kind == "page1solutions":
        import page1solutions

        return (*page1solutions.crop_watermark(before, after), True)
    return before, after, False


def crop_bottom_frac(data: bytes, frac: float) -> bytes:
    """Trim `frac` of the image's WIDTH off the bottom (ClinicConfig.bottom_crop_frac).

    Distinct from `crop_bottom`, which trims an absolute number of PIXEL rows
    (`ClinicConfig.bottom_crop_px`). The two mechanisms coexist because they
    were measured differently per clinic: see AGENTS.md on both bullets.

    Returns the bytes unchanged when frac is 0, so a clinic with no burned-in
    mark keeps the file the site delivered and is not re-encoded here for
    nothing (ingest.py re-encodes everything on the way to staging anyway).
    """
    import io

    from PIL import Image

    if frac <= 0:
        return data
    img = Image.open(io.BytesIO(data))
    keep = img.height - int(round(img.width * frac))
    if keep <= 0:
        raise ValueError(
            f"bottom_crop_frac={frac} removes the whole {img.width}x{img.height} frame")
    buf = io.BytesIO()
    img.crop((0, 0, img.width, keep)).convert("RGB").save(
        buf, format="JPEG", quality=95)
    return buf.getvalue()


def crop_grid_cell(data: bytes, rows: int, cols: int, cell: tuple[int, int]) -> bytes:
    """Crop one (row, col) cell out of a rows x cols grid composite image.

    Used for multi-panel composites (drrohrich's 2x2 front/side x
    before/after; drteitelbaum/skplastic's 2x3 front/oblique/side x
    before/after) where a single fetched image yields several pairs.
    """
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(data))
    cell_w, cell_h = img.width // cols, img.height // rows
    row, col = cell
    box = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
    buf = io.BytesIO()
    img.crop(box).convert("RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Metadata emission
# ---------------------------------------------------------------------------


def build_notes(specs: CaseSpecs, laterality_source: str | None) -> str:
    parts = []
    if specs.summary:
        parts.append(f"Clinic description: {specs.summary}")
    details = []
    if specs.age is not None:
        details.append(f"age {specs.age}")
    if specs.gender:
        details.append(specs.gender)
    if specs.height:
        details.append(f"height {specs.height}")
    if specs.weight_lbs is not None:
        details.append(f"weight {specs.weight_lbs} lbs")
    for label, value in sorted(specs.fields.items()):
        details.append(f"{label}: {value}")
    if specs.left_cc is not None and specs.right_cc is not None:
        if specs.left_cc != specs.right_cc:
            details.append(
                f"asymmetric volumes (left {specs.left_cc:g}cc, "
                f"right {specs.right_cc:g}cc); volume_cc is the average"
            )
    # View-label provenance is kept only alongside real clinical content;
    # on its own it is boilerplate, and notes are omitted entirely.
    if laterality_source and (parts or details):
        details.append(f"view labels: {laterality_source}")
    if details:
        parts.append(". ".join(details) + ".")
    return "\n".join(parts)


def resolve_view(pair: ImagePair, annotations: dict) -> tuple[str | None, str | None]:
    """(schema view, annotation source) for a pair, or (None, ...) to skip."""
    pair_ann = annotations.get("pairs", {}).get(pair.key, {})
    if pair_ann.get("view") in SCHEMA_VIEWS:
        return pair_ann["view"], "visual inspection of downloaded images"
    # Some clinics document laterality directly in the page (e.g. heavenly's
    # older filenames spell out 'Left-Oblique'/'Right-Oblique'); a hint that
    # is already a full schema view needs no annotation.
    if pair.view_hint in SCHEMA_VIEWS:
        return pair.view_hint, None
    if pair.view_hint in ("oblique", "side"):
        laterality = annotations.get("laterality")
        if laterality in ("left", "right"):
            return (f"{pair.view_hint}-{laterality}",
                    "laterality from visual inspection of downloaded images")
    return None, None


def build_meta(pair_id: str, view: str, specs: CaseSpecs, annotations: dict,
               pair_annotations: dict, view_source: str | None,
               consent_ref: str) -> dict:
    meta: dict = {"pair_id": pair_id, "view": view, "consent_ref": consent_ref}
    vol = volume_cc(specs)
    if vol is not None:
        meta["volume_cc"] = vol
    if specs.months_post_op is not None:
        meta["months_post_op"] = specs.months_post_op
    # shape is a required field; 'unknown' (schema enum) when the clinic did
    # not document it - never guessed.
    meta["shape"] = specs.shape if specs.shape is not None else "unknown"
    if specs.brand != "unknown":
        meta["brand"] = specs.brand
    if specs.profile is not None:
        meta["profile"] = specs.profile
    # Chart/frame metadata: curation and evaluation only, never a caption
    # (dataset_schema.json). Omitted rather than written as 'unknown' when the
    # clinic did not document it.
    for chart_field in ("placement", "incision", "height_cm", "weight_kg"):
        value = getattr(specs, chart_field)
        if value is not None:
            meta[chart_field] = value
    clothing = pair_annotations.get("clothing") or annotations.get("clothing")
    if clothing in SCHEMA_CLOTHING:
        meta["clothing"] = clothing
    notes = build_notes(specs, view_source)
    if notes:
        meta["notes"] = notes
    return meta


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _fetch_optional(fetcher: PoliteFetcher, url: str, cache_key: str) -> str | None:
    """GET a URL as text; return None (instead of raising) on a 404, for
    paginated listings where the last page isn't known in advance."""
    try:
        return fetcher.get(url, cache_key).decode("utf-8", "replace")
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def _fetch_seed(fetcher: PoliteFetcher, url: str, cache_key: str) -> str | None:
    """GET a chain-seeding page, or None if it is absent.

    Seeding pages (the gallery index and the sibling category listings) only
    widen the walk's entry points; a missing one costs coverage, never
    correctness, and the declared-total check reports any shortfall. Unlike
    _fetch_optional this also tolerates a missing cache entry, so an offline
    re-parse of a clinic cached before the multi-seed walk existed still runs.
    """
    try:
        return _fetch_optional(fetcher, url, cache_key)
    except FileNotFoundError:
        return None


def collect_cases(cfg: ClinicConfig, fetcher: PoliteFetcher,
                  gallery_endpoint: bool = False,
                  grant_root: Path | None = None) -> list[CaseData]:
    if cfg.kind == "page1solutions":
        # Imported here rather than at module scope: page1solutions imports
        # this module for the shared data model, so a top-level import in both
        # directions would be circular.
        import page1solutions

        gallery_path = cfg.gallery_paths[0]
        listing = fetcher.get(cfg.base_url + gallery_path,
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        cases = []
        for case_id in page1solutions.page1_list_cases(listing):
            url = f"{cfg.base_url}{gallery_path}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(page1solutions.page1_parse_case(html, case_id, url))
        return cases
    if cfg.kind == "drkolker":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        cases = []
        for case_id in kolker_list_cases(listing):
            url = f"{cfg.base_url}{cfg.gallery_paths[0]}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(kolker_parse_case(html, case_id, url))
        return cases
    if cfg.kind == "drdanielbarrett":
        cases = []
        for path in cfg.gallery_paths:
            url = cfg.base_url + path
            html = fetcher.get(url, f"{cfg.slug}_listing_{path.rsplit('/', 1)[-1]}.html"
                               ).decode("utf-8", "replace")
            cases.extend(barrett_parse_listing(html, url))
        return cases
    if cfg.kind == "sanantonio":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        cases = []
        for case_id in sanantonio_list_cases(listing):
            url = f"{cfg.base_url}{cfg.gallery_paths[0]}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(sanantonio_parse_case(html, case_id, url))
        return cases
    if cfg.kind == "harrington":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        cases = []
        for case_id in harrington_list_cases(listing):
            url = f"{cfg.base_url}{cfg.gallery_paths[0]}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(harrington_parse_case(html, case_id, url))
        return cases
    if cfg.kind == "influx_swiper":
        gallery_path = cfg.gallery_paths[0]
        listing = fetcher.get(cfg.base_url + gallery_path,
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        cases = []
        for case_id in influx_swiper_list_cases(listing, gallery_path):
            url = f"{cfg.base_url}{gallery_path}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(influx_swiper_parse_case(html, case_id, url, gallery_path))
        return cases
    if cfg.kind == "austinweston":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        cases = []
        ids = sorted(set(re.findall(re.escape(cfg.gallery_paths[0]) + r"(\d+)/", listing)),
                    key=int)
        for case_id in ids:
            url = f"{cfg.base_url}{cfg.gallery_paths[0]}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(austinweston_parse_case(html, case_id, url))
        return cases
    if cfg.kind == "charlotte":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        cases = []
        ids = sorted(set(re.findall(re.escape(cfg.gallery_paths[0]) + r"(\d+)/", listing)),
                    key=int)
        for case_id in ids:
            url = f"{cfg.base_url}{cfg.gallery_paths[0]}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(charlotte_parse_case(html, case_id, url))
        return cases
    if cfg.kind == "mwps":
        gallery_path = cfg.gallery_paths[0]
        listing = fetcher.get(cfg.base_url + gallery_path,
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        cases = []
        for case_id in mwps_list_cases(listing, gallery_path):
            url = f"{cfg.base_url}{gallery_path}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(mwps_parse_case(html, case_id, url))
        return cases
    if cfg.kind == "allure":
        cases = []
        case_id, seen = "01", set()
        while case_id and case_id not in seen:
            seen.add(case_id)
            url = f"{cfg.base_url}{cfg.gallery_paths[0]}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(allure_parse_case(html, case_id, url))
            case_id = allure_next_case_id(html)
        return cases
    if cfg.kind == "drtavakoli":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return drtavakoli_parse_listing(listing, cfg.base_url + cfg.gallery_paths[0])
    if cfg.kind == "sixsurgery":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return sixsurgery_parse_listing(listing, cfg.base_url + cfg.gallery_paths[0])
    if cfg.kind == "drmiroshnik":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return drmiroshnik_parse_listing(listing, cfg.base_url + cfg.gallery_paths[0])
    if cfg.kind == "drrohrich":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return drrohrich_parse_listing(listing, cfg.base_url + cfg.gallery_paths[0])
    if cfg.kind == "drjeremyhunt":
        cases, case_ids, page = [], set(), 1
        while True:
            path = (cfg.gallery_paths[0] if page == 1
                    else f"{cfg.gallery_paths[0].rstrip('/')}/page/{page}/")
            url = cfg.base_url + path
            html = _fetch_optional(fetcher, url, f"{cfg.slug}_listing_p{page}.html")
            if html is None:
                break
            new = [c for c in drjeremyhunt_parse_listing(html, url)
                  if c.case_id not in case_ids]
            if not new:
                break
            case_ids.update(c.case_id for c in new)
            cases.extend(new)
            page += 1
            if page > 30:
                break
        return cases
    if cfg.kind == "wny":
        listing_url = cfg.base_url + cfg.gallery_paths[0]
        listing = fetcher.get(listing_url, f"{cfg.slug}_listing.html").decode(
            "utf-8", "replace")
        to_visit = list(wny_seed_cases(listing))
        visited: set[str] = set()
        cases = []
        while to_visit:
            case_id = to_visit.pop(0)
            if case_id in visited:
                continue
            visited.add(case_id)
            url = f"{cfg.base_url}{cfg.gallery_paths[0]}{case_id}/"
            html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
                "utf-8", "replace")
            cases.append(wny_parse_case(html, case_id, url))
            for next_id in wny_next_case_ids(html):
                if next_id not in visited:
                    to_visit.append(next_id)
        return sorted(cases, key=lambda c: int(c.case_id))
    if cfg.kind == "etna":
        gallery_path = cfg.gallery_paths[0]
        listing_url = cfg.base_url + gallery_path
        # The endpoint route reads the listing under its OWN cache key.
        #
        # The listing carries the declared total, and the declared total is the
        # denominator this route reconciles against - so a listing cached by an
        # earlier run silently reconciles against a stale number. tccs declared
        # 579 cases when it was cached on 2026-08-15 and declares 582 now;
        # sweeping to a stale 579 would stop three cases short and report
        # success. A separate key gets a current listing without touching the
        # shared entry the other clinics and every offline re-parse resolve
        # against - that cache is shared state, and a collection run has no
        # business invalidating it.
        listing_key = (f"{cfg.slug}_listing_endpoint.html" if gallery_endpoint
                       else f"{cfg.slug}_listing.html")
        # ...but that own key is written to the same persistent cache, so the
        # freshness only holds the FIRST time this route runs for a clinic. On a
        # later run the listing replays, and the total it carries is as old as the
        # cache. That does not stop the sweep, and it must not be silently
        # presented as a live check either: the reconciliation says which of the
        # two it is, so "named all N declared case(s)" cannot be read as a
        # verified-complete sweep when it is a sweep verified against a snapshot.
        declared_replayed = fetcher.is_cached(listing_key)
        listing = fetcher.get(listing_url, listing_key).decode("utf-8", "replace")
        declared = etna_declared_total(listing)
        if gallery_endpoint and declared_replayed:
            print(f"  NOTE {cfg.slug}: the declared total was read from a "
                  f"REPLAYED cache entry ({listing_key}), not a fresh fetch, so "
                  f"it is as old as that entry and cases published since are not "
                  f"counted in it")
        if gallery_endpoint and not declared:
            # The declared total is the denominator this route reconciles against,
            # and reconciliation is the whole reason the endpoint route exists.
            # Sweeping to a total of 0 enumerates NOTHING, falls back to the 12
            # cases the listing renders, and reports that as a clean run - a 98%
            # under-collection with a zero exit status. That is the same silent
            # truncation that turned 582 declared cases into a confident 450, so
            # the route refuses instead of degrading.
            raise RuntimeError(
                f"{cfg.slug}: the gallery listing declares no case total, so an "
                f"endpoint sweep cannot be reconciled against one. Refusing to "
                f"enumerate rather than report an unverifiable count as complete "
                f"(check the EII_GALLERY_JS state block in {listing_key}).")
        root = etna_gallery_root(gallery_path)
        to_visit = [f"{gallery_path}{cid}/"
                    for cid in etna_list_seed_cases(listing, gallery_path)]
        if gallery_endpoint:
            # The gallery's own case-list endpoint names the whole category in one
            # pass, so the chain walk is not run alongside it. The walk exists only
            # because the endpoint was closed: it reached 7 of 12 clinics' declared
            # totals by traversing the practice's ENTIRE gallery, hundreds of
            # off-category pages at a time, to find the disconnected components the
            # target category's cases sit in. Running both would spend all of that
            # to re-derive a list the endpoint already gave, and the declared-total
            # reconciliation below is what proves the endpoint did not come short.
            for path in etna_endpoint_case_paths(cfg, fetcher, listing, declared,
                                                 grant_root=grant_root):
                if path not in to_visit:
                    to_visit.append(path)
        else:
            # Seed from every other category listing too - the chain is a set of
            # disconnected components (see etna_category_paths), and the target
            # category's own 12 rendered cases sit in only one of them.
            index_html = _fetch_seed(fetcher, cfg.base_url + root,
                                     f"{cfg.slug}_gallery_index.html")
            for cat_path in etna_category_paths(index_html or "", root):
                if cat_path == gallery_path:
                    continue
                cat_html = _fetch_seed(
                    fetcher, cfg.base_url + cat_path,
                    f"{cfg.slug}_cat_" + re.sub(r"[^\w]+", "_", cat_path.strip("/"))
                    + ".html")
                if cat_html is None:
                    continue
                for m in re.finditer(
                        re.escape(root) + r"[a-z0-9\-]+/[a-z0-9\-]+/(\d+)/", cat_html):
                    seed = m.group(0)
                    if seed not in to_visit:
                        to_visit.append(seed)
        visited: set[str] = set()
        cases = []
        uncached = 0
        # Reconciliation counts distinct CASE IDS, never paths. A case the
        # practice files under two categories is named twice by the enumeration -
        # tccs 12145 under /breast-augmentation/ and /motiva-implants/, camp 507
        # also under /body/mommy-makeover/ - and `visited` dedupes on path, so a
        # path-based count reports one more case than the gallery declares. That
        # is a spurious WARN in one direction and, worse, in the other: one
        # double-count cancelling one genuinely missed case reads as a complete
        # sweep, which is the silent truncation this whole route exists to catch.
        seen_ids: set[str] = set()
        in_category_ids: set[str] = set()
        while to_visit and len(visited) < ETNA_MAX_PAGES:
            path = to_visit.pop(0)
            if path in visited:
                continue
            visited.add(path)
            case_id = path.rstrip("/").rsplit("/", 1)[-1]
            url = cfg.base_url + path
            in_scope = path.startswith(gallery_path)
            seen_ids.add(case_id)
            if in_scope:
                in_category_ids.add(case_id)
            else:
                if gallery_endpoint:
                    # The practice filed this case under another procedure. Under
                    # the chain route an off-category page still had to be fetched,
                    # because it was the only way through to the next component of
                    # the chain; under the endpoint route nothing is downstream of
                    # it, and its own URL is the practice saying the case is not a
                    # pure augmentation. Fetching it would spend a request to
                    # rediscover that from its image filenames.
                    continue
            # Off-category pages fetched to follow the chain keep the category in
            # their cache key so ids cannot collide.
            key = (f"{cfg.slug}_case_{case_id}.html" if in_scope else
                   f"{cfg.slug}_chain_"
                   + re.sub(r"[^\w]+", "_", path.strip("/")) + ".html")
            # An uncached page in offline mode ends this branch of the walk
            # rather than the whole run: offline re-parsing is how the corpus
            # proves a shared parser's blast radius, and it must still work
            # against a cache taken before the walk crossed categories.
            html_bytes = _fetch_seed(fetcher, url, key)
            if html_bytes is None:
                uncached += 1
                continue
            html = html_bytes
            if in_scope:
                cases.append(etna_parse_case(html, case_id, url, gallery_path))
            if gallery_endpoint:
                continue
            for next_path in etna_next_case_paths(html, root):
                if next_path not in visited:
                    to_visit.append(next_path)
        # The gallery publishes its own case count, so enumeration can be
        # checked rather than assumed. A short walk means the chain does not
        # reach every case and the shortfall is real data we never saw.
        if uncached:
            print(f"  {cfg.slug}: {uncached} chain page(s) not in cache "
                  f"(offline walk); enumeration is cache-bounded")
        if gallery_endpoint:
            # The endpoint enumerates the CATEGORY, and a category can name cases
            # whose canonical URL sits elsewhere: tccs's breast-augmentation
            # category returns /gallery/mommy-makeover/mommy-makeover/240/. So the
            # declared total counts cases TAGGED with the procedure, and the
            # denominator for "did enumeration succeed" is in-category plus
            # elsewhere - not the in-category count on its own.
            reached = len(seen_ids)
            elsewhere = reached - len(in_category_ids)
            against = ("a REPLAYED cached total, not the gallery's current one"
                       if declared_replayed else
                       "the total the gallery declares now")
            if declared and reached != declared:
                print(f"  WARN {cfg.slug}: case-list endpoint named {reached} "
                      f"distinct case(s) ({len(in_category_ids)} in this category, "
                      f"{elsewhere} the practice files only under another) but the "
                      f"gallery declares {declared} - reconciled against {against}")
            else:
                print(f"  {cfg.slug}: case-list endpoint named all {declared} declared "
                      f"case(s) - {len(in_category_ids)} in this category, "
                      f"{elsewhere} the practice files only under another; "
                      f"reconciled against {against}")
        elif declared is not None and len(cases) != declared:
            print(f"  WARN {cfg.slug}: chain walk reached {len(cases)} case(s) "
                  f"but the gallery declares {declared}")
        else:
            print(f"  {cfg.slug}: chain walk reached all {len(cases)} declared case(s)")
        return sorted(cases, key=lambda c: int(c.case_id))
    if cfg.kind == "privateclinic":
        cases, page = [], 1
        while True:
            suffix = "" if page == 1 else f"?pg={page}"
            url = f"{cfg.base_url}{cfg.gallery_paths[0]}{suffix}"
            html = fetcher.get(url, f"{cfg.slug}_listing_p{page}.html").decode(
                "utf-8", "replace")
            soup = BeautifulSoup(html, "html.parser")
            card_els = soup.select("div.wp-block-group.is-style-thumbnail-format-2")
            if not card_els:
                break
            for card in card_els:
                case = privateclinic_parse_card(card, url)
                if case is not None:
                    cases.append(case)
            page += 1
            if page > 20:
                break
        return cases
    if cfg.kind == "mitchellbrown":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return mitchellbrown_parse_listing(listing, cfg.base_url + cfg.gallery_paths[0])
    if cfg.kind == "skplastic":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return skplastic_parse_listing(listing, cfg.base_url + cfg.gallery_paths[0])
    if cfg.kind == "heavenly":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return heavenly_parse_listing(listing, cfg.base_url + cfg.gallery_paths[0])
    if cfg.kind == "aips":
        url = cfg.base_url + cfg.gallery_paths[0]
        listing = fetcher.get(url, f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        cases = aips_parse_listing(listing, url)
        # The gallery publishes no case count of its own, so enumeration can
        # only be checked against the shape the page actually has.
        print(f"  {cfg.slug}: one inline listing page, no declared total; "
              f"{len(cases)} case(s), "
              f"{sum(len(c.pairs) for c in cases)} pair(s)")
        return sorted(cases, key=lambda c: int(c.case_id))
    if cfg.kind == "choice":
        url = cfg.base_url + cfg.gallery_paths[0]
        listing = fetcher.get(url, f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return choice_parse_listing(listing, url)
    if cfg.kind == "mya":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return mya_parse_listing(listing, cfg.base_url + cfg.gallery_paths[0])
    if cfg.kind == "blaine":
        url = cfg.base_url + cfg.gallery_paths[0]
        listing = fetcher.get(url, f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return blaine_parse_listing(listing, url)
    if cfg.kind == "drgrover":
        url = cfg.base_url + cfg.gallery_paths[0]
        listing = fetcher.get(url, f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return drgrover_parse_listing(listing, url)
    if cfg.kind == "basu":
        cases, page = [], 1
        while True:
            path = (cfg.gallery_paths[0] if page == 1
                    else f"{cfg.gallery_paths[0].rstrip('/')}/{page}/")
            url = cfg.base_url + path
            html = _fetch_optional(fetcher, url, f"{cfg.slug}_listing_p{page}.html")
            if html is None:
                break
            page_cases = basu_parse_listing_page(html, url)
            if not page_cases:
                break
            cases.extend(page_cases)
            page += 1
            if page > 20:
                break
        return cases
    if cfg.kind == "page1":
        # The platform publishes no case total, and page N past the end is a
        # plain 404, so the walk runs until a page yields no cases and the
        # count reconciles against that boundary rather than a declared figure.
        gallery_path = cfg.gallery_paths[0]
        listed: list[tuple[str, str]] = []
        seen: set[str] = set()
        page = 1
        while True:
            url = (cfg.base_url + gallery_path if page == 1
                   else f"{cfg.base_url}{gallery_path}page/{page}/")
            key = (f"{cfg.slug}_listing.html" if page == 1
                   else f"{cfg.slug}_listing_p{page}.html")
            # _fetch_seed, not _fetch_optional: page N+1 past the end is a
            # 404 that was never cached, so an OFFLINE re-parse (the
            # blast-radius check every shared parser needs) has to treat a
            # missing cache entry as the same end-of-pages boundary.
            html = _fetch_seed(fetcher, url, key)
            if html is None:
                break
            found = p1.page1_list_cases(html)
            if not found:
                break
            for case_number, case_url in found:
                if case_number not in seen:
                    seen.add(case_number)
                    listed.append((case_number, case_url))
            page += 1
        print(f"  listing walk: {page - 1} page(s), {len(listed)} case(s) "
              f"(platform publishes no declared total)")
        cases = []
        for case_number, case_url in listed:
            html = fetcher.get(case_url, f"{cfg.slug}_case_{case_number}.html"
                               ).decode("utf-8", "replace")
            cases.append(page1_parse_case(html, case_number, case_url))
        return cases
    if cfg.kind == "drteitelbaum":
        cases, page = [], 1
        while True:
            path = (cfg.gallery_paths[0] if page == 1
                    else f"{cfg.gallery_paths[0].rstrip('/')}/{page}/")
            url = cfg.base_url + path
            html = _fetch_optional(fetcher, url, f"{cfg.slug}_listing_p{page}.html")
            if html is None:
                break
            page_cases = drteitelbaum_parse_listing_page(html, url)
            if not page_cases:
                break
            cases.extend(page_cases)
            page += 1
            if page > 40:
                break
        return cases
    if cfg.kind == "sculpted":
        # WordPress /page/N pagination. The page AFTER the last one 200s with a
        # fully rendered shell and an empty gallery list rather than 404ing, so
        # the walk stops on "no case blocks" and _fetch_optional's 404 handling
        # is only a backstop. The gallery publishes no case total anywhere -
        # the pager's highest numbered link is the only count it declares - so
        # the run walks one page past the last non-empty one to prove the end.
        cases, page = [], 1
        while True:
            path = (cfg.gallery_paths[0] if page == 1
                    else f"{cfg.gallery_paths[0].rstrip('/')}/page/{page}")
            url = cfg.base_url + path
            html = _fetch_optional(fetcher, url, f"{cfg.slug}_listing_p{page}.html")
            if html is None:
                break
            page_cases = sculpted_parse_listing_page(html, url)
            if not page_cases:
                break
            cases.extend(page_cases)
            page += 1
            if page > 20:
                break
        return cases
    if cfg.kind == "arps":
        # Imported here rather than at module scope: arps_gallery imports this
        # module for the shared data model, and by the time collect_cases runs
        # this module is fully loaded.
        import arps_gallery

        return arps_gallery.arps_collect_cases(cfg, fetcher)
    raise ValueError(f"unknown clinic kind {cfg.kind!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clinic", required=True, choices=sorted(CLINICS))
    parser.add_argument("--out", type=Path, required=True,
                        help="Raw intake root (writes <out>/<clinic>/<pair_id>/...)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Minimum seconds between requests (default 2)")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="Fetch cache (default <out>/.scraper-cache)")
    parser.add_argument("--annotations", type=Path, default=None,
                        help="JSON: per-case view/clothing labels from visual "
                             "inspection (keys '<clinic>:<case>')")
    parser.add_argument("--cases", default=None,
                        help="Comma-separated case ids to restrict to")
    parser.add_argument("--parse-only", action="store_true",
                        help="Fetch case pages, report spec coverage, no images")
    parser.add_argument("--prefetch", action="store_true",
                        help="Download all pair images into the cache without "
                             "emitting anything (for visual annotation)")
    parser.add_argument("--offline", action="store_true",
                        help="Serve only from cache; never touch the network")
    parser.add_argument("--gallery-endpoint", action="store_true",
                        help="Enumerate an Etna gallery through its own case-list "
                             "endpoint instead of the prev/next chain. Requires an "
                             "endpoint_grant on the clinic's config: the site "
                             "owner's grant is what makes that request permitted, "
                             "and it covers the case list only.")
    parser.add_argument("--grant-root", type=Path, default=None,
                        help="Directory a clinic's endpoint_grant path resolves "
                             "against (default: the current directory). The grant "
                             "document must be present there before the gallery "
                             "case-list endpoint is requested.")
    args = parser.parse_args()

    cfg = CLINICS[args.clinic]
    cache_dir = args.cache_dir or (args.out / ".scraper-cache")
    fetcher = PoliteFetcher(cache_dir, delay=args.delay, offline=args.offline)
    annotations_all = json.loads(args.annotations.read_text()) if args.annotations else {}

    # Prefetching images is a second pass over an enumeration that already
    # happened, so it walks the cache rather than the site: re-crawling here
    # would spend the whole run re-walking a chain whose pages are already on
    # disk before it downloaded a single image. Images themselves still come
    # from the network through `fetcher` below.
    enumerator = fetcher
    if args.prefetch and not args.offline:
        enumerator = PoliteFetcher(cache_dir, delay=args.delay, offline=True)
    if args.gallery_endpoint:
        # Verified BEFORE anything is fetched, not on the way into the sweep.
        try:
            endpoint_grant_document(cfg, args.grant_root)
        except PermissionError as exc:
            parser.error(f"{exc}; --gallery-endpoint must not be used for this "
                         f"clinic")
    cases = collect_cases(cfg, enumerator, gallery_endpoint=args.gallery_endpoint,
                          grant_root=args.grant_root)
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c.case_id in wanted]
    print(f"Collected {len(cases)} case(s) for clinic {cfg.slug}")

    out_clinic = args.out / cfg.slug
    emitted = skipped = 0
    stats = {"cc": 0, "shape": 0, "brand": 0, "profile": 0, "specs": 0, "pairs": 0}
    for i, case in enumerate(cases, 1):
        specs = case.specs
        if specs.summary or specs.fields:
            stats["specs"] += 1
        if volume_cc(specs) is not None:
            stats["cc"] += 1
        if specs.shape:
            stats["shape"] += 1
        if specs.brand != "unknown":
            stats["brand"] += 1
        if specs.profile:
            stats["profile"] += 1
        stats["pairs"] += len(case.pairs)
        for w in case.warnings:
            print(f"  WARN case {case.case_id}: {w}")
        print(f"[{i}/{len(cases)}] case {case.case_id}: pairs={len(case.pairs)} "
              f"cc={volume_cc(specs)} shape={specs.shape} brand={specs.brand} "
              f"profile={specs.profile}")
        if args.parse_only:
            continue
        if args.prefetch:
            for pair in case.pairs:
                for url in dict.fromkeys((pair.before_url, pair.after_url)):
                    full_url = url if url.startswith("http") else cfg.base_url + url
                    fetcher.get(full_url, image_cache_key(cfg.slug, full_url))
            continue

        annotations = annotations_all.get(f"{cfg.slug}:{case.case_id}", {})
        emitted_ids = set()
        for pair in case.pairs:
            view, view_source = resolve_view(pair, annotations)
            if view is None:
                print(f"    SKIP {pair.key}: no view annotation")
                skipped += 1
                continue
            pair_id = f"{cfg.slug}-{case.case_id}-{view}"
            if pair_id in emitted_ids:
                print(f"    SKIP {pair.key}: view {view} already emitted for "
                      f"this case (duplicate view label)")
                skipped += 1
                continue
            emitted_ids.add(pair_id)
            pair_dir = out_clinic / pair_id
            pair_dir.mkdir(parents=True, exist_ok=True)
            try:
                if pair.grid_shape is not None:
                    full_url = (pair.before_url
                                if pair.before_url.startswith("http")
                                else cfg.base_url + pair.before_url)
                    data = fetcher.get(full_url,
                                       image_cache_key(cfg.slug, full_url))
                    rows, cols = pair.grid_shape
                    before_data = crop_grid_cell(data, rows, cols, pair.before_cell)
                    after_data = crop_grid_cell(data, rows, cols, pair.after_cell)
                    before_data, after_data, _ = postprocess_pair(
                        cfg, before_data, after_data)
                    (pair_dir / "before.jpg").write_bytes(
                        crop_bottom(crop_bottom_frac(
                            before_data, cfg.bottom_crop_frac),
                            cfg.bottom_crop_px))
                    (pair_dir / "after.jpg").write_bytes(
                        crop_bottom(crop_bottom_frac(
                            after_data, cfg.bottom_crop_frac),
                            cfg.bottom_crop_px))
                elif pair.split_composite:
                    full_url = (pair.before_url
                                if pair.before_url.startswith("http")
                                else cfg.base_url + pair.before_url)
                    data = fetcher.get(full_url,
                                       image_cache_key(cfg.slug, full_url))
                    try:
                        before_data, after_data = split_composite_image(
                            data, border=pair.composite_border,
                            gutter=pair.composite_gutter,
                            bottom_frac=pair.composite_bottom_frac)
                    except ValueError as exc:
                        print(f"    SKIP {pair.key}: {exc}")
                        skipped += 1
                        continue
                    before_data, after_data, _ = postprocess_pair(
                        cfg, before_data, after_data)
                    (pair_dir / "before.jpg").write_bytes(
                        crop_bottom(crop_bottom_frac(
                            before_data, cfg.bottom_crop_frac),
                            cfg.bottom_crop_px))
                    (pair_dir / "after.jpg").write_bytes(
                        crop_bottom(crop_bottom_frac(
                            after_data, cfg.bottom_crop_frac),
                            cfg.bottom_crop_px))
                else:
                    halves = {}
                    for stem, url in (("before", pair.before_url),
                                      ("after", pair.after_url)):
                        full_url = (url if url.startswith("http")
                                    else cfg.base_url + url)
                        data = fetcher.get(full_url,
                                           image_cache_key(cfg.slug, full_url))
                        halves[stem] = (full_url, data)
                    before_data, after_data, cropped = postprocess_pair(
                        cfg, halves["before"][1], halves["after"][1])
                    for stem, data in (("before", before_data),
                                       ("after", after_data)):
                        # A postprocessed half is re-encoded as JPEG, so it may
                        # keep the source extension only when neither
                        # postprocess_pair nor crop_bottom touched it.
                        name = (f"{stem}.jpg"
                                if cropped or cfg.bottom_crop_frac
                                else emitted_image_name(stem, halves[stem][0],
                                                        cfg.bottom_crop_px))
                        (pair_dir / name).write_bytes(
                            crop_bottom(crop_bottom_frac(
                                data, cfg.bottom_crop_frac),
                                cfg.bottom_crop_px))
            except requests.exceptions.HTTPError as exc:
                # One image missing from the CDN must not end the clinic. tccs
                # publishes case 11336 with a front photograph that 404s, and an
                # unguarded raise there abandoned the remaining 300+ cases mid-run
                # - a failure that looks exactly like a finished collection.
                #
                # Only a MISSING image earns that tolerance. A 403 from a WAF or a
                # 5xx the fetcher already retried out is the site refusing the run,
                # and swallowing it per pair turns a site-wide refusal into a tally
                # of skips and a zero exit status - the very failure this guard was
                # written to prevent, just reached from the other side.
                status = (exc.response.status_code
                          if exc.response is not None else None)
                if status not in MISSING_STATUS:
                    raise
                print(f"    SKIP {pair.key}: image unavailable ({exc})")
                skipped += 1
                continue
            pair_ann = annotations.get("pairs", {}).get(pair.key, {})
            meta = build_meta(pair_id, view, specs, annotations, pair_ann,
                              view_source, cfg.consent_ref)
            (pair_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
            emitted += 1
            print(f"    OK {pair_id}")

    print(f"\nSpec coverage across {len(cases)} case(s): specs={stats['specs']} "
          f"cc={stats['cc']} shape={stats['shape']} brand={stats['brand']} "
          f"profile={stats['profile']} pairs={stats['pairs']}")
    if not args.parse_only:
        print(f"Emitted {emitted} pair(s), skipped {skipped} pair(s); "
              f"{fetcher.requests_made} network request(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
