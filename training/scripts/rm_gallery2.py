#!/usr/bin/env python3
"""RM Gallery 2 (Rosemont Media) patient-gallery parser - one family, 14 clinics.

Rosemont Media builds a WordPress patient gallery whose plugin markup is shared
across every practice on it. Fifteen of the sixteen clinics consented on
2026-08-25 run it and fourteen are registered (drtabbal is withheld; see its
note in `scrape_gallery.CLINICS`), so this module is one parser configured
fourteen times rather than fourteen parsers; `scrape_gallery.collect_cases`
imports it for `kind="rm_gallery2"`.

The family is unusually cheap to collect for two reasons that are worth stating
because most of this corpus's clinics have neither: it stores **separate before
and after files** (no composite, so no midpoint split and no half-resolution
penalty), and it **enumerates completely on one listing page** with no AJAX, no
pagination and nothing under a `robots.txt` Disallow.

Markup contract
---------------
Listing (`<gallery_path>`): every case is linked inline as
`<gallery_path>patient-<n>` on a single page. All 16 galleries enumerated
exactly to the count the prospecting run measured, so the parsed link count IS
the gallery's size; these galleries publish no declared total to reconcile
against, and `rm_list_cases` returning fewer links than the site shows would be
a parser bug rather than a short gallery.

Case page (`<gallery_path>patient-<n>`):

- **`before-img` / `after-img` are the plugin's own classes and the only
  reliable pairing signal.** They are present, balanced and in document order on
  all sixteen galleries. Nothing else is: `img-set` appears on one clinic,
  `hdng` on three, and the asset's own `-b`/`-a` filename suffix is a property
  of the upload rather than of the slot.
- **The case container is theme-specific, and reading the wrong one silently
  returns no specs** (the lakeshore lesson, and error 2 of the prospecting run).
  Three layouts cover the family: `section|div.single-case-content` (14),
  `div|section.case-wrap` (plasticsurgerycarolina), `article.content` (weston).
  `case-wrap` is a `<section>` on some themes and a `<div>` on others.
- **The spec block sits either side of the photographs.** coberly and
  jkplasticsurgery print it above `div.img-wrap`, most clinics below it, and
  santabarbarabreast puts it in a `div.patient-full-details` that is a SIBLING
  of `case-wrap` rather than a child - which is why the region is taken from
  `single-case-content` where that exists rather than from `case-wrap`.
- **The trailing category list is inside the case container and must be
  stripped.** `ul.archive-grandchildren` lists the practice's other breast
  categories - "Breast Reconstruction / Breast Reduction / Breast Lift /
  Breast Augmentation" - so a purity screen run over the raw container text
  rejects every case in the gallery as a lift. On doctorleber that is 94 of 94.
- **Two clinics lazy-load their photographs** (boynton, sbplasticsurgery): the
  URL is on `data-src` and `src` holds a base64 GIF placeholder. A naive
  `<img src>` read yields a `data:` URI, not a photograph.
- **The page's `<img>` is not necessarily the original.** Assets live at
  `/wp-content/uploads/rmgallery2/RMG<id>-<case>-<b|a>/<variant>.<ext>`.
  doctorleber serves `medium.jpeg` (600x897) where `original.jpeg` is
  2592x3872 - measuring what the page serves understates that clinic four-fold.
  `rm_full_res` substitutes the `original` stem and keeps the served extension
  (one clinic publishes both `.jpeg` and `.png` assets). `large` 404s, so
  `original` is the ceiling.

Case identity
-------------
The case key is the **RM case number embedded in the asset path** (the `-2149-`
of `RMG2671321081-2149-b`), never the `patient-<n>` URL slug. Every case page
carries exactly one such number. The slug is a curated DISPLAY POSITION: on
irasavetskymd the RM numbers run 366, 328, 2009, 1193 ... in slug order, and on
teleosplasticsurgery the two newest cases sit last rather than first. Keying on
the slug would re-point every pair id in the clinic the moment the practice
reorders its gallery. This is the `sculpted` lesson (AGENTS.md) applied to a
second platform; the display slug is kept in the case's notes.

Volumes
-------
`rm_parse_volumes` reads the sided chart fields and the mL unit locally rather
than through `scrape_gallery.parse_fill_volumes`, for the reason bayside
already records: `mL` cannot go into the shared `VOLUME_UNIT` because it would
read sanantonio's "225 mL of lipoaspirate" as an implant volume. Here the unit
is only ever accepted next to an implant, so the local reader is safe where a
shared one would not be. drcoberly publishes exclusively in mL ("425 mL smooth
round saline implants"); under the captain's 2026-08-15 ruling mL reads as cc.

A bare number is read ONLY out of a labelled implant field (`Implant Size:
421`), never out of prose - the mwps precedent. A published RANGE is not a
measurement and is not read (the sixsurgery precedent): drbottger's
"400-425 cc" and najera's "Weight: 151-160 lbs" both stay unrecorded.

Nor are two figures that do not say which one is the implant: a saline implant
published with its fill ("325 cc filled to 350 cc") and an unsided slash pair
("325/375 cc") both record no volume, rather than one of the figures or their
average.
"""

