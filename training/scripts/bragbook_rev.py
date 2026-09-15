#!/usr/bin/env python3
"""BRAG book, LEGACY plugin (the `rev*` markup) - sarasota.

Sarasota Plastic Surgery Center publishes its gallery through BRAG book, the
same vendor as `sanantonio`, and its composites come from the same
`www.bragbook.gallery` asset host. It is nevertheless a different parser, and
`sanantonio_parse_case` finds nothing on it: sanantonio runs the CURRENT BRAG
book WordPress plugin (`div.brag-book-gallery-case-detail-view`, a
Patient Information grid, a thumbnail track), while this site runs the OLDER
plugin, whose every class is prefixed `rev` (`li.revCatImageSet`,
`div.revBA-gallery`, `#revPatientDetails`). Vendor is not markup - the folk and
swan lessons again, from the other side.

Markup contract
---------------
**Listing.** `/photo-gallery/<category>/` renders the first ten cases as
`li.revCatImageSet`, each linking its case page at `<category>/<id>/`. The rest
are served ten at a time by the plugin's infinite scroll, whose next page is the
hidden `a.revJscroll-next` anchor:
`/photo-gallery/?revCatname=<category>&getCategorySets=1&categorySetsStart=N`.
Those pages are bare `<li>` FRAGMENTS with no enclosing `<ul>`. Each carries its
own `revJscroll-next` until the last, which carries none. robots.txt disallows
none of these paths. The gallery publishes NO total, so the reconciliation is
the sum of the pages' own case links against the page count, and the
collection report says so.

**Case key.** The numeric URL segment. The `Breast Augmentation: Patient N`
headline is a DISPLAY position that renumbers when a case is added at the top
(the sculpted lesson), so it is kept in the notes as `Gallery Patient` and never
used as a key.

**Photographs.** Each `figure.revBAcol` inside a `div.revBA-gallery` is one
side-by-side before|after composite (`a.psLink`, `_highres.webp`), split at the
midpoint; its caption reads only `Before / After Angle N`, so the VIEW is not
documented and every pair needs a visual label. The pair key is the asset's own
token (`...-before-and-after-<token>_highres.webp`), not the angle number: a
positional key re-points at a different photograph the moment the plugin
reorders a case (AGENTS.md, running-count keys).

**Watermark.** Sarasota burns an opaque red "SARASOTA PLASTIC SURGERY CENTER"
block into the bottom-right corner of each half, on the backdrop beside the
lower abdomen. It is cropped (captain, 2026-08-19) as a fraction of the
composite's HEIGHT before the split (`ImagePair.composite_bottom_frac`, the mwps
mechanism), because its top edge sits at a constant fraction of height across
every export size - 0.205-0.226, median 0.210 on 1800x599 and 0.217 on
1280x480 - while its pixel offset is not. A handful of composites carry no
block, and at least one carries it on one half only, which is why the crop is
applied to every composite rather than detected per image: the crop is what
keeps every pair framed alike. `REV_BOTTOM_FRAC` holds the per-clinic figure;
another clinic on this plugin measures its own.

**Text.** `#revPatientDetails` is the surgeon's narrative. Two `<ul>` charts
follow: `#revPatientDetailsList` (Age / Weight / Height / Gender / Post-op
Timeline / bra sizes) and `#revPatientDetailsList2` (Implant Type / Shape /
Incision / Volume / Profile / Placement ...). The chart's Age, Weight, Height
and Volume are BUCKETS ('Under 25 years old', 'Between 300cc and 350cc'), and a
bucket is not a measurement (the sixsurgery and folk precedents): they are kept
verbatim in the notes and never converted. The volume comes from the narrative
(`rev_volumes`), and a chart figure that is NOT a range is used only when the
narrative names none. The chart's `Implant Profile` is a labelled field and is
read as the profile; the narrative's own projection words are the fallback.

**Purity.** Screened on the case's own headline, narrative and chart, never on
the category or the asset name - every photograph on this host is named
`breast-augmentation-before-and-after-...`. `rev_impure_reason` is the Rosemont
screen (`rm_gallery2.RM_IMPURE_PATTERNS`, sentence-scoped, with its negation
list) with two measured corrections for this surgeon population's prose, both
of which cost pure cases here: a lift that is RECOMMENDED or that a patient
"may need" is hypothetical, not performed; and `lift` counts only as a
procedure noun, because these surgeons write that anatomic implants were
chosen "to lift her pseudoptotic breasts".
"""

from __future__ import annotations

import html as _html
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import folk_gallery
import rm_gallery2
import scrape_gallery as sg

