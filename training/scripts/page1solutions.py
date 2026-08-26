#!/usr/bin/env python3
"""Page 1 Solutions patient-gallery parser (one platform family, many clinics).

Page 1 Solutions builds a WordPress "patient gallery" whose markup is the same
across the practices that run it (drbandy.com, thebodydoc.com,
plasticsurgerynow.com and drgregpark.com are all on it in the 2026-08-25
consented batch). This module is that family's parser, kept out of
`scrape_gallery.py` so the family can grow without touching the twenty-odd
parsers already living there; `scrape_gallery.collect_cases` imports it for
`kind="page1solutions"`.

Markup contract
---------------
Listing (`<gallery>/`): every case is one `div.patient-item` inline on a single
page, linking to its own subpage (`href="5342/"`). There is no pagination and
**no declared total** - the page states no count of its own, so a run
reconciles against the number of `div.patient-item` blocks it parsed and says
so rather than quoting a total the gallery never published.

The listing block is NOT a substitute for the case page. It carries a truncated
excerpt of the spec text (`div.patient-text` cut mid-field: drbandy 5325 shows
`Right Implant: 425cc` where the case publishes both sides) and only 6 of the
case's thumbnails, where the case page publishes 5 before/after pairs. Reading
the listing alone loses two fifths of the photographs and mis-states volumes.

Case page (`<gallery>/<slug>/`):

- `div#patient-info` holds the whole published record: one `<p>` of
  `<br>`-separated chart lines, then a `<ul>` of
  `<li>Label: <span class="data">value</span></li>` (Patient #, Gender,
  Ethnicity, Age, Procedure).
- Photographs are `div.row.image-pair` blocks, each containing exactly two
  `div.img-box` images in before, after order. They lazy-load, so the URL is on
  `data-src` and a naive `<img src>` read finds a base64 placeholder.
- Every `src`/`data-src` carries a WordPress size suffix (`-420x315`); the
  original is the same URL with that suffix removed.

**One clinic's chart block is not one markup** (the lakeshore lesson). drbandy
publishes three layouts in the same gallery, and a parser that reads only the
first silently drops volumes:

1. `Implant Size: 550cc` - plain `Label: value` text.
2. `<strong>Implant Size:</strong> 350cc` - label in its own element, so the
   line has to be cut at `<br>` rather than at a text-node boundary.
3. No labels at all: `Fill Volume 325cc bilateral` / `465cc Left` /
   `Submuscular Placement`, one statement per line (drbandy 503/554/555).

Volumes therefore run through `parse_case_volumes()`, which reads the side off
the label where there is one, and otherwise pairs each figure with the side
word in its own line - drbandy writes the volume BEFORE the side (`500cc left &
575cc right`), which is the opposite of the order `scrape_gallery`'s shared
narrative parser assumes, and reading it that way swaps the two.

Sharp edges
-----------
- **The URL slug is the case key, not the published patient number.** They
  usually match and sometimes do not: drbandy `/554/` publishes
  `Patient #: 5540`, and one case is published under WordPress's placeholder
  permalink `/auto-draft/` (`Patient #: 4010`). Slugs are taken verbatim, so a
  non-numeric one is collected rather than silently dropped.
- **The image filename's patient index is not the case key either.** Case 5325
  publishes `Breast-Augmentation-Patient-17-Before-_1.jpg`; that 17 is an index
  within one upload batch and repeats across batches.
- Pairs are positional and carry **no view label**: the alt text is one generic
  string on all ten images and the filename only counts them. Every view comes
  from the visual-annotation file, and laterality is never inferred from
  position.
- Purity is screened on the case's own published **text**, never on a slug or
  on the gallery it is filed under: see `COMBINED_PROCEDURE_RE`.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import re

from bs4 import BeautifulSoup
from bs4.element import Tag

# scrape_gallery imports this module from inside collect_cases (a function-level
# import), so importing it here at module level is not circular: by the time
# anything calls into this parser, scrape_gallery is fully loaded.
import scrape_gallery as sg

# WordPress resize suffix on a media URL: 'name-420x315.jpg' -> 'name.jpg'.
WP_SIZE_SUFFIX_RE = re.compile(r"-\d{2,4}x\d{2,4}(?=\.[A-Za-z]{3,4}$)")

# Words a chart label for an implant volume is built from, in any order:
# 'Implant Size', 'Implant Size Right', 'Right Implant', 'Implant Left',
# 'Left Implant Size'. 'Implant Type'/'Implant Types' carry a word outside this
# set and so are never read as a volume.
VOLUME_LABEL_WORDS = {"implant", "implants", "size", "fill", "volume"}
SIDE_WORDS = {"left", "right"}

VOLUME_UNIT = r"(?:cc|ml)s?\b"
# A volume that names its own side, with the figure FIRST: '465cc Left',
# '500cc left & 575cc right', '750cc on the right'. The gap is bounded and
# stops at a separator so one line's figure cannot reach the next line's side.
VOLUME_SIDE_RE = re.compile(
    rf"(\d{{2,4}}(?:\.\d+)?)\s*{VOLUME_UNIT}[^.;&\n]{{0,12}}?\b(left|right)\b", re.I)
# 'filled to 675 on left' - the final fill volume, which is the implant's real
# size and overrides the shell size quoted beside it ('650cc bags filled to
# 675 on left and 750cc on right', drbandy 5337).
FILLED_TO_SIDE_RE = re.compile(
    rf"filled\s+to\s*(\d{{2,4}}(?:\.\d+)?)\s*(?:{VOLUME_UNIT})?"
    r"[^.;&\n]{0,15}?\bon\s+(?:the\s+|her\s+)?(left|right)\b", re.I)
# A figure with an explicit unit, anywhere. Used for the symmetric case
# ('325cc bilateral') and for unlabelled lines, where a bare number is NOT a
# documented volume (captain's rule) so the unit is required.
VOLUME_WITH_UNIT_RE = re.compile(rf"(\d{{2,4}}(?:\.\d+)?)\s*{VOLUME_UNIT}", re.I)
# A bare number. Only ever read inside a LABELLED implant-size field, where the
# label is what makes it a volume.
BARE_NUMBER_RE = re.compile(r"\b(\d{3,4})\b")

# Schema bounds (dataset_schema.json volume_cc).
VOLUME_MIN, VOLUME_MAX = 100, 1000

# Combined-procedure vocabulary, screened against the case's published text. A
# breast-augmentation gallery still publishes combined cases; the captain's
# ruling excludes every one of them, and the Etna clinics proved that trusting
# the gallery a case is filed under (or its filename slug) lets them through.
# 'lift' is matched as a whole word inside a named procedure so a stray
# 'lifted' cannot trip it, and only procedures a practice actually names are
# listed - nothing here guesses at what a sentence might imply.
COMBINED_PROCEDURE_RE = re.compile(
    r"\b(?:mastopexy|breast\s+lift|lift\s+and|and\s+(?:a\s+)?lift|with\s+(?:a\s+)?lift"
    r"|augmentation[/ -]lift|lift\s+with|mommy\s+makeover|tummy\s+tuck|abdominoplasty"
    r"|liposuction|lipo\b|breast\s+reduction|reduction\s+mammo\w*|explant"
    r"|implant\s+removal|removal\s+and\s+replacement|fat\s+transfer|fat\s+graft\w*"
    r"|revision|reconstruction|gynecomastia)\b", re.I)

# 'Notes: After photos are 6 weeks post op' / '6 months post op' / '1 year post op'.
POST_OP_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(week|month|year)s?\s*(?:post[- ]?op|after\s+surgery)", re.I)
# Weeks -> months uses the mean calendar month (365.25/12/7 weeks), so '6 weeks
# post op' records 1.38 months rather than a rounded 1.5.
POST_OP_MONTHS = {"week": 7 * 12 / 365.25, "month": 1.0, "year": 12.0}


def page1_full_res(url: str) -> str:
    """The original media URL for a WordPress-resized gallery image."""
    return WP_SIZE_SUFFIX_RE.sub("", url)


def page1_list_cases(listing_html: str) -> list[str]:
    """Case slugs, in published order, from a Page 1 Solutions listing page.

    The slug comes from the case link rather than the printed patient number:
    they are not always the same value.
    """
    soup = BeautifulSoup(listing_html, "html.parser")
    slugs: list[str] = []
    for item in soup.select("div.patient-item"):
        for anchor in item.select("a[href]"):
            href = anchor.get("href", "").strip()
            if not href or href.startswith(("#", "http")):
                continue
            slug = href.strip("/").rsplit("/", 1)[-1]
            if slug and slug not in slugs:
                slugs.append(slug)
            break
    return slugs


def chart_lines(info: Tag) -> list[str]:
    """The chart paragraph's lines, cut at `<br>`.

    Cutting at `<br>` rather than at text-node boundaries is what keeps
    `<strong>Implant Size:</strong> 350cc` on one line; splitting the
    paragraph's rendered text would separate that label from its value.
    """
    para = info.find("p")
    if para is None:
        return []
    lines, current = [], []
    for node in para.children:
        if isinstance(node, Tag) and node.name == "br":
            lines.append(" ".join(current))
            current = []
            continue
        text = node.get_text(" ", strip=True) if isinstance(node, Tag) else str(node)
        if text.strip():
            current.append(text.strip())
    lines.append(" ".join(current))
    return [re.sub(r"\s+", " ", line).strip() for line in lines if line.strip()]


def _label_side(label: str) -> str | None:
    """'left'/'right' if a chart label names a side, else None."""
    words = {w for w in re.split(r"[^A-Za-z]+", label.lower()) if w}
    sides = words & SIDE_WORDS
    return sides.pop() if len(sides) == 1 else None


def is_volume_label(label: str) -> bool:
    """True for a chart label that makes the value an implant volume."""
    words = [w for w in re.split(r"[^A-Za-z]+", label.lower()) if w]
    if not words or not any(w in ("implant", "implants", "volume") for w in words):
        return False
    return all(w in VOLUME_LABEL_WORDS | SIDE_WORDS for w in words)


def _in_range(value: float) -> bool:
    return VOLUME_MIN <= value <= VOLUME_MAX


def _sided_volumes(text: str) -> tuple[float | None, float | None]:
    """(left, right) from figure-then-side text, or (None, None)."""
    left = right = None

    def assign(side: str, value: float) -> None:
        nonlocal left, right
        if not _in_range(value):
            return
        if side.lower() == "left" and left is None:
            left = value
        elif side.lower() == "right" and right is None:
            right = value

    for match in FILLED_TO_SIDE_RE.finditer(text):
        assign(match.group(2), float(match.group(1)))
    for match in VOLUME_SIDE_RE.finditer(text):
        assign(match.group(2), float(match.group(1)))
    return left, right


def _first_number(value: str, labelled: bool) -> float | None:
    """The value's implant volume: a unit-qualified figure, or - only inside a
    labelled implant-size field - a bare number."""
    match = VOLUME_WITH_UNIT_RE.search(value)
    if match is None and labelled:
        match = BARE_NUMBER_RE.search(value)
    if match is None:
        return None
    number = float(match.group(1))
    return number if _in_range(number) else None


def parse_case_volumes(chart_fields: list[tuple[str, str]], bare_lines: list[str],
                       specs: sg.CaseSpecs) -> None:
    """Set specs.left_cc/right_cc from a case's chart.

    Labelled implant-size fields win: they are the clinic's own statement, and
    a bare number inside one counts as a documented volume. Unlabelled chart
    lines are read only when they carry an explicit unit.

    A case that publishes two volumes WITHOUT naming their sides (six drbandy
    cases repeat the `Implant Size:` line, e.g. `385cc` then `405cc`) records
    the average on both sides rather than assigning an order to left and right:
    volume_cc is that average either way, and guessing which implant went in
    which breast would put an invented laterality into the pair's notes. The
    two published figures survive verbatim in the case's fields, and so in the
    notes.
    """
    sided: dict[str, float] = {}
    unsided: list[float] = []

    for label, value in chart_fields:
        if not is_volume_label(label):
            continue
        side = _label_side(label)
        line_left, line_right = _sided_volumes(value)
        if side is None and (line_left is not None or line_right is not None):
            if line_left is not None:
                sided.setdefault("left", line_left)
            if line_right is not None:
                sided.setdefault("right", line_right)
            continue
        number = _first_number(value, labelled=True)
        if number is None:
            continue
        if side is not None:
            sided.setdefault(side, number)
        else:
            unsided.append(number)

    if not sided and not unsided:
        for line in bare_lines:
            line_left, line_right = _sided_volumes(line)
            if line_left is not None:
                sided.setdefault("left", line_left)
            if line_right is not None:
                sided.setdefault("right", line_right)
            if line_left is None and line_right is None:
                # 'Fill Volume 325cc bilateral' - symmetric, and an unlabelled
                # line so the unit is what makes it a volume at all.
                number = _first_number(line, labelled=False)
                if number is not None and re.search(
                        r"\b(?:fill|volume|implants?)\b|\bbilateral\b", line, re.I):
                    unsided.append(number)

    if sided:
        specs.left_cc = sided.get("left")
        specs.right_cc = sided.get("right")
    elif unsided:
        average = sum(unsided[:2]) / len(unsided[:2])
        specs.left_cc = specs.right_cc = average


def page1_parse_case(case_html: str, case_id: str, source_url: str) -> sg.CaseData:
    """One Page 1 Solutions case page -> CaseData (positional pairs, no views)."""
    case = sg.CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")

    specs = sg.CaseSpecs()
    bare_lines: list[str] = []
    chart_fields: list[tuple[str, str]] = []
    info = soup.select_one("#patient-info")
    if info is None:
        case.warnings.append("no #patient-info block published")
        lines: list[str] = []
    else:
        lines = chart_lines(info)
        for line in lines:
            label, sep, value = line.partition(":")
            label, value = label.strip(), value.strip()
            if sep and label and value:
                chart_fields.append((label, value))
                # A repeated label is a second published value, not a
                # correction: both are kept so the notes record what the
                # clinic actually printed.
                specs.fields[label] = (f"{specs.fields[label]} / {value}"
                                       if label in specs.fields else value)
            else:
                bare_lines.append(line)
        for item in info.select("li"):
            data = item.find("span", class_="data")
            if data is None:
                continue
            value = data.get_text(" ", strip=True).strip()
            label = item.get_text(" ", strip=True)
            cut = label.rfind(value)
            label = (label[:cut] if cut > 0 else label).strip().rstrip(":").strip()
            if label and value:
                specs.fields[label] = value
        specs.summary = " ".join(lines)
        if not lines:
            case.warnings.append("no chart text published")

    parse_case_volumes(chart_fields, bare_lines, specs)

    specs.gender = specs.fields.get("Gender", "").lower()
    # 'Age: 26 - 30' is a published bucket, not a measurement: it stays in the
    # notes verbatim and never becomes a number the schema would read as this
    # patient's age.
    age = specs.fields.get("Age", "").strip()
    if age.isdigit():
        specs.age = int(age)

    post_op = POST_OP_RE.search(specs.fields.get("Notes", ""))
    if post_op:
        specs.months_post_op = round(
            float(post_op.group(1)) * POST_OP_MONTHS[post_op.group(2).lower()], 2)

    haystack = " ".join([specs.summary, *specs.fields.values()])
    sg.classify_brand_shape_profile(specs, haystack)
    # Chart text only (CLAUDE.md). Every line here IS chart text: this family
    # publishes a labelled chart (or, in three drbandy cases, the same
    # statements without their labels) and no narrative at all, so there is no
    # prose in which a placement word could be describing the options rather
    # than this patient's operation.
    sg.classify_placement_incision(specs, specs.summary)
    case.specs = specs

    for index, row in enumerate(soup.select("div.row.image-pair"), 1):
        urls = []
        for box in row.select("div.img-box"):
            img = box.find("img")
            if img is None:
                continue
            src = (img.get("data-src") or img.get("src") or "").strip()
            if not src or src.startswith("data:"):
                continue
            urls.append(page1_full_res(src))
        if len(urls) != 2:
            case.warnings.append(
                f"pair {index}: expected 2 images, found {len(urls)}; skipped")
            continue
        # Positional pair keys only. The page labels no view and the filenames
        # only count the photographs, so view (and laterality) come from the
        # annotation file via resolve_view().
        case.pairs.append(sg.ImagePair(
            key=f"pair{index}", before_url=urls[0], after_url=urls[1]))

    # Purity is screened on the case's own published text - the chart, the
    # Procedure field and the page title - never on the gallery it sits in.
    title = soup.find("title")
    screened = " ".join(filter(None, [
        specs.summary,
        specs.fields.get("Procedure", ""),
        title.get_text(" ", strip=True) if title else "",
    ]))
    combined = COMBINED_PROCEDURE_RE.search(screened)
    if combined:
        case.warnings.append(
            f"not pure breast augmentation (case text says '{combined.group(0)}'); "
            "excluded by captain ruling")
        case.pairs = []
    if not case.pairs and not case.warnings:
        case.warnings.append("no usable image pairs")
    return case


# ---------------------------------------------------------------------------
# Burnt-in watermark
# ---------------------------------------------------------------------------

# drbandy burns a large translucent "© Dr. Amy Bandy" wordmark across a
# full-width band at the BOTTOM of its 655x491 gallery exports, in two sizes
# that overlap; the crop is the union of both.
#
# Measured, not assumed. A median high-pass over the 2,199 downloaded 655x491
# images keeps the fixed overlay and cancels the anatomy, which moves: the
# wordmark's ink starts at y=401 of 491 (the rows above it carry only the
# high-pass filter's own halo) and runs to the bottom edge. 401/491 = 0.8167,
# so the crop keeps the top 81.67% of the frame - 401px of a 491px export,
# which clears ingest.py's 400px floor by 1px, and 1568px of a 1920px original.
# The band sits over the lower abdomen; no case's breast tissue reaches it.
#
# The band is cropped, never masked and never tolerated (the captain's standing
# ruling on corner and edge watermarks), and it is cropped on EVERY pair rather
# than only the ones a detector calls watermarked. Some cases publish the
# un-watermarked 2560x1920 original instead of the export (241 of 2,664 images,
# with no wordmark structure at all in the same median high-pass), and cropping
# those by the same fraction costs only field of view the frame does not use,
# where a per-image detector that scores one half wrong would either ship a
# stamped photograph or leave the watermark itself as a before/after label.
WATERMARK_CROP = (0.0, 0.0, 1.0, 0.8167)


def crop_watermark(before: bytes, after: bytes) -> tuple[bytes, bytes]:
    """Crop the burnt-in wordmark band off both halves of a pair."""
    return (sg.crop_fraction(before, WATERMARK_CROP),
            sg.crop_fraction(after, WATERMARK_CROP))