from __future__ import annotations

import html as _html
import re

import scrape_gallery as sg

# ---------------------------------------------------------------------------
# Markup helpers
# ---------------------------------------------------------------------------

_BLOCK_TAG_RE = re.compile(r"<(/?)(div|section|article|main)\b[^>]*>", re.I)

# The three theme layouts, most specific first. `single-case-content` wraps both
# the photographs and a sibling details block, so it is preferred where present.
_REGION_PATTERNS = (
    re.compile(r'<(?:section|div|main)[^>]*class="[^"]*\bsingle-case-content\b[^"]*"[^>]*>', re.I),
    re.compile(r'<(?:div|section)[^>]*class="[^"]*\bcase-wrap\b[^"]*"[^>]*>', re.I),
    re.compile(r'<article[^>]*class="[^"]*\bcontent\b[^"]*"[^>]*>', re.I),
)

# Chrome that lives INSIDE the case container on one theme or another. Every one
# of these is stripped before the container's text is read, because that text is
# both the purity screen and the spec source: `archive-grandchildren` alone
# rejects a whole gallery as a lift.
_STRIP_PATTERNS = (
    re.compile(r'<ul[^>]*class="[^"]*\barchive-grandchildren\b.*?</ul>', re.I | re.S),
    re.compile(r'<(?:p|div|span)[^>]*class="[^"]*\bdisclaimer\b[^"]*"[^>]*>.*?</(?:p|div|span)>',
               re.I | re.S),
    re.compile(r'<(?:p|div|nav|section)[^>]*class="[^"]*\b(?:salacious-crumb|crumb-wrapper'
               r'|gallery-nav|back-btn|pagination-buttons|bna-label)\b.*?</(?:p|div|nav|section)>',
               re.I | re.S),
    re.compile(r'<(?:section|div)[^>]*class="[^"]*\bpage-title\b.*?</(?:section|div)>', re.I | re.S),
    # The case's own <h1> ("Breast Augmentation Patient 1") and the block
    # heading that labels the spec list ("Patient Information", "Case Details",
    # "Details:") are the theme naming the page, not the surgeon describing the
    # patient. Left in, they land in `notes` as though they were the clinic's
    # description of the case.
    re.compile(r'<h1[^>]*>.*?</h1>', re.I | re.S),
    re.compile(r'<div[^>]*class="[^"]*\b(?:details-)?hdng\b[^"]*"[^>]*>.*?</div>', re.I | re.S),
)

_NOISE_RE = (
    re.compile(r"<script.*?</script>", re.I | re.S),
    re.compile(r"<style.*?</style>", re.I | re.S),
    re.compile(r"<!--.*?-->", re.S),
)

# Case number as the plugin writes it into every asset path.
RM_ASSET_RE = re.compile(r"/rmgallery2/(RMG\d+)-(\d+)-([ab])/([^/\"']+)", re.I)
RM_CASE_SLUG_RE = re.compile(r"(patient-\d+)/?$", re.I)


def _strip_noise(html: str) -> str:
    for pattern in _NOISE_RE:
        html = pattern.sub("", html)
    return html


def _element_at(html: str, start: int) -> str:
    """The full block element beginning at `start`, by tag-depth balancing.

    Falls back to the remainder of the document when the markup does not
    balance, which is what an unclosed `<div>` in a hand-edited theme produces;
    an over-long region costs a little stripping, a truncated one costs specs.
    """
    match = _BLOCK_TAG_RE.match(html, start)
    if not match:
        return html[start:]
    depth = 0
    for tag in _BLOCK_TAG_RE.finditer(html, start):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[start:tag.end()]
    return html[start:]


def rm_case_region(case_html: str) -> str:
    """The single-case region of a case page, whichever theme drew it."""
    html = _strip_noise(case_html)
    for pattern in _REGION_PATTERNS:
        match = pattern.search(html)
        if match:
            return _element_at(html, match.start())
    return ""


def rm_case_text(case_html: str) -> str:
    """The case's OWN published text: specs and narrative, no chrome.

    This is both the purity screen and the spec source, so everything the theme
    puts in the container that the practice did not write about THIS patient is
    removed first - the photographs, the sibling category list, the disclaimer
    and the gallery navigation.
    """
    region = rm_case_region(case_html)
    if not region:
        return ""
    # Drop the photograph grid wherever the theme placed it.
    while True:
        match = re.search(r'<div[^>]*class="[^"]*\bimg-wrap\b[^"]*"[^>]*>', region, re.I)
        if not match:
            break
        region = region[:match.start()] + region[match.start() + len(
            _element_at(region, match.start())):]
    for pattern in _STRIP_PATTERNS:
        region = pattern.sub("\n", region)
    region = re.sub(r"<br\s*/?>", "\n", region, flags=re.I)
    region = re.sub(r"</(p|div|li|h\d|ul|section|article)>", "\n", region, flags=re.I)
    region = re.sub(r"<[^>]+>", " ", region)
    region = _html.unescape(region)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in region.split("\n")]
    return "\n".join(line for line in lines
                      if line and line not in _BOILERPLATE_LINES
                      and not _BOILERPLATE_LINE_RE.match(line))


