#!/usr/bin/env python3
"""arps (AR Plastic Surgery, Auchenflower/Brisbane QLD) gallery parser.

Registered in `scrape_gallery.CLINICS` as kind='arps'; `collect_cases` calls
`arps_collect_cases` below. Everything specific to this gallery lives here so
the shared module keeps one config entry and one dispatch branch.

Markup contract (bespoke WordPress theme, one listing page per 4 cases):

    div.allslider                       <- ONE case
      div.intro-title-box
        h2.text-title                   <- '400cc Smooth Round Breast Implants'
        div.icon-box b                  <- 'Breast Augmentation Mammoplasty'
      div.patient-gallery#patient-gallery-<postid>-<n>
        div.gallery_slide_item          <- ONE before/after pair
          picture.patientgallery-img.before img[src]
          picture.patientgallery-img.after  img[src]
      div.casestudies                   <- 'Case Details: Dr Eddie Cheng
                                          (Surgeon) - 6 months following ...'

`<postid>` is the WordPress post id and is the stable case key; the trailing
`-<n>` is the case's running position across the whole paginated gallery and
changes whenever a case is added, so it is deliberately not part of the id.

Three things about this gallery are not guessable and each cost a measurement:

1. **The gallery is shared by four surgeons and the consent instrument is one
   surgeon's.** `CONSENT-2026-08-25-PROSPECTED-CLINICS.md` grants use of images
   "where the signatory was the performing surgeon", and the signatory is Dr
   Eddie Cheng. The site publishes its own attribution through the listing's
   `?surgeon=` filter, so enumeration goes through `ARPS_SURGEON` rather than
   through the unfiltered listing plus a grep of the narrative. That matters
   for more than tidiness: case 2971 publishes no surgeon name in its Case
   Details at all, and a narrative grep would have dropped a consented case
   while a future non-Cheng case would sail through. Measured 2026-08-25: the
   filter is live (the other three surgeons return 0 breast-augmentation
   cases) and Dr Cheng's filter returns all 53 cases the unfiltered listing
   shows.

2. **Most of the gallery is published below the pipeline's 400px floor.** 21 of
   53 cases publish at 667-1707px on the short edge; the other 32 publish a
   320x360 (or 267x300) JPEG and nothing larger - the `-320x360-1.jpg` suffix
   is part of the *uploaded* filename, not a WordPress-generated size, and
   every larger candidate 404s. Resolution is a property of the case, not of
   the image: within a case every pair shares one size.

3. **Every image carries a burned-in "(c) Dr Eddie Cheng" mark in the bottom
   band**, on both halves of every pair - so it is not a label leak - but a
   corner or edge watermark is cropped rather than tolerated (captain,
   2026-08-19). The mark is drawn at a size proportional to the frame's WIDTH,
   so the crop is a fraction of width rather than a pixel count - across the
   six export sizes its top edge is 3.8%-7.2% of width above the bottom but
   anywhere from 24px to 115px. See `ClinicConfig.bottom_crop_frac` and the
   measurement in `data/ba-viz-collect-arps/report.md`.

Views are not documented anywhere on the page (the alt text is one constant
string for the whole gallery), so every view label comes from the visual
annotation pass, as it does for drdanielbarrett and sanantonio.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import unquote

from bs4 import BeautifulSoup

import scrape_gallery as sg

ARPS_GALLERY_PATH = "/breast-augmentation-gallery/"
# The consent instrument's signatory. Enumeration is scoped to this surgeon's
# cases; see the module docstring.
ARPS_SURGEON = "dr-eddie-cheng"
# The listing renders 4 cases per page and 404s nothing - an over-run page
# returns HTTP 200 with an empty gallery - so the walk stops on an empty page.
# 60 pages is ~4x the gallery's present length and exists only as a backstop.
ARPS_MAX_PAGES = 60

ARPS_CASE_ID_RE = re.compile(r"^patient-gallery-(\d+)-\d+$")

# Procedures that make a case something other than pure breast augmentation.
# Screened against the case TEXT (title + procedure label + Case Details), never
# a filename: at this gallery the filename is the least reliable field on the
# page - case 2598's 'after' is published as
# '8-12-455CC-UHP-BAM-POST-...jpg' while its own Case Details reads '400cc
# smooth round moderate plus profile'.
#
# The last two entries are adjunct excisions rather than a second cosmetic
# operation, and they are the reason this list is spelled out rather than
# reduced to 'lift|mommy makeover': the captain's ruling is that every combined
# procedure is excluded, and an accessory-nipple or accessory-breast-tissue
# excision performed in the same operation changes the torso in the frame.
ARPS_COMBINED_PATTERNS = [
    (re.compile(r"\bmastopexy\b", re.I), "mastopexy"),
    (re.compile(r"\b(?:breast\s+)?lift\b", re.I), "breast lift"),
    (re.compile(r"\bmommy\s+makeover\b", re.I), "mommy makeover"),
    (re.compile(r"\breduction\b", re.I), "breast reduction"),
    (re.compile(r"\b(?:explant|revision)\w*\b", re.I), "explant/revision"),
    (re.compile(r"\babdominoplasty\b|\btummy\s+tuck\b", re.I), "abdominoplasty"),
    (re.compile(r"\blipo(?:suction|sculpture)\b", re.I), "liposuction"),
    (re.compile(r"\bfat\s+(?:transfer|graft\w*)\b", re.I), "fat transfer"),
    (re.compile(r"\baccessory\s+breast\s+tissue\b", re.I),
     "accessory breast tissue excision"),
    (re.compile(r"\baccessory\s+nipple\b", re.I), "accessory nipple excision"),
]

# Profile abbreviations this gallery uses that the shared PROFILE_PATTERNS table
# does not decode. Applied ONLY after the shared classifier has found nothing,
# and deliberately kept local: adding them to the shared table would change how
# every other clinic's text is read, and that blast radius has not been proven
# here (AGENTS.md, "Before changing a parser that more than one clinic shares").
#
# - 'UHP' is Ultra High Profile, which maps to the schema's extra-high by the
#   captain's 2026-08-19 profile ruling. Case 2606 publishes 'UHP 455 CC' in
#   both its title and its Case Details and is the only extra-high case here.
# - 'M+' is Mentor's own shorthand for Moderate Plus, and the one case using it
#   (5190) names Mentor in the same sentence: '275CC M+ Mentor Smooth Round'.
#   The '+' IS the 'Plus', so this is a decode of the manufacturer's label
#   rather than an inference about the implant.
#
# Mentor's 'Xtra' is deliberately absent: it is a product line, not a profile
# (captain, 2026-08-19), and it does not appear in this gallery anyway.
# Cases withheld on VISUAL inspection of the published photographs, 2026-08-25.
#
# The gallery's own disclaimer says "Some images may have the patient's tattoos,
# jewellery or other identifiable items blurred", and case 2603 is where it
# actually happens: grey mosaic blocks over the shoulder, upper chest and arm in
# all six of its published images. Training on those teaches the mosaic.
#
# This is an enumeration and not a rule, for the same reason
# `training/retired_pairs.json` is one: a rule would be a live query, and no
# query available here finds this case. `censorship.py` ACCEPTED all six images
# and, on the same run, rejected six clean pairs at five other cases as
# "texture-free patch of skin" - the known false-positive family in AGENTS.md
# (smooth, evenly-lit, lightly-retouched skin, and one backdrop region merged
# into the silhouette). So the detector is exactly inverted on this clinic, and
# retuning it is a corpus-wide change that must be re-measured against every
# clinic first - not something to do from inside one gallery's parser.
#
# If the practice republishes these photographs unmarked, delete the entry.
ARPS_CENSORED_CASES = {
    "2603": "grey mosaic blocks over the shoulder, upper chest and arm in all "
            "six published images (the gallery's 'identifiable items blurred' "
            "disclaimer); confirmed by eye, and censorship.py does not catch it",
}

ARPS_PROFILE_PATTERNS = [
    (re.compile(r"\bUHP\b"), "extra-high"),
    (re.compile(r"\bM\+"), "moderate-plus"),
]

# 'Label: value' pairs as this gallery's chart-style Case Details spell them:
# 'Implant size: 325cc, Implant Type: Round High profile, Placement:
# Sub-pectoral, dual plane, Incision: Inframammary folds.' Spacing around the
# colon is inconsistent ('Incision : Inframammary fold', 'Placement: :
# Sub-pectoral'), and fields are separated by commas that also appear INSIDE a
# value ('Sub-pectoral, dual plane'), so the split is on the next known label
# rather than on the comma.
ARPS_FIELD_LABELS = (
    "Pre-op bra size", "Pre-op Bra Size", "Post op bra size", "Post-Op bra size",
    "Implant size", "Implant Size", "Size implant", "Breast implant size",
    "Implant Type", "Implant type", "Type implant", "Implant position",
    "Incision Line", "Incision type", "Incision", "Placement", "Age",
    "Height", "Ht", "Weight", "Wt", "Children", "Patient Goal",
)
ARPS_LABEL_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(l) for l in ARPS_FIELD_LABELS),
                             key=len, reverse=True)) + r")\s*:+\s*",
    re.I)

# 'Height: 160 cm', 'Ht: 165 cm', and the one case that publishes a bare
# 'Height: 160' beside a 'Weight: 55 kg'. A bare number is NOT converted - the
# clinic documented no unit for it, and reading it as centimetres would invent
# the unit (AGENTS.md: a published range or an undocumented unit is not a
# measurement).
ARPS_HEIGHT_CM_RE = re.compile(r"^(\d{2,3}(?:\.\d+)?)\s*cm\b", re.I)
# Per-side volumes as this gallery writes them, in both orders:
#   'Left 450cc / Right 425cc', 'Implant Size: Left 350 cc/ Right: 400 cc',
#   'L 350cc R 400cc'                                   -> side then volume
#   'implant size: 400cc right 375cc left'              -> volume then side
# The separator class deliberately excludes '-', so 'Pre-op bra size: Right-A,
# Left-B' (a cup size, not a volume) cannot be read as one.
#
# The shared parse_fill_volumes() could not read the first shape when this
# parser was written - its prefix pass ran to the next comma or full stop, so
# 'Left 450cc / Right 425cc' returned the left volume and never saw the right
# one. That half was fixed centrally on 2026-08-25 (the segment now stops at
# the next side marker); the SECOND shape is still live, and
# 'implant size: 400cc right 375cc left' still comes back with the sides wrong.
# Widening the shared helper to cover it would change how 22 other clinics'
# text is read, and that blast radius is not proven here, so this gallery reads
# its own sided volumes and falls back to the shared helper for everything else
# (AGENTS.md, "Before changing a parser that more than one clinic shares").
ARPS_SIDE_FIRST_RE = re.compile(
    r"\b(left|right|l|r)\b[\s:,/]*(\d{2,4})\s*(?:cc|ml)\b", re.I)
ARPS_VOLUME_FIRST_RE = re.compile(
    r"\b(\d{2,4})\s*(?:cc|ml)\s*[\s:,/]*\b(left|right|l|r)\b", re.I)
ARPS_WEIGHT_KG_RE = re.compile(r"^(\d{2,3}(?:\.\d+)?)\s*kgs?\b", re.I)
# '6 months following', '8 weeks after', '11 months post op', '3 months after'.
ARPS_MONTHS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*months?\b", re.I)
ARPS_WEEKS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*weeks?\b", re.I)
ARPS_AGE_RE = re.compile(r"\b(\d{2})\s*(?:yo\b|year[- ]old\b|years?\b)", re.I)
# Operations other than breast augmentation, as they appear in this gallery's
# image FILENAMES. Hyphen-delimited because the filenames are hyphen-joined
# slugs ("Dr-Eddie-Cheng-AR-Plastic-Surgery-Abdominoplasty-7-320x360-1.jpg").
ARPS_FILENAME_PROCEDURE_RE = re.compile(
    r"(?<=-)(?:Abdominoplasty|Tummy-Tuck|Mastopexy|Breast-Lift|Breast-Reduction"
    r"|Liposuction|Mommy-Makeover|Explant|Revision)(?=-)", re.I)


def arps_sided_volumes(text: str) -> tuple[float | None, float | None]:
    """(left_cc, right_cc) where this gallery names a side beside a volume.

    Returns (None, None) unless BOTH sides are named exactly once and
    consistently; an ambiguous string is left for the caller's fallback rather
    than resolved by preferring one reading over another.
    """
    for pattern, side_group, cc_group in ((ARPS_SIDE_FIRST_RE, 1, 2),
                                          (ARPS_VOLUME_FIRST_RE, 2, 1)):
        found: dict[str, set[float]] = {"left": set(), "right": set()}
        for m in pattern.finditer(text):
            side = "left" if m.group(side_group).lower().startswith("l") else "right"
            cc = float(m.group(cc_group))
            if 100 <= cc <= 1000:
                found[side].add(cc)
        if len(found["left"]) == 1 and len(found["right"]) == 1:
            return found["left"].pop(), found["right"].pop()
    return None, None


def arps_listing_url(base_url: str, page: int) -> str:
    """Listing URL for `page`, scoped to the consent signatory's cases."""
    path = (ARPS_GALLERY_PATH if page == 1
            else f"{ARPS_GALLERY_PATH}page/{page}/")
    return f"{base_url}{path}?surgeon={ARPS_SURGEON}"