REV_ASSET_RE = re.compile(r"-([A-Za-z0-9]+)_highres\.\w+$")
REV_PATIENT_RE = re.compile(r"\bPatient\s+(\d+)\b", re.I)
# Hard cap on the infinite-scroll walk. The gallery serves ten cases a page, so
# this is ~2,000 cases - far above anything the site publishes - and exists only
# so a page that links back to itself can never loop the crawler forever.
REV_MAX_PAGES = 200
# Fraction of each composite's HEIGHT cropped off its bottom before the split,
# per clinic; measured, never transferred (module docstring). 0.25 clears the
# worst measured top edge (0.226) by ~10%.
REV_BOTTOM_FRAC = {"sarasota": 0.25}
# A chart value that is a bucket rather than a figure. The unit may sit between
# the first figure and the dash ('300cc - 350cc').
REV_BUCKET_RE = re.compile(
    r"\b(?:between|under|over|less than|more than|up to)\b"
    r"|\d\s*(?:ccs?|mls?)?\s*(?:-|–|—|\bto\b|\band\b)\s*\d|\+",
    re.I)
REV_POSTOP_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(year|month|week)s?\s*$", re.I)

# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

# mL is read here and deliberately NOT added to the shared VOLUME_UNIT (the
# bayside lipoaspirate precedent): on this gallery the unit only ever sits beside
# an implant - '400 mL implants', 'SCX 525 ml' - and combined procedures, the
# only source of a fat volume, are excluded before any volume matters. 'cc's' and
# "cc’s" are the clinic's own spelling on several cases.
_REV_UNIT = r"(?:cc|ml)(?:['’]?s)?\b"
REV_FIGURE_RE = re.compile(rf"\b(\d{{3}})\s*{_REV_UNIT}", re.I)
# A saline implant published with its fill: the fill is the final volume, and
# the shell size beside it is not a second breast. The fill's own unit may be
# omitted when the shell's states it ('360cc filled to 375 on the left').
REV_FILL_RE = re.compile(
    rf"\b(\d{{3}})\s*{_REV_UNIT}[^.;\d]{{0,60}}?\b(?:filled|inflated)\s+to\s+(\d{{3}})\b"
    rf"(?:\s*{_REV_UNIT})?", re.I)
REV_FILL_ONLY_RE = re.compile(
    rf"\b(?:filled|inflated)\s+to\s+(\d{{3}})\s*{_REV_UNIT}", re.I)
REV_SIDE_RE = re.compile(r"\b(left|right)\b", re.I)
REV_BOTH_RE = re.compile(r"\b(?:left\s+and\s+right|right\s+and\s+left|both|each)\b", re.I)
# One clause per implant: sentences, commas, ' and ', and a spaced dash
# ('implants – 365cc on the left'). An unspaced hyphen is a style code
# ('style 20-425cc') and is not a boundary.
REV_CLAUSE_RE = re.compile(r"(?<=[.;])\s+|,\s+|\s+and\s+|\s+[–—-]\s+", re.I)


def _clause_figures(clause: str) -> list[float]:
    fills = [float(m.group(2)) for m in REV_FILL_RE.finditer(clause)]
    fills += [float(m.group(1)) for m in REV_FILL_ONLY_RE.finditer(clause)
              if not REV_FILL_RE.search(clause)]
    figures = fills or [float(m.group(1)) for m in REV_FIGURE_RE.finditer(clause)]
    return list(dict.fromkeys(f for f in figures if 100 <= f <= 1000))


def rev_volumes(text: str) -> tuple[float | None, float | None, str | None]:
    """(left_cc, right_cc, warning) from a narrative, clause by clause.

    A clause naming one side and one figure sets that side; a clause with a
    figure and no side (or 'both'/'each') is a bilateral figure. A fill figure
    replaces the shell size it follows. Anything this cannot read unambiguously
    - one side named and the other not, or two different unsided figures -
    returns no volume and says why, rather than recording half an answer as the
    whole one (the shared reader's measured failure modes on this gallery).
    """
    text = REV_BOTH_RE.sub("both", " ".join(text.split()))
    sides: dict[str, float] = {}
    plain: list[float] = []
    for clause in REV_CLAUSE_RE.split(text):
        figures = _clause_figures(clause)
        if not figures:
            continue
        named = {m.group(1).lower() for m in REV_SIDE_RE.finditer(clause)}
        if len(figures) > 1:
            return None, None, (f"clause publishes several implant figures "
                                f"({', '.join(f'{f:g}' for f in figures)}): {clause!r}")
        if len(named) == 1 and "both" not in clause.lower():
            sides.setdefault(named.pop(), figures[0])
        else:
            plain.append(figures[0])
    if sides:
        if len(sides) == 2:
            return sides["left"], sides["right"], None
        (side, cc), = sides.items()
        return None, None, (f"volume published for the {side} breast only "
                            f"({cc:g}cc); the other side's figure is not "
                            f"attributed in the text")
    distinct = list(dict.fromkeys(plain))
    if len(distinct) == 1:
        return distinct[0], distinct[0], None
    if len(distinct) > 1:
        return None, None, (f"narrative publishes different unsided figures "
                            f"({', '.join(f'{f:g}' for f in distinct)})")
    return None, None, None