# Theme furniture that survives tag-stripping as bare text on one theme or
# another. Dropped so it neither reaches `notes` as though the surgeon had
# written it nor feeds the purity screen.
# The theme naming the page rather than the surgeon describing the patient,
# on the themes that render it as plain text instead of a heading element:
# the display title ("Patient 12") and the gallery category ("Breast
# Augmentation"). Both otherwise open the case's `notes`.
_BOILERPLATE_LINE_RE = re.compile(
    r"^(?:Patient\s*#?\s*\d+|Breast\s+Augmentation|Case\s*#?\s*\d+"
    r"|Patient\s+(?:Details|Information|Notes)|Case\s+Details|Details"
    r"|Back\s+to\s+Main\s+Gallery)[.:]?$", re.I)

_BOILERPLATE_LINES = {
    "Before", "After", "Next", "Previous", "Next Patient", "Previous Patient",
    "Back to Gallery", "Gallery", "View Gallery", "Stay Connected",
    "Contact Us For A Complimentary Consultation",
    "Contact Us For a Complimentary Consultation",
}


def rm_list_cases(listing_html: str, gallery_path: str) -> list[str]:
    """Every case slug linked from the (single, unpaginated) listing page."""
    slugs: list[str] = []
    pattern = re.escape(gallery_path) + r"(patient-\d+)/?(?=[\"'])"
    for match in re.finditer(pattern, listing_html, re.I):
        if match.group(1) not in slugs:
            slugs.append(match.group(1))
    return sorted(slugs, key=lambda s: int(s.rsplit("-", 1)[1]))


def rm_full_res(url: str) -> str:
    """The `original` variant of an RM asset URL, keeping its own extension.

    doctorleber serves `medium.jpeg` where the original is 2592x3872; one
    clinic publishes `.png` assets alongside `.jpeg`, so the extension is the
    asset's and is never normalised. `large` 404s across the family.
    """
    return re.sub(r"/(?:medium|large|thumb(?:nail)?|small)(\.\w+)(?=$|[?#])",
                  r"/original\1", url)


def rm_pair_urls(case_html: str) -> list[tuple[str, str]]:
    """(before, after) full-resolution URLs, in the page's own document order.

    Pairing is by the plugin's `before-img`/`after-img` classes rather than by
    the asset's `-b`/`-a` filename suffix: the classes are the slot the practice
    put the photograph in, and they are the one signal present on every theme.
    """
    html = _strip_noise(case_html)
    pairs: list[tuple[str, str]] = []
    pending: dict[str, str] = {}
    for match in re.finditer(r'class="([^"]*\b(?:before|after)-img\b[^"]*)"', html):
        slot = "before" if "before-img" in match.group(1) else "after"
        url = _first_image_url(html[match.end():match.end() + 2000])
        if url is None:
            continue
        pending[slot] = rm_full_res(url)
        if "before" in pending and "after" in pending:
            pairs.append((pending["before"], pending["after"]))
            pending = {}
    return pairs


def _first_image_url(fragment: str) -> str | None:
    """The photograph URL of the next `<img>`, honouring lazy-loading.

    boynton and sbplasticsurgery put the real URL on `data-src` and a base64 GIF
    in `src`, so `data-src` wins wherever both are present and a `data:` URI is
    never returned as a photograph.
    """
    tag = re.search(r"<img\b[^>]*>", fragment, re.I)
    if not tag:
        return None
    for attr in ("data-src", "data-lazy-src", "src"):
        value = re.search(rf'\b{re.escape(attr)}="([^"]+)"', tag.group(0), re.I)
        if value and not value.group(1).startswith("data:"):
            return value.group(1)
    return None


def rm_case_number(case_html: str) -> str | None:
    """The RM case number every asset on the page carries, or None.

    Returns None when the page's assets disagree, which would mean the theme
    rendered photographs from two cases into one page; the caller warns rather
    than picking one.
    """
    numbers = {m.group(2) for m in RM_ASSET_RE.finditer(_strip_noise(case_html))}
    if len(numbers) == 1:
        return numbers.pop()
    return None


# ---------------------------------------------------------------------------
# Purity screen - on the case's OWN text, never on a filename or a slug
# ---------------------------------------------------------------------------