def arps_parse_specs(title: str, procedure: str, narrative: str,
                     warnings: list[str] | None = None) -> sg.CaseSpecs:
    """CaseSpecs from a case's three published text blocks.

    The Case Details block comes in two shapes on the same gallery - a chart
    ('Implant size: 325cc, Placement: Sub-pectoral, dual plane, ...') and a
    sentence ('9 months after 400cc smooth round moderate plus profile breast
    implants, sub-muscular dual plane, inframammary incisions') - and some cases
    publish both in one paragraph. Both are parsed; the chart wins where they
    disagree, because it is the clinic's own structured statement.
    """
    specs = sg.CaseSpecs()
    specs.summary = narrative

    # --- chart fields -----------------------------------------------------
    matches = list(ARPS_LABEL_RE.finditer(narrative))
    for i, m in enumerate(matches):
        label = m.group(1)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(narrative)
        value = narrative[m.end():end].strip().strip(",.;").strip()
        if value:
            specs.fields[label] = value

    def field(*names: str) -> str:
        for label, value in specs.fields.items():
            if label.lower() in names:
                return value
        return ""

    age = field("age")
    m = re.match(r"(\d{2})", age)
    if m:
        specs.age = int(m.group(1))
    elif not age:
        m = ARPS_AGE_RE.search(narrative)
        if m:
            specs.age = int(m.group(1))

    height = field("height", "ht")
    if height:
        specs.height = height
        m = ARPS_HEIGHT_CM_RE.match(height)
        if m and 120 <= float(m.group(1)) <= 220:
            specs.height_cm = float(m.group(1))
    weight = field("weight", "wt")
    m = ARPS_WEIGHT_KG_RE.match(weight)
    if m and 30 <= float(m.group(1)) <= 250:
        specs.weight_kg = float(m.group(1))

    # --- volume -----------------------------------------------------------
    # Three sources, tried in this order: the case's own headline (h2.text-title,
    # e.g. '275CC M+ Smooth Round Breast Implants'), the chart's implant-size
    # field, then the narrative. The headline leads because it is the clinic's
    # most consistently formatted statement of the volume - every asymmetric
    # case writes it 'Left 450cc / Right 425cc' there, while the narrative for
    # the same case may write '400cc right 375cc left', whose two readings
    # disagree about which side is which - and because several cases publish a
    # volume ONLY there.
    #
    # Within each source, a per-side reading wins over the shared symmetric one;
    # across sources, the first that names BOTH sides wins, and failing that the
    # first that names any volume at all. `volume_cc()` averages the two, per
    # the schema.
    size = field("implant size", "size implant", "breast implant size")
    sources = [("headline", title), ("chart", size), ("narrative", narrative)]
    readings = []
    for name, text in sources:
        if not text:
            continue
        left, right = arps_sided_volumes(text)
        if left is None and right is None:
            left, right = sg.parse_fill_volumes(text)
        if left is not None or right is not None:
            readings.append((name, left, right))
    chosen = next((r for r in readings if r[1] is not None and r[2] is not None),
                  readings[0] if readings else None)
    if chosen is not None:
        _, specs.left_cc, specs.right_cc = chosen
    # Two sources naming different volumes is a fact about the page, not
    # something to resolve silently: it means one of the clinic's own captions
    # is wrong about this patient, and a curator should see which.
    if warnings is not None and len(readings) > 1:
        averages = {name: round(sum(v for v in (left, right) if v is not None)
                                / len([v for v in (left, right) if v is not None]))
                    for name, left, right in readings}
        if len(set(averages.values())) > 1:
            warnings.append(
                "sources disagree on implant volume ("
                + ", ".join(f"{n} {v}cc" for n, v in averages.items())
                + f"); recorded the {chosen[0]}'s {sg.volume_cc(specs)}cc")

    # --- months post-op ---------------------------------------------------
    # Narrative only, and only where it is stated about this photograph
    # ('6 months following ...', '8 weeks after ...'). 'Age: 33 years' is not a
    # post-op interval, which is why the age field is consumed above first.
    interval_text = ARPS_LABEL_RE.sub(" ", narrative)
    m = ARPS_MONTHS_RE.search(interval_text)
    if m:
        specs.months_post_op = float(m.group(1))
    else:
        m = ARPS_WEEKS_RE.search(interval_text)
        if m:
            specs.months_post_op = round(float(m.group(1)) / 4.345, 1)

    # --- brand / shape / profile -----------------------------------------
    haystack = " ".join([title, procedure, narrative])
    sg.classify_brand_shape_profile(specs, haystack)
    if specs.profile is None:
        for pattern, profile in ARPS_PROFILE_PATTERNS:
            if pattern.search(haystack):
                specs.profile = profile
                break

    # Chart text only, per AGENTS.md - the narrative here describes the
    # operation in the same words the chart uses, but 'placed under the muscle'
    # is prose about a placement rather than the documented value, and this
    # gallery's charts publish 'Placement:' and 'Incision:' explicitly.
    sg.classify_placement_incision(
        specs, " ".join(f"{k}: {v}" for k, v in specs.fields.items()))
    return specs