# ---------------------------------------------------------------------------
# Profile and purity
# ---------------------------------------------------------------------------

REV_CHART_PROFILES = {
    "high": "high", "moderate plus": "moderate-plus", "moderate": "moderate",
    "extra high": "extra-high", "ultra high": "extra-high",
}

# `lift` as a PROCEDURE, never as the verb ('implants to lift her pseudoptotic
# breasts', case 12287). Everything else is the Rosemont screen unchanged.
REV_LIFT_NOUN_RE = re.compile(
    r"\b(?:breast|a|the|with|and|plus)\s+lift\b|/\s*lift\b"
    r"|\blift\s+(?:and|with|procedure|surgery)\b", re.I)
REV_IMPURE_PATTERNS = [
    (REV_LIFT_NOUN_RE, reason) if reason == "breast lift" else (pattern, reason)
    for pattern, reason in rm_gallery2.RM_IMPURE_PATTERNS]
# A procedure a surgeon RECOMMENDED, one the patient 'may (sometimes) need' or
# 'would benefit from', or one she 'was against', was not performed (cases 4724,
# 19002, 13665). Added to the Rosemont negation list.
REV_HYPOTHETICAL_RE = re.compile(
    r"\b(?:recommend\w*|advis\w*|may\s+(?:\w+\s+)?need|might|would\s+benefit"
    r"|(?:was|is|were)\s+against)\b", re.I)


def rev_impure_reason(case_text: str) -> str | None:
    """The combined/revision procedure this case's own text names, or None."""
    for sentence in rm_gallery2._sentences(case_text):
        if (rm_gallery2.RM_NEGATION_RE.search(sentence)
                or REV_HYPOTHETICAL_RE.search(sentence)):
            continue
        for pattern, reason in REV_IMPURE_PATTERNS:
            if pattern.search(sentence):
                return reason
    return None


# ---------------------------------------------------------------------------
# Listing and case pages
# ---------------------------------------------------------------------------


def rev_case_ids(listing_html: str, gallery_url: str) -> list[str]:
    """Case ids linked from one listing page, in published order."""
    soup = BeautifulSoup(listing_html, "html.parser")
    pattern = re.compile(re.escape(gallery_url.rstrip("/")) + r"/(\d+)/?$")
    ids: list[str] = []
    for item in soup.select("li.revCatImageSet"):
        for anchor in item.find_all("a", href=True):
            m = pattern.match(anchor["href"].split("?")[0].split("#")[0])
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
    return ids


def rev_next_page(listing_html: str, page_url: str) -> str | None:
    """The plugin's own infinite-scroll link to the next ten cases, if any."""
    soup = BeautifulSoup(listing_html, "html.parser")
    anchor = soup.select_one("a.revJscroll-next[href]")
    if anchor is None:
        return None
    return urljoin(page_url, _html.unescape(anchor["href"]))


def _chart(soup: BeautifulSoup, selector: str) -> list[tuple[str, str]]:
    rows = []
    for li in soup.select(f"{selector} > li"):
        strong = li.find("strong")
        if strong is None:
            continue
        label = strong.get_text(" ", strip=True).rstrip(":").strip()
        value = li.get_text(" ", strip=True)[len(strong.get_text(" ", strip=True)):].strip()
        if label and value:
            rows.append((label, value))
    return rows


def _post_op_months(value: str) -> float | None:
    m = REV_POSTOP_RE.match(value)
    if m is None:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    return {"year": n * 12, "month": n, "week": round(n / 4.345, 1)}[unit]