# Scanned against the case's published description. A combined case whose
# photographs happen to sit in the augmentation category is still combined:
# these galleries file by category, and drbottger's patient-1 is filed under
# breast augmentation while its own text says "with breast lift".
RM_IMPURE_PATTERNS = [
    (re.compile(r"\bmastopex\w*", re.I), "mastopexy"),
    (re.compile(r"\blift(s|ed|ing)?\b", re.I), "breast lift"),
    (re.compile(r"\breduction\b", re.I), "reduction"),
    (re.compile(r"\brevision\b", re.I), "revision"),
    (re.compile(r"\bexplant\w*\b", re.I), "explant"),
    (re.compile(r"\bcapsulectomy\b|\bcapsulorrhaphy\b", re.I), "capsulectomy"),
    (re.compile(r"\bremov\w*\b[^.]{0,60}\bimplant", re.I), "implant removal"),
    (re.compile(r"\bimplants?\b[^.]{0,60}\bremov\w*", re.I), "implant removal"),
    (re.compile(r"\breplac\w*\b[^.]{0,60}\bimplant", re.I), "implant exchange"),
    (re.compile(r"\bimplants?\b[^.]{0,60}\breplac\w*", re.I), "implant exchange"),
    (re.compile(r"\bexchange\b", re.I), "implant exchange"),
    (re.compile(r"\bmommy makeover\b", re.I), "mommy makeover"),
    (re.compile(r"\bfat\s+(?:transfer|graft\w*|inject\w*)", re.I), "fat transfer"),
    (re.compile(r"\breconstruction\b", re.I), "breast reconstruction"),
    (re.compile(r"\babdominoplasty\b|\btummy tuck\b", re.I), "abdominoplasty"),
    (re.compile(r"\bliposuction\b|\blipo\b", re.I), "liposuction"),
    (re.compile(r"\bgynecomastia\b", re.I), "gynecomastia"),
    (re.compile(r"\baugmentation[- ]mastopexy\b", re.I), "augmentation-mastopexy"),
]

# A sentence that DECLINES or merely discusses a second procedure does not make
# the case combined - the ncps lesson. This surgeon population writes
# "she did not want a lift" and "we discussed a lift" routinely, and screening
# those as combined costs pure cases outright.
RM_NEGATION_RE = re.compile(
    r"\b(?:no|not|without|declin\w*|avoid\w*|instead of|rather than|opted against"
    r"|did ?n[o']t|does ?n[o']t|would ?n[o']t|chose not|elected not"
    r"|candidate for|considering|discussed|option|alternative|may need"
    r"|would (?:need|require)|will need|future)\b", re.I)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n", text) if s.strip()]


def rm_impure_reason(case_text: str) -> str | None:
    """The combined/revision procedure this case's own text names, or None.

    Screened sentence by sentence so a negated or hypothetical mention does not
    reject a pure case, and so the reason names the sentence that disqualified
    it rather than the whole description.
    """
    for sentence in _sentences(case_text):
        if RM_NEGATION_RE.search(sentence):
            continue
        for pattern, reason in RM_IMPURE_PATTERNS:
            if pattern.search(sentence):
                return reason
    return None


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------

# mL is read HERE and deliberately not added to the shared VOLUME_UNIT: on a
# clinic that publishes fat volumes, "225 mL of lipoaspirate" would read as an
# implant volume (the bayside precedent). In this family the unit only ever
# appears beside an implant.
_RM_VOLUME_UNIT = r"(?:ccs?|mls?|cc\.|m\.?l\.?)\b"
RM_VOLUME_RE = re.compile(rf"(\d{{2,4}}(?:\.\d+)?)\s*{_RM_VOLUME_UNIT}", re.I)
# A bare L/R counts as a side only when punctuated ("R) 457cc") or leading
# straight into a volume ("R 450 cc"); leber's breast widths ("L 12.5cm")
# are not side markers. The third group is always the side of the figure
# that FOLLOWS it.
RM_SIDE_RE = re.compile(
    rf"\b(left|right)\b|\b([LR])\s*[):.]|\b([LR])\s+(?=\d{{2,4}}\s*{_RM_VOLUME_UNIT})", re.I)
# "Right Breast Implant: Smooth Round Moderate Plus Profile 450 cc Silicone" -
# a side label whose figure can sit far along its own field.
RM_SIDE_LABEL_RE = re.compile(r"\b(left|right)(?:\s+(?:breast|implant|side))*\s*:", re.I)
# A published range is not a measurement (the sixsurgery precedent):
# drbottger's "400-425 cc" and najera's "151-160 lbs" both stay unrecorded.
RM_RANGE_RE = re.compile(
    rf"\d{{2,4}}\s*(?:{_RM_VOLUME_UNIT})?\s*(?:-|\u2013|\u2014|\bto\b)\s*\d{{2,4}}\s*{_RM_VOLUME_UNIT}",
    re.I)
# Two figures without saying which is the implant: a saline fill
# ("325 cc filled to 350 cc", "overfilled 425 cc") or an unsided slash pair
# ("325/375 cc", "300/340 c").
RM_FILL_RE = re.compile(r"\bover[- ]?fill(?:ed)?\b|\bfill(?:ed)?\s+to\b", re.I)
RM_SLASH_PAIR_RE = re.compile(
    rf"\d{{2,4}}\s*(?:{_RM_VOLUME_UNIT})?\s*/\s*\d{{2,4}}\s*(?:{_RM_VOLUME_UNIT}|c\b)", re.I)
# A bare number counts only under a labelled implant field (the mwps rule);
# a bare number in prose does not. The field runs to the end of its line.
RM_LABELLED_FIELD_RE = re.compile(
    r"\b(?:implant|implants)\s*(?:size|sizes|volume|details?)?\s*[:\-]\s*(\d{2,4}(?![\d.])[^\n]*)",
    re.I)