def arps_combined_procedures(title: str, procedure: str, narrative: str) -> list[str]:
    """Names of any non-augmentation procedure this case's TEXT documents."""
    haystack = " ".join([title, procedure, narrative])
    return [name for pattern, name in ARPS_COMBINED_PATTERNS
            if pattern.search(haystack)]


def arps_parse_listing(listing_html: str, source_url: str) -> list[sg.CaseData]:
    """Every case rendered on one listing page."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases: list[sg.CaseData] = []
    for block in soup.select("div.allslider"):
        gallery = block.select_one("div.patient-gallery")
        if gallery is None:
            continue
        m = ARPS_CASE_ID_RE.match(gallery.get("id", ""))
        if m is None:
            continue
        case = sg.CaseData(case_id=m.group(1), source_url=source_url)

        title_el = block.select_one("h2.text-title")
        procedure_el = block.select_one("div.icon-box b")
        details_el = block.select_one("div.casestudies")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        procedure = procedure_el.get_text(" ", strip=True) if procedure_el else ""
        narrative = details_el.get_text(" ", strip=True) if details_el else ""
        # A non-breaking space inside 'ID\xa037173' and '400 cc\xa0by' would
        # otherwise defeat every \s in the patterns above.
        narrative = narrative.replace("\xa0", " ")

        for i, item in enumerate(block.select("div.gallery_slide_item"), 1):
            before = item.select_one("picture.patientgallery-img.before img")
            after = item.select_one("picture.patientgallery-img.after img")
            if before is None or after is None or not before.get("src") \
                    or not after.get("src"):
                case.warnings.append(f"pair{i}: incomplete before/after markup")
                continue
            case.pairs.append(sg.ImagePair(
                key=f"pair{i}",
                before_url=before["src"],
                after_url=after["src"]))

        # One photograph used by two "pairs" of the same case means at least one
        # of them is not a pair. Case 4578 publishes its oblique-right AFTER as
        # the after of BOTH pair2 and pair4, while pair2's before is the
        # oblique-LEFT view - so pair2 is a before and an after of two different
        # views, which would train the model that changing the view is part of
        # the augmentation. Nothing downstream can see this: both halves are
        # clean, full-resolution, correctly labelled photographs of the same
        # patient. It is reported rather than resolved here because the parser
        # cannot tell WHICH pair is the sound one - that is a view call, and the
        # annotation file settles it by labelling only the sound pair.
        seen_urls: dict[str, str] = {}
        for pair in case.pairs:
            for role, url in (("before", pair.before_url), ("after", pair.after_url)):
                where = f"{pair.key} {role}"
                if url in seen_urls:
                    case.warnings.append(
                        f"{where} is the same photograph as {seen_urls[url]}; "
                        "at least one of those two is not a before/after pair - "
                        "annotate only the pair whose halves share a view")
                else:
                    seen_urls[url] = where

        # A filename naming another operation is reported, never acted on. The
        # exclusion rule reads the case TEXT (a slug let 86 combined cases
        # through at the Etna clinics), and here the filename is the least
        # trustworthy field on the page: case 2971 publishes an
        # '...-Abdominoplasty-7-...jpg' as its BEFORE and a
        # '...-Breast-Augmentation-...jpg' as its AFTER. That is worth a
        # curator's eye - it may be a mislabelled upload or a cross-wired pair -
        # but it is not evidence about what operation this patient had.
        for pair in case.pairs:
            for role, url in (("before", pair.before_url), ("after", pair.after_url)):
                stem = unquote(url.rsplit("/", 1)[-1])
                other = ARPS_FILENAME_PROCEDURE_RE.search(stem)
                if other:
                    case.warnings.append(
                        f"{pair.key} {role} filename names '{other.group(0)}', "
                        "not breast augmentation; case text screened instead")

        combined = arps_combined_procedures(title, procedure, narrative)
        if combined:
            case.warnings.append(
                "not pure breast augmentation (case text documents "
                + ", ".join(combined) + "); excluded by captain ruling")
            case.pairs = []
        if case.case_id in ARPS_CENSORED_CASES:
            case.warnings.append(
                "censored/annotated photographs: "
                + ARPS_CENSORED_CASES[case.case_id] + "; withheld")
            case.pairs = []
        if not case.pairs and not case.warnings:
            case.warnings.append("no usable image pairs")
        if not narrative:
            case.warnings.append("no Case Details block published")

        case.specs = arps_parse_specs(title, procedure, narrative, case.warnings)
        cases.append(case)
    return cases


def arps_collect_cases(cfg, fetcher) -> list[sg.CaseData]:
    """Walk the surgeon-scoped listing until a page renders no cases."""
    cases: list[sg.CaseData] = []
    seen: set[str] = set()
    for page in range(1, ARPS_MAX_PAGES + 1):
        url = arps_listing_url(cfg.base_url, page)
        html = sg._fetch_optional(fetcher, url, f"{cfg.slug}_listing_p{page}.html")
        if html is None:
            break
        page_cases = arps_parse_listing(html, url)
        if not page_cases:
            break
        for case in page_cases:
            if case.case_id in seen:
                continue
            seen.add(case.case_id)
            cases.append(case)
    arps_flag_shared_case_text(cases)
    return cases


def arps_flag_shared_case_text(cases: list[sg.CaseData]) -> None:
    """Warn where two cases publish all but the same Case Details paragraph.

    Cases 2610 and 2603 publish '3 months after a breast augmentation
    mammoplasty using 360 cc round implants placed under the muscle WITH/USING
    inframammary incisions by Dr Eddie Cheng, Brisbane' - one word apart - and
    the photographs are demonstrably two different patients. So one of those two
    carries the other's chart, and `volume_cc`, which is the training LABEL, is
    wrong for one of them. Nothing else in the pipeline can notice: both cases
    parse cleanly and agree with themselves.

    Near-identical rather than identical is the whole point: the two real
    instances here differ by one word (2610/2603) and by a trailing full stop
    (2866/2868), so an equality test finds neither.

    A warning and not an exclusion, because the parser cannot tell which case
    owns the text; deciding that is an ask to the practice.
    """
    normalised = [(case, " ".join(case.specs.summary.split()).lower())
                  for case in cases]
    for i, (case, text) in enumerate(normalised):
        if not text:
            continue
        others = [other.case_id for other, other_text in normalised[i + 1:]
                  if other_text and
                  SequenceMatcher(None, text, other_text).ratio() >= 0.95]
        if not others:
            continue
        for c in cases:
            if c.case_id in others or c is case:
                names = [o for o in [case.case_id, *others] if o != c.case_id]
                c.warnings.append(
                    "publishes all but the same Case Details text as case(s) "
                    + ", ".join(names)
                    + "; if they are different patients, the volume/profile on "
                      "one of them belongs to the other")