def rev_parse_case(case_html: str, case_id: str, source_url: str) -> sg.CaseData:
    """One case page; an impure case is returned with its reason and no pairs."""
    case = sg.CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")
    gallery = soup.select("div.revBA-gallery figure.revBAcol")
    if not gallery and soup.select_one("#revPatientDetails") is None:
        case.warnings.append("no BRAG book (rev) case markup found")
        return case

    specs = sg.CaseSpecs()
    headline_el = soup.select_one("h1#revPatientHeadline")
    headline = headline_el.get_text(" ", strip=True) if headline_el else ""
    m = REV_PATIENT_RE.search(headline)
    if m:
        specs.fields["Gallery Patient"] = m.group(1)
    narrative_el = soup.select_one("#revPatientDetails")
    specs.summary = (" ".join(narrative_el.get_text(" ", strip=True).split())
                     if narrative_el is not None else "")

    patient = _chart(soup, "#revPatientDetailsList")
    implant = _chart(soup, "#revPatientDetailsList2")
    chart_volume = None
    chart_profile = None
    for label, value in patient + implant:
        key = label.lower()
        if key == "gender":
            specs.gender = value.lower()
            continue
        specs.fields[label] = value
        if key == "post-op timeline":
            specs.months_post_op = _post_op_months(value)
        elif key == "volume" and not REV_BUCKET_RE.search(value):
            chart_volume = sg.labelled_volume(value)
        elif key == "implant profile":
            chart_profile = REV_CHART_PROFILES.get(" ".join(value.lower().split()))
    if specs.summary:
        m = re.search(r"\b(\d{2})[ -]years?[ -]old\b", specs.summary, re.I)
        if m:
            specs.age = int(m.group(1))

    specs.left_cc, specs.right_cc, volume_warning = rev_volumes(specs.summary)
    if volume_warning:
        case.warnings.append(f"no volume recorded: {volume_warning}")
    if specs.left_cc is None and specs.right_cc is None:
        if chart_volume is not None and volume_warning is None:
            specs.left_cc = specs.right_cc = chart_volume
    elif chart_volume is not None and sg.volume_cc(specs) != int(round(chart_volume)):
        case.warnings.append(
            f"chart volume {chart_volume:g}cc disagrees with the narrative "
            f"({sg.volume_cc(specs)}cc); the narrative is kept")
    chart_text = " ".join(f"{label}: {value}" for label, value in implant)
    sg.classify_brand_shape_profile(specs, f"{specs.summary} {chart_text}")
    narrative_profile = folk_gallery.folk_profile(specs.summary)
    if chart_profile and narrative_profile and chart_profile != narrative_profile:
        case.warnings.append(
            f"chart profile '{chart_profile}' disagrees with the narrative's "
            f"'{narrative_profile}'; neither is recorded")
        specs.profile = None
    else:
        specs.profile = chart_profile or narrative_profile
    sg.classify_placement_incision(
        specs, " ".join(v for l, v in implant
                        if l.lower() in ("implant placement", "implant incision")))
    case.specs = specs

    reason = rev_impure_reason("\n".join([headline, specs.summary, chart_text]))
    if reason:
        case.warnings.append(f"excluded: not a pure augmentation ({reason})")
        return case

    for figure in gallery:
        anchor = figure.select_one("a.psLink[href]") or figure.find("a", href=True)
        url = anchor["href"] if anchor is not None else ""
        if not url:
            img = figure.find("img")
            url = img.get("src", "") if img is not None else ""
        token = REV_ASSET_RE.search(url)
        if token is None:
            case.warnings.append(f"composite with no asset token skipped: {url!r}")
            continue
        if any(p.key == token.group(1) for p in case.pairs):
            continue
        case.pairs.append(sg.ImagePair(key=token.group(1), before_url=url,
                                       after_url=url, split_composite=True))
    if not case.pairs:
        case.warnings.append("no usable image pairs")
    return case


def collect(cfg: sg.ClinicConfig, fetcher: sg.PoliteFetcher) -> list[sg.CaseData]:
    """Enumerate every gallery page, then parse every case page.

    Duplicate patients are dropped on the pixels of their before-halves, as on
    every gallery since the Rosemont collection (`_drop_duplicate_patient`).
    """
    cases: list[sg.CaseData] = []
    seen_hashes: list = []
    bottom_frac = REV_BOTTOM_FRAC.get(cfg.slug, 0.0)
    for gallery_path in cfg.gallery_paths:
        gallery = sg.gallery_url(cfg, gallery_path)
        slug = gallery.rstrip("/").rsplit("/", 1)[-1]
        ids: list[str] = []
        url: str | None = gallery
        visited: set[str] = set()
        page = 0
        while url is not None and url not in visited and page < REV_MAX_PAGES:
            visited.add(url)
            key = (f"{cfg.slug}_{slug}_listing.html" if page == 0
                   else f"{cfg.slug}_{slug}_listing_p{page}.html")
            html = fetcher.get(url, key).decode("utf-8", "replace")
            page_ids = rev_case_ids(html, gallery)
            if not page_ids:
                break
            ids.extend(i for i in page_ids if i not in ids)
            url = rev_next_page(html, url)
            page += 1
        print(f"  {gallery}: {len(ids)} case(s) over {page} listing page(s)")
        for case_id in ids:
            case_url = f"{gallery.rstrip('/')}/{case_id}/"
            html = fetcher.get(case_url, f"{cfg.slug}_case_{case_id}.html")
            case = rev_parse_case(html.decode("utf-8", "replace"), case_id, case_url)
            for pair in case.pairs:
                pair.composite_bottom_frac = bottom_frac
            sg._drop_duplicate_patient(fetcher, cfg, case, seen_hashes)
            cases.append(case)
    return cases