_RM_BARE_FIGURE_RE = re.compile(
    r"(?<![\d.])(\d{2,4})(?![\d.])(?!\s*(?:cm|mm|lbs?|pounds?|%|\"|'|\u2019|\u201d))", re.I)
# A manufacturer style or model number ("457 style 15") is not a volume.
_RM_STYLE_PREFIX_RE = re.compile(r"(?:\bstyle|\bmodel|#)\s*#?\s*$", re.I)
_RM_WORD_SIDE_RE = re.compile(r"\b(left|right)\b", re.I)
RM_MONTHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:months?|mos?\b\.?)", re.I)
RM_YEARS_POSTOP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*years?\s+(?:post|after)", re.I)
# Age: a single documented figure only. najera publishes buckets ("Age: 30 - 39"),
# and reading the floor of a bucket would invent a value the clinic never stated.
RM_AGE_NARRATIVE_RE = re.compile(r"\b(\d{2})[\s-]*(?:year|yr)s?[\s-]*old\b", re.I)
RM_AGE_FIELD_RE = re.compile(
    r"\bAge\s*:?\s*(\d{2})\b(?!\s*(?:-|\u2013|\u2014|\bto\b))", re.I)

# Profile, read locally and never through the shared PROFILE_PATTERNS.
#
# Two reasons this cannot be a shared widening. The bare abbreviations MP/HP are
# far too weak a token to put in a corpus-wide pattern (the bayside precedent,
# which is why they are matched CASE-SENSITIVELY here), and a bare "Moderate"
# with no following "profile" is a profile only inside an implant descriptor:
# drtabbal's own prose asks for "a moderate increase in breast size", which is
# not a projection at all. So the spelled-out words are accepted only when they
# sit adjacent to implant vocabulary, which is what the captain's 2026-08-19
# ruling ("Moderate Plus" without the word profile is the same claim as MP)
# actually licenses.
#
# The adjacency is IMMEDIATE - no intervening word - and that is load-bearing
# rather than conservative housekeeping. Allowing even two words of slack made
# drtabbal's "enhanced projection and a moderate increase in breast size" read
# as a moderate profile, writing a projection label onto a case that documents
# none. An unrecorded field is omitted, never defaulted (AGENTS.md).
_RM_IMPLANT_WORD = (r"(?:smooth|round|textured|anatomic|silicone|saline|gel"
                    r"|implants?|\d{2,4}\s*(?:cc|ml))")
_RM_ADJACENT = r"[\s,()-]+"


def _rm_profile_re(word: str) -> re.Pattern:
    """`word` used as a projection: adjacent to implant vocabulary either side."""
    return re.compile(
        rf"(?:{_RM_IMPLANT_WORD}{_RM_ADJACENT}{word}|{word}{_RM_ADJACENT}{_RM_IMPLANT_WORD})",
        re.I)


RM_PROFILE_PATTERNS = [
    # Mentor's in-between "moderate-high" has no schema value, and is not
    # rounded to either neighbour (the Natrelle style-code rule). Checked first
    # so the "high profile" inside it never reads as a high one.
    (re.compile(r"\bmoderate[- ]high\b", re.I), None),
    # A labelled chart field states the projection outright and needs no
    # adjacency test: jkplasticsurgery publishes "Profile: Moderate", where the
    # value FOLLOWS the word rather than preceding it.
    (re.compile(r"\bprofile\s*[:\-]\s*(?:extra|ultra)[- ]high\b", re.I), "extra-high"),
    (re.compile(r"\bprofile\s*[:\-]\s*moderate[- ]plus\b", re.I), "moderate-plus"),
    (re.compile(r"\bprofile\s*[:\-]\s*high\b", re.I), "high"),
    (re.compile(r"\bprofile\s*[:\-]\s*moderate\b(?!\s*plus)", re.I), "moderate"),
    # Spelled out, unambiguous with or without the word "profile". Natrelle
    # Inspira's fill names ("extra full", "full") are not profiles and are not
    # decoded into one.
    (re.compile(r"\b(?:extra|ultra)[- ]high\b", re.I), "extra-high"),
    (re.compile(r"\bmoderate[- ](?:profile[- ])?plus\b|\bmod\.?\s*plus\b", re.I),
     "moderate-plus"),
    (re.compile(r"\bhigh\s+(?:profile|projection)\b", re.I), "high"),
    (re.compile(r"\bmoderate\s+(?:profile|projection)\b", re.I), "moderate"),
    # Bare abbreviations: case-sensitive, per bayside.
    (re.compile(r"\bMP\s*\+|\bMP\s+plus\b"), "moderate-plus"),
    (re.compile(r"\bUHP\b|\bXHP\b"), "extra-high"),
    (re.compile(r"\bMP\b"), "moderate"),
    (re.compile(r"\bHP\b"), "high"),
    # Spelled out inside an implant descriptor only.
    (_rm_profile_re(r"\bhigh\b"), "high"),
    (_rm_profile_re(r"\bmoderate\b"), "moderate"),
]


def rm_profile(case_text: str) -> str | None:
    """The projection this case documents, or None when it documents none."""
    text = " ".join(case_text.split())
    for pattern, profile in RM_PROFILE_PATTERNS:
        if pattern.search(text):
            return profile
    return None


def _rm_assign_sides(text: str, volumes: list[tuple[int, int, float]],
                     after: bool) -> dict[str, float]:
    """Assign each volume the nearest side marker in ONE direction.

    `after=True` reads "295cc Left" (marker follows the figure); `after=False`
    reads "Left: 400cc" (marker precedes it). A marker separated from the
    figure by ANOTHER figure belongs to that one instead.
    """
    spans = [(s, e) for s, e, _ in volumes]
    sides: dict[str, float] = {}
    for start, end, value in volumes:
        best, best_gap = None, None
        for match in RM_SIDE_RE.finditer(text):
            if after and match.group(3):
                continue
            side = (match.group(1) or match.group(2) or match.group(3)).lower()[0]
            side = "left" if side == "l" else "right"
            if after and match.start() >= end:
                gap, lo, hi = match.start() - end, end, match.start()
            elif not after and match.end() <= start:
                gap, lo, hi = start - match.end(), match.end(), start
            else:
                continue
            if gap > 40:
                continue
            if any(lo < vs < hi or lo < ve < hi for vs, ve in spans):
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = side, gap
        if best is not None:
            sides.setdefault(best, value)
    return sides


def rm_parse_volumes(case_text: str) -> tuple[float | None, float | None]:
    """(left_cc, right_cc) from this family's layouts, or (None, None).

    Both orderings of side and volume are live in this family, so the direction
    is decided ONCE for the whole string rather than per figure by nearest
    marker. Nearest-marker-per-figure is subtly wrong in both directions:
    in "Left: 400cc Right: 425cc" the marker AFTER 400cc is nearer than the one
    before it, which files 400 as the right breast and loses the left entirely;
    in "295cc Left and 275cc Right" the opposite rule files 275 as the left.
    The orientation that assigns more figures wins, and a two-sided assignment
    beats a one-sided one.

    A side label on its own field ("Right Breast Implant: ... 450 cc") decides
    its figure outright. A sided figure wins over a bilateral one, a range is
    never read, a fill or an unsided slash pair records nothing, and a bare
    number is read only from a labelled implant field.
    """
    text = " ".join(case_text.split())
    if RM_FILL_RE.search(text) or RM_SLASH_PAIR_RE.search(text):
        return None, None

    labelled = _rm_labelled_sides(case_text)
    if labelled is None:
        return None, None
    if labelled:
        return labelled.get("left"), labelled.get("right")

    volumes = []
    for match in RM_VOLUME_RE.finditer(text):
        window = text[max(0, match.start() - 14):match.end() + 14]
        if RM_RANGE_RE.search(window):
            continue
        volumes.append((match.start(), match.end(), float(match.group(1))))

    if volumes:
        following = _rm_assign_sides(text, volumes, after=True)
        preceding = _rm_assign_sides(text, volumes, after=False)
        # A tie means both readings explain the text; the labelled-chart form
        # ("Left: 400cc") is the one that states the side outright.
        sides = preceding if len(preceding) >= len(following) else following
        if sides:
            return sides.get("left"), sides.get("right")
        value = volumes[0][2]
        return value, value

    return _rm_labelled_bare(case_text)


def _rm_labelled_sides(case_text: str) -> dict[str, float] | None:
    """{side: cc} from side-labelled fields; None when one holds two figures.

    Each label's field runs to the next label or the end of its line, so the
    figure is read wherever along the field the practice printed it. A field
    carrying two figures or a range does not say which is the implant.
    """
    sides: dict[str, float] = {}
    for line in case_text.split("\n"):
        labels = list(RM_SIDE_LABEL_RE.finditer(line))
        for index, label in enumerate(labels):
            end = labels[index + 1].start() if index + 1 < len(labels) else len(line)
            field = line[label.end():end]
            figures = [float(m.group(1)) for m in RM_VOLUME_RE.finditer(field)]
            if len(figures) > 1 or (figures and RM_RANGE_RE.search(field)):
                return None
            if figures:
                sides.setdefault(label.group(1).lower(), figures[0])
    return sides


def _rm_labelled_bare(case_text: str) -> tuple[float | None, float | None]:
    """(left_cc, right_cc) from the bare figures of a labelled implant field.

    One unsided figure is bilateral ("Implant Size: 421"). Several figures are
    read only when each names its own side ("350 ... on Right//325 ... on
    Left"); otherwise none of them is kept.
    """
    match = RM_LABELLED_FIELD_RE.search(case_text)
    if not match:
        return None, None
    value = match.group(1)
    if re.search(r"\d\s*(?:-|–|—|\bto\b|/)\s*\d", value, re.I):
        return None, None
    figures = [figure for figure in _RM_BARE_FIGURE_RE.finditer(value)
               if not _RM_STYLE_PREFIX_RE.search(value, 0, figure.start())]
    sides: dict[str, float] = {}
    for index, figure in enumerate(figures):
        end = figures[index + 1].start() if index + 1 < len(figures) else len(value)
        side = _RM_WORD_SIDE_RE.search(value, figure.end(), end)
        if side:
            sides.setdefault(side.group(1).lower(), float(figure.group(1)))
    if sides and len(sides) == len(figures):
        return sides.get("left"), sides.get("right")
    if len(figures) == 1 and not sides:
        figure = float(figures[0].group(1))
        return figure, figure
    return None, None


def rm_parse_specs(case_text: str) -> sg.CaseSpecs:
    """Structured specs from a case's published text.

    Chart fields (`Label: value`) are read as fields; a narrative case yields a
    summary and whatever the shared classifiers can name in it. Nothing is
    inferred: a field the practice did not publish stays None.
    """
    specs = sg.CaseSpecs()
    chart_lines, prose_lines = [], []
    for line in case_text.split("\n"):
        if re.match(r"^[A-Za-z][A-Za-z .'/#-]{1,32}:\s*\S", line):
            chart_lines.append(line)
        elif line.endswith(":"):
            chart_lines.append(line)
        else:
            prose_lines.append(line)

    # A label alone on its line takes the NEXT line as its value (najera's
    # theme renders every chart field that way).
    pending_label = None
    for line in case_text.split("\n"):
        if pending_label is not None:
            specs.fields.setdefault(pending_label, line.strip())
            pending_label = None
            continue
        if re.match(r"^[A-Za-z][A-Za-z .'/#-]{1,32}:$", line):
            label = line[:-1].strip().lower()
            if label not in ("details", "patient details", "case details",
                             "patient information", "patient notes"):
                pending_label = label
            continue
        match = re.match(r"^([A-Za-z][A-Za-z .'/#-]{1,32}):\s*(\S.*)$", line)
        if match:
            specs.fields.setdefault(match.group(1).strip().lower(),
                                    match.group(2).strip())

    narrative = " ".join(prose_lines).strip()
    if narrative:
        specs.summary = narrative

    specs.left_cc, specs.right_cc = rm_parse_volumes(case_text)

    age_match = (RM_AGE_NARRATIVE_RE.search(case_text)
                 or RM_AGE_FIELD_RE.search(case_text))
    if age_match:
        age = int(age_match.group(1))
        if 18 <= age <= 80:
            specs.age = age
    for key in ("height", "ht"):
        if key in specs.fields:
            specs.height = specs.fields[key]
            break
    height_source = specs.height or narrative
    height_cm = _rm_height_cm(height_source)
    if height_cm is not None:
        specs.height_cm = height_cm
    weight = _rm_weight_lbs(specs.fields, narrative)
    if weight is not None:
        specs.weight_lbs = weight
        specs.weight_kg = sg.pounds_to_kg(weight)

    months = RM_MONTHS_RE.search(case_text)
    if months:
        specs.months_post_op = float(months.group(1))
    else:
        years = RM_YEARS_POSTOP_RE.search(case_text)
        if years:
            specs.months_post_op = float(years.group(1)) * 12

    sg.classify_brand_shape_profile(specs, case_text)
    # Profile is decided by this family's own reader, which accepts the
    # spelled-out and abbreviated forms the shared patterns deliberately do
    # not; the shared classifier still owns brand and shape.
    specs.profile = rm_profile(case_text)
    # Placement and incision are chart values here as well as narrative ones -
    # this family prints "Dual-Plane Subpectoral" as the case's own procedure
    # line - so the whole case text is the chart for these two fields.
    sg.classify_placement_incision(specs, case_text)
    return specs


# The abbreviation's full stop sits OUTSIDE the word boundary (`ft\b\.?`), and
# the unit carries no leading boundary. Written the other way round
# (`\bft\.?\b`), weston's `5 ft. 9 in.` backtracked to a bare `5 ft`, dropped
# the inches and recorded every patient as 152.4 cm; pscarolina's run-together
# `5ft. 2in.` matched nothing at all.
_RM_HEIGHT_FT_IN_RE = re.compile(
    r"(\d)\s*(?:'|’|ft\b\.?|foot\b|feet\b)\s*,?\s*(?:(\d{1,2})(?!\d)\s*(?:\"|”|''|in\b\.?|inch(?:es)?\b)?)?",
    re.I)
_RM_WEIGHT_RE = re.compile(r"(\d{2,3})\s*(?:lbs?\.?|pounds?)\b", re.I)


def _rm_height_cm(text: str) -> float | None:
    """Height in cm from a single documented figure; None for a range.

    najera publishes buckets (`5' 0" - 5' 5"`), which are not a measurement.
    """
    if not text:
        return None
    if re.search(r"(?:'|’|ft|foot|feet)[^\n]{0,12}(?:-|–|—|\bto\b)", text, re.I):
        return None
    match = _RM_HEIGHT_FT_IN_RE.search(text)
    if not match:
        return None
    feet = int(match.group(1))
    inches = int(match.group(2) or 0)
    if not (3 <= feet <= 7) or inches > 11:
        return None
    return round((feet * 12 + inches) * 2.54, 1)


def _rm_weight_lbs(fields: dict, narrative: str) -> int | None:
    for key in ("weight", "wt"):
        if key in fields:
            value = fields[key]
            if re.search(r"(?:-|–|—|\bto\b)", value):
                return None  # najera's '151-160 lbs' bucket
            match = _RM_WEIGHT_RE.search(value) or re.match(r"^\s*(\d{2,3})\s*$", value)
            return int(match.group(1)) if match else None
    # A narrative figure is a weight only when it is not a weight CHANGE:
    # sbschooler 1722's "lost 70 pounds after weight loss surgery" was once
    # recorded as a 31.8 kg patient.
    for match in _RM_WEIGHT_RE.finditer(narrative):
        if not _RM_WEIGHT_CHANGE_RE.search(narrative[max(0, match.start() - 24):match.start()]):
            return int(match.group(1))
    return None


# The change verb must lead straight into the figure; one hedging word between
# is allowed. Anything looser swallows a real weight later in the sentence.
_RM_WEIGHT_CHANGE_RE = re.compile(
    r"\b(?:lost|lose|loses|losing|loss\s+of|gain(?:ed|ing|s)?|dropped|shed)\s+"
    r"(?:(?:about|over|nearly|almost|around|approximately|roughly|some)\s+)?$",
    re.I)


# ---------------------------------------------------------------------------
# Case assembly
# ---------------------------------------------------------------------------


def rm_parse_case(case_html: str, case_slug: str, source_url: str) -> sg.CaseData:
    """One RM Gallery 2 case page.

    An impure case is RETURNED carrying its reason and no pairs, so a run
    accounts for every published case rather than quietly shrinking the gallery
    to the ones it liked (the wyten rule).

    Views are undocumented everywhere in this family - the plugin publishes no
    view label, and alt text is empty or the word "After" - so every pair needs
    a visual annotation and carries only its ordinal key here.
    """
    case_number = rm_case_number(case_html)
    case_id = case_number or case_slug
    case = sg.CaseData(case_id=case_id, source_url=source_url)
    if case_number is None:
        case.warnings.append(
            f"{case_slug}: no single RM case number on the page; "
            "keyed on the display slug instead")

    text = rm_case_text(case_html)
    case.specs = rm_parse_specs(text)
    if case_number is not None:
        case.specs.fields.setdefault("gallery slug", case_slug)

    reason = rm_impure_reason(text)
    if reason:
        case.warnings.append(f"excluded: not a pure augmentation ({reason})")
        return case

    for index, (before, after) in enumerate(rm_pair_urls(case_html), start=1):
        case.pairs.append(sg.ImagePair(key=f"pair{index}",
                                       before_url=before, after_url=after))
    if not case.pairs:
        case.warnings.append("no before/after pairs found on the case page")
    return case


# Duplicate patients are found on the PIXELS, not on the published text.
#
# Text was the obvious signal and it is the wrong one, in both directions. One
# practice here publishes across two domains and republishes patients on both,
# but its two copies differ by a word or a full stop, so exact text matching
# found 5 of them where the real overlap is 33 (the arps "near-identical, not
# identical" lesson). And loosening to a similarity threshold is worse: inside
# a SINGLE gallery, weston patients 52 and 53 publish byte-identical chart text
# (29, 5'2", 110 lbs, 34A-34C, 339cc) and leber 30 and 38 match at 0.963, and
# opening all four shows four different women. Merging on text would have
# thrown away real patients to remove imaginary duplicates.
#
# A perceptual hash of the photograph settles it by evidence.
#
# The threshold is 14 of 256 and it was measured, not chosen. Over every
# pairwise comparison in the five largest galleries, the republished patients
# sit at 0 (weston 3888/4070, leber 1388/2286 and 1389/2287, pscarolina
# 11071/10968), 10 and 12 (coberly 3579/4212 and 3580/4213 - re-encoded rather
# than byte-identical, which is why an exact-bytes check alone is not enough).
# The nearest pair that is NOT a duplicate is 21 (weston 3749/3067), then 24
# (weston 3819/3825): two different women each, confirmed by opening them at
# full resolution - one has a chest tattoo the other does not.
#
# 14 sits in the empty band between 12 and 21 with margin on both sides. It
# matters that it is not looser: weston photographs a tightly cropped chest
# against a plain wall, so unrelated patients there are far more alike to a
# perceptual hash than they are on a gallery that frames the whole torso, and a
# threshold of 24 discarded two real patients as imaginary duplicates.
DUPLICATE_HAMMING_MAX = 14
_DHASH_SIZE = 16


def rm_image_hash(data: bytes) -> "list[bool]":
    """Row-wise difference hash of an image; comparable by Hamming distance."""
    import io

    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("L").resize(
        (_DHASH_SIZE + 1, _DHASH_SIZE), Image.LANCZOS)
    pixels = np.asarray(image, dtype=np.int16)
    return (pixels[:, 1:] > pixels[:, :-1]).flatten()


def rm_hash_distance(left, right) -> int:
    return int((left != right).sum())
