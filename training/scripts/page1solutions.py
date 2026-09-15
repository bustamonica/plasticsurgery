#!/usr/bin/env python3
"""Page 1 Solutions patient galleries: one parser for the whole platform family.

Page 1 Solutions is a medical-marketing agency, and its WordPress "patient
gallery" is the product four consented practices run. It is ONE `kind`
(`page1solutions`) in `scrape_gallery.CLINICS`, and each clinic names the
markup it publishes in `ClinicConfig.template`, because the agency ships that
gallery in four templates that share nothing below the WordPress layer:

| template | clinic    | enumeration                                  | a case is                           | case key                    |
| -------- | --------- | -------------------------------------------- | ----------------------------------- | --------------------------- |
| `item`   | bandy     | one listing of `div.patient-item` links      | its own page, `#patient-info`       | the URL slug                |
| `entry`  | ncps      | `/page/N/` listing of `div.patient-content`  | its own page, `div.patient-entry`   | the `Case #N` in the anchor |
| `holder` | psiw      | every case inline on one listing             | a `div.patient-holder` block        | its numbered asset folder   |
| `pager`  | ciaravino | `?page=N` listing enumerated by `ul.pager`   | a `div.patient` block               | gallery tag + asset folder  |

Before this module there were four parsers under four kinds, written by four
parallel collections that could not see each other, in two modules whose names
differed by one underscore plus two sections of `scrape_gallery.py`. Two of
them were once registered under the same kind, and because `collect_cases`
returns from the first matching branch, ncps was enumerated by psiw's parser
and reported a clean zero-case run. One kind with a validated `template` makes
that collision impossible: an unknown template refuses the run.

What the four templates share lives at the top of this module: the WordPress
size-suffix strip (`wp_original`) and the dispatch. Everything else is
per-template, and deliberately so. Each template's volume reader, purity
screen and chart parser encodes what its own clinic's text was measured to
need - ncps's screen skips bare `removal` (a prior tumour removal is not an
explant) where psiw's counts it, ncps's and psiw's narrative readers disagree
on side order - so unifying them would change which cases and which volumes
reach the corpus. Each template's names carry its prefix (`item_`, `entry_`,
`holder_`, `pager_`) so the four read side by side without colliding. Views
are documented by none of the four templates: every view label comes from the
visual-annotation file, and laterality is never inferred from position.

A new Page 1 Solutions practice is a new `ClinicConfig` with `kind` set to
`page1solutions` and the `template` its markup matches. Check the listing
block and the case markup against the table before assuming - a fifth template
is a new section here, never a new kind.

Template 'item' (bandy)
-----------------------
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

Volumes therefore run through `item_parse_volumes()`, which reads the side off
the label where there is one, and otherwise pairs each figure with the side
word in its own line - drbandy writes the volume BEFORE the side (`500cc left &
575cc right`), which is the opposite of the order `scrape_gallery`'s shared
narrative parser assumes, and reading it that way swaps the two.

- **The URL slug is the case key, not the published patient number.** They
  usually match and sometimes do not: drbandy `/554/` publishes
  `Patient #: 5540`, and one case is published under WordPress's placeholder
  permalink `/auto-draft/` (`Patient #: 4010`). Slugs are taken verbatim, so a
  non-numeric one is collected rather than silently dropped.
- **The image filename's patient index is not the case key either.** Case 5325
  publishes `Breast-Augmentation-Patient-17-Before-_1.jpg`; that 17 is an index
  within one upload batch and repeats across batches.
- Pairs are positional and carry **no view label**: the alt text is one generic
  string on all ten images and the filename only counts them.
- Purity is screened on the case's own published **text**, never on a slug or
  on the gallery it is filed under: see `ITEM_COMBINED_RE`.
- drbandy burns a wordmark into the bottom band of its exports; it is cropped
  by the clinic's `crop` in `scrape_gallery.CLINICS`, like every clinic's.

Template 'entry' (ncps)
-----------------------
Listing (`/<gallery-root>/<procedure>/` and `.../page/N/`):

* One `div.patient-content` per case, each opening with an anchor whose text
  is `Case #<number> - <Procedure>` and whose href is the case page.
* Pagination is WordPress-style `/page/N/`; page N+1 past the end is a plain
  404. **The platform publishes no case total**, so the count reconciles
  against pages-until-404 rather than against a declared figure.

Case page:

* `div.patient-entry` holds a `div.single-content` chart followed by the
  images. The chart is `<p>` blocks of `Label: value` lines separated by
  `<br/>`; values often carry a trailing period, and label spelling varies
  on the same site (`Implant Profile:` and bare `Profile:`,
  `Implant Shape:` and bare `Shape:`).
* Images are single views - no composites, no grids - in
  `div.patient-single`, each carrying a sibling `<span>` reading exactly
  `Before` or `After`. Pairs are formed positionally from that
  before-then-after order, exactly as the influx/BRAG galleries are.
* Every `src` is a WordPress `-300x300` derivative; `wp_original()` strips the
  suffix for the bare original.

Two things here are NOT guessable:

1. **The case key is the `Case #<number>` in the anchor text, not the URL
   slug.** 12 of ncps's 370 cases publish a word slug
   (`ideal-breast-implant-2`, `gummy-bear-implants-4`, and one whose slug
   is the bare procedure name), and two numeric slugs disagree with their own
   case number (slug `8901` is Case #12688). The case numbers are unique
   across the gallery; the slugs are not a stable key.
2. **The purity screen is `Procedure Type:` in the chart plus the narrative,
   never the slug.** ncps publishes `gummy-bear-implants-4` and
   `silicone-breast-augmentation` as slugs on cases whose chart says plain
   Breast Augmentation, and publishes `Revision Breast Augmentation` under a
   numeric slug that looks like every other case.

Templates 'holder' (psiw) and 'pager' (ciaravino)
-------------------------------------------------
Their markup contracts open their own sections below.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

# scrape_gallery imports this module from inside collect_cases (a function-level
# import), so importing it here at module level is not circular: by the time
# anything calls into this parser, scrape_gallery is fully loaded.
import scrape_gallery as sg
from scrape_gallery import (
    VOLUME_UNIT,
    CaseData,
    CaseSpecs,
    ImagePair,
    _br_lines,
    _label_regex,
    _parse_fill_side,
    _split_labelled_line,
    captain_profile_term,
    classify_brand_shape_profile,
    classify_placement_incision,
    combined_procedure_term,
    height_to_cm,
    labelled_volume,
    parse_fill_volumes,
    pounds_to_kg,
)

# WordPress resize suffix on a media URL: 'name-420x315.jpg' -> 'name.jpg',
# '...-1of10-300x300.jpg' -> '...-1of10.jpg'. The item and entry templates
# both serve derivatives; holder and pager reference their originals directly.
WP_SIZE_SUFFIX_RE = re.compile(r"-\d{2,4}x\d{2,4}(?=\.[A-Za-z]{3,4}$)")


def wp_original(url: str) -> str:
    """The original media URL for a WordPress-resized gallery image."""
    return WP_SIZE_SUFFIX_RE.sub("", url)


def collect_cases(cfg, fetcher) -> list[CaseData]:
    """Every case of one Page 1 Solutions clinic, through its own template."""
    collect = TEMPLATES.get(cfg.template)
    if collect is None:
        raise ValueError(
            f"{cfg.slug}: Page 1 Solutions template {cfg.template!r} is not one of "
            f"{sorted(TEMPLATES)}; name the markup this clinic publishes in its "
            "ClinicConfig.template (see page1solutions.py)")
    return collect(cfg, fetcher)


# ---------------------------------------------------------------------------
# Template 'item' (bandy): div.patient-item listing -> one page per case
# ---------------------------------------------------------------------------

# Words a chart label for an implant volume is built from, in any order:
# 'Implant Size', 'Implant Size Right', 'Right Implant', 'Implant Left',
# 'Left Implant Size'. 'Implant Type'/'Implant Types' carry a word outside this
# set and so are never read as a volume.
ITEM_VOLUME_LABEL_WORDS = {"implant", "implants", "size", "fill", "volume"}
ITEM_SIDE_WORDS = {"left", "right"}

ITEM_VOLUME_UNIT = r"(?:cc|ml)s?\b"
# A volume that names its own side, with the figure FIRST: '465cc Left',
# '500cc left & 575cc right', '750cc on the right'. The gap is bounded and
# stops at a separator so one line's figure cannot reach the next line's side.
ITEM_VOLUME_SIDE_RE = re.compile(
    rf"(\d{{2,4}}(?:\.\d+)?)\s*{ITEM_VOLUME_UNIT}[^.;&\n]{{0,12}}?\b(left|right)\b", re.I)
# 'filled to 675 on left' - the final fill volume, which is the implant's real
# size and overrides the shell size quoted beside it ('650cc bags filled to
# 675 on left and 750cc on right', drbandy 5337).
ITEM_FILLED_TO_SIDE_RE = re.compile(
    rf"filled\s+to\s*(\d{{2,4}}(?:\.\d+)?)\s*(?:{ITEM_VOLUME_UNIT})?"
    r"[^.;&\n]{0,15}?\bon\s+(?:the\s+|her\s+)?(left|right)\b", re.I)
# A figure with an explicit unit, anywhere. Used for the symmetric case
# ('325cc bilateral') and for unlabelled lines, where a bare number is NOT a
# documented volume (captain's rule) so the unit is required.
ITEM_VOLUME_WITH_UNIT_RE = re.compile(rf"(\d{{2,4}}(?:\.\d+)?)\s*{ITEM_VOLUME_UNIT}", re.I)
# A bare number. Only ever read inside a LABELLED implant-size field, where the
# label is what makes it a volume.
ITEM_BARE_NUMBER_RE = re.compile(r"\b(\d{3,4})\b")

# Schema bounds (dataset_schema.json volume_cc).
ITEM_VOLUME_MIN, ITEM_VOLUME_MAX = 100, 1000

# Combined-procedure vocabulary, screened against the case's published text. A
# breast-augmentation gallery still publishes combined cases; the captain's
# ruling excludes every one of them, and the Etna clinics proved that trusting
# the gallery a case is filed under (or its filename slug) lets them through.
# 'lift' is matched as a whole word inside a named procedure so a stray
# 'lifted' cannot trip it, and only procedures a practice actually names are
# listed - nothing here guesses at what a sentence might imply.
ITEM_COMBINED_RE = re.compile(
    r"\b(?:mastopexy|breast\s+lift|lift\s+and|and\s+(?:a\s+)?lift|with\s+(?:a\s+)?lift"
    r"|augmentation[/ -]lift|lift\s+with|mommy\s+makeover|tummy\s+tuck|abdominoplasty"
    r"|liposuction|lipo\b|breast\s+reduction|reduction\s+mammo\w*|explant"
    r"|implant\s+removal|removal\s+and\s+replacement|fat\s+transfer|fat\s+graft\w*"
    r"|revision|reconstruction|gynecomastia)\b", re.I)

# 'Notes: After photos are 6 weeks post op' / '6 months post op' / '1 year post op'.
ITEM_POST_OP_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(week|month|year)s?\s*(?:post[- ]?op|after\s+surgery)", re.I)
# Weeks -> months uses the mean calendar month (365.25/12/7 weeks), so '6 weeks
# post op' records 1.38 months rather than a rounded 1.5.
ITEM_POST_OP_MONTHS = {"week": 7 * 12 / 365.25, "month": 1.0, "year": 12.0}


def item_list_cases(listing_html: str) -> list[str]:
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


def item_chart_lines(info: Tag) -> list[str]:
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


def _item_label_side(label: str) -> str | None:
    """'left'/'right' if a chart label names a side, else None."""
    words = {w for w in re.split(r"[^A-Za-z]+", label.lower()) if w}
    sides = words & ITEM_SIDE_WORDS
    return sides.pop() if len(sides) == 1 else None


def item_is_volume_label(label: str) -> bool:
    """True for a chart label that makes the value an implant volume."""
    words = [w for w in re.split(r"[^A-Za-z]+", label.lower()) if w]
    if not words or not any(w in ("implant", "implants", "volume") for w in words):
        return False
    return all(w in ITEM_VOLUME_LABEL_WORDS | ITEM_SIDE_WORDS for w in words)


def _item_in_range(value: float) -> bool:
    return ITEM_VOLUME_MIN <= value <= ITEM_VOLUME_MAX


def _item_sided_volumes(text: str) -> tuple[float | None, float | None]:
    """(left, right) from figure-then-side text, or (None, None)."""
    left = right = None

    def assign(side: str, value: float) -> None:
        nonlocal left, right
        if not _item_in_range(value):
            return
        if side.lower() == "left" and left is None:
            left = value
        elif side.lower() == "right" and right is None:
            right = value

    for match in ITEM_FILLED_TO_SIDE_RE.finditer(text):
        assign(match.group(2), float(match.group(1)))
    for match in ITEM_VOLUME_SIDE_RE.finditer(text):
        assign(match.group(2), float(match.group(1)))
    return left, right


def _item_first_number(value: str, labelled: bool) -> float | None:
    """The value's implant volume: a unit-qualified figure, or - only inside a
    labelled implant-size field - a bare number."""
    match = ITEM_VOLUME_WITH_UNIT_RE.search(value)
    if match is None and labelled:
        match = ITEM_BARE_NUMBER_RE.search(value)
    if match is None:
        return None
    number = float(match.group(1))
    return number if _item_in_range(number) else None


def item_parse_volumes(chart_fields: list[tuple[str, str]], bare_lines: list[str],
                       specs: CaseSpecs) -> None:
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
        if not item_is_volume_label(label):
            continue
        side = _item_label_side(label)
        line_left, line_right = _item_sided_volumes(value)
        if side is None and (line_left is not None or line_right is not None):
            if line_left is not None:
                sided.setdefault("left", line_left)
            if line_right is not None:
                sided.setdefault("right", line_right)
            continue
        number = _item_first_number(value, labelled=True)
        if number is None:
            continue
        if side is not None:
            sided.setdefault(side, number)
        else:
            unsided.append(number)

    if not sided and not unsided:
        for line in bare_lines:
            line_left, line_right = _item_sided_volumes(line)
            if line_left is not None:
                sided.setdefault("left", line_left)
            if line_right is not None:
                sided.setdefault("right", line_right)
            if line_left is None and line_right is None:
                # 'Fill Volume 325cc bilateral' - symmetric, and an unlabelled
                # line so the unit is what makes it a volume at all.
                number = _item_first_number(line, labelled=False)
                if number is not None and re.search(
                        r"\b(?:fill|volume|implants?)\b|\bbilateral\b", line, re.I):
                    unsided.append(number)

    if sided:
        specs.left_cc = sided.get("left")
        specs.right_cc = sided.get("right")
    elif unsided:
        average = sum(unsided[:2]) / len(unsided[:2])
        specs.left_cc = specs.right_cc = average


def item_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    """One Page 1 Solutions case page -> CaseData (positional pairs, no views)."""
    case = CaseData(case_id=case_id, source_url=source_url)
    soup = BeautifulSoup(case_html, "html.parser")

    specs = CaseSpecs()
    bare_lines: list[str] = []
    chart_fields: list[tuple[str, str]] = []
    info = soup.select_one("#patient-info")
    if info is None:
        case.warnings.append("no #patient-info block published")
        lines: list[str] = []
    else:
        lines = item_chart_lines(info)
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

    item_parse_volumes(chart_fields, bare_lines, specs)

    specs.gender = specs.fields.get("Gender", "").lower()
    # 'Age: 26 - 30' is a published bucket, not a measurement: it stays in the
    # notes verbatim and never becomes a number the schema would read as this
    # patient's age.
    age = specs.fields.get("Age", "").strip()
    if age.isdigit():
        specs.age = int(age)

    post_op = ITEM_POST_OP_RE.search(specs.fields.get("Notes", ""))
    if post_op:
        specs.months_post_op = round(
            float(post_op.group(1)) * ITEM_POST_OP_MONTHS[post_op.group(2).lower()], 2)

    haystack = " ".join([specs.summary, *specs.fields.values()])
    classify_brand_shape_profile(specs, haystack)
    # Chart text only (CLAUDE.md). Every line here IS chart text: this family
    # publishes a labelled chart (or, in three drbandy cases, the same
    # statements without their labels) and no narrative at all, so there is no
    # prose in which a placement word could be describing the options rather
    # than this patient's operation.
    classify_placement_incision(specs, specs.summary)
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
            urls.append(wp_original(src))
        if len(urls) != 2:
            case.warnings.append(
                f"pair {index}: expected 2 images, found {len(urls)}; skipped")
            continue
        # Positional pair keys only. The page labels no view and the filenames
        # only count the photographs, so view (and laterality) come from the
        # annotation file via resolve_view().
        case.pairs.append(ImagePair(
            key=f"pair{index}", before_url=urls[0], after_url=urls[1]))

    # Purity is screened on the case's own published text - the chart, the
    # Procedure field and the page title - never on the gallery it sits in.
    title = soup.find("title")
    screened = " ".join(filter(None, [
        specs.summary,
        specs.fields.get("Procedure", ""),
        title.get_text(" ", strip=True) if title else "",
    ]))
    combined = ITEM_COMBINED_RE.search(screened)
    if combined:
        case.warnings.append(
            f"not pure breast augmentation (case text says '{combined.group(0)}'); "
            "excluded by captain ruling")
        case.pairs = []
    if not case.pairs and not case.warnings:
        case.warnings.append("no usable image pairs")
    return case


# ---------------------------------------------------------------------------
# Template 'entry' (ncps): paged div.patient-content listing -> div.patient-entry pages
# ---------------------------------------------------------------------------

# Chart labels this platform publishes. Read as a set of alternatives rather
# than 'split at the first colon' because the same site prints two spellings
# for the same field (`Implant Profile:` / `Profile:`), and because a value can
# itself contain a colon-free parenthetical that a naive split would keep.
# Longest first so `Implant Profile` wins over `Profile`.
ENTRY_FIELD_LABELS = (
    "Gender", "Age", "Height", "Weight", "Months Post-Op", "Procedure Type",
    "Implant Placement", "Incision Site", "Pre-Op Cup Size", "Post-Op Cup Size",
    "Implant Type", "Implant Profile", "Profile", "Implant Shape", "Shape",
    "Implant Surface", "Left Implant Size", "Right Implant Size",
)
ENTRY_LABEL_RE = re.compile(
    r"\b(" + "|".join(re.escape(label) for label in
                      sorted(ENTRY_FIELD_LABELS, key=len, reverse=True))
    + r")\s*:\s*", re.I)

# The chart's narrative heading. Everything under it is the surgeon's prose and
# must not reach classify_placement_incision() - see that function's docstring.
ENTRY_NARRATIVE_HEADINGS = ("doctor's comments", "doctor’s comments")

# Feet/inches on this platform are typed with PRIME and DOUBLE PRIME (5′ 6″),
# which the shared FEET_INCHES_RE does not accept. Normalised here rather than
# by widening that regex, so no other clinic's parsing changes.
ENTRY_PRIMES = {"′": "'", "″": '"', "’": "'", "”": '"'}

# Procedure vocabulary for the purity screen. A case passes only when its
# documented procedure is a primary breast augmentation with implants.
#
# 'Revision' is rejected on its own class, not lumped in with the combined
# procedures: a revision case's BEFORE photo already carries implants, so the
# published volume does not describe the same before->after transition the rest
# of the corpus teaches. That is a data-quality call rather than the captain's
# combined-procedure ruling, and it is reversible - the cases stay in the fetch
# cache and re-admitting them is one entry in this tuple.
#
# 'removal' is deliberately NOT here as a bare word. It reads as an implant
# explant only about half the time in this prose: ncps case 9756 is a pure
# augmentation whose narrative explains that "a previous tumor removal in the
# left breast resulted in volume asymmetry", which is a prior unrelated
# operation, not part of this one. The implant-exchange cases it would have
# caught are already caught by 'capsulectomy', 'explant' and the revision
# terms, so dropping it costs nothing and buys back a clean case.
ENTRY_COMBINED_TERMS = (
    "lift", "mastopexy", "mommy makeover", "tummy tuck", "abdominoplasty",
    "reduction", "reconstruction", "liposuction", "explant",
    "exchange", "capsulectomy", "gynecomastia", "fat transfer", "fat grafting",
)
ENTRY_REVISION_TERMS = ("revision", "re-augmentation", "replacement")

# A narrative that NAMES a procedure has not necessarily reported one: this
# surgeon's prose routinely explains what the patient declined or what was
# merely discussed ("she did not want to have breast lift ... and opted for a
# breast augmentation alone", "different options ... were discussed including
# reduction of the larger breast"). Screening the narrative without this costs
# four pure ncps cases outright, and it is the same trap CLAUDE.md records for
# marina's dual-plane-or-subglandular paragraph. The chart's own
# 'Procedure Type' field is never negated, so this applies to prose only.
ENTRY_NEGATION_MARKERS = (
    "did not", "didn't", "does not", "doesn't", "without", "no need",
    "declined", "instead of", "rather than", "avoid", "opted for",
    "options", "discussed", "considered", "chose not", "would have",
    "cannot", "can't", "unwilling", "refused", "not want", "alone",
)
# Sentence boundary for that negation scope. Kept simple on purpose: the prose
# here is plain clinical narrative with no abbreviations that end in a period.
ENTRY_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


# A labelled 'Implant Profile:' field whose value is the bare projection word
# ('High', 'Moderate') documents a profile as surely as 'High Profile' does -
# the same reasoning the captain applied to a bare number inside a labelled
# implant-size field. Only exact whole-value matches decode, so 'Moderate High'
# (six ncps cases, and not a term in the captain's 2026-08-19 mapping) and
# 'Classic Profile' stay undocumented rather than being guessed into an enum.
# 'Full' is high and 'Extra Full' extra-high (captain's ruling of 2026-08-26,
# see scrape_gallery.FULL_PROJECTION_RE).
ENTRY_BARE_PROFILES = {
    "moderate": "moderate",
    "moderate plus": "moderate-plus",
    "moderate+": "moderate-plus",
    "high": "high",
    "full": "high",
    "extra high": "extra-high",
    "ultra high": "extra-high",
    "extra full": "extra-high",
}
# 'Implant Profile: Moderate+ Left, High Profile Right' - one case, two
# profiles. The schema records one, so a sided field is left undocumented
# rather than resolved to whichever side the regex happened to reach first.
ENTRY_SIDED_PROFILE_RE = re.compile(r"\b(left|right)\b", re.I)


def entry_profile_field(fields: dict[str, str]) -> str:
    """The case's labelled profile value, under either spelling the site uses."""
    return fields.get("Implant Profile", "") or fields.get("Profile", "")


def entry_profile(fields: dict[str, str]) -> tuple[str, bool]:
    """(profile text to classify, is_sided) for a case's labelled profile field.

    An empty string means there is nothing to classify; `is_sided` marks the
    field as documenting a different profile per breast, which the caller must
    leave undocumented.
    """
    value = entry_profile_field(fields)
    if not value:
        return "", False
    if ENTRY_SIDED_PROFILE_RE.search(value):
        return value, True
    return value, False


def entry_bare_profile(value: str) -> str | None:
    """Schema profile for a bare labelled value ('High'), else None."""
    return ENTRY_BARE_PROFILES.get(value.strip().lower().rstrip("."))


def entry_normalise_primes(text: str) -> str:
    """Typographic primes/quotes to ASCII, so heights parse as 5' 6\"."""
    for prime, ascii_char in ENTRY_PRIMES.items():
        text = text.replace(prime, ascii_char)
    return text


def entry_list_cases(listing_html: str) -> list[tuple[str, str]]:
    """[(case_number, case_url)] for one listing page, in document order.

    The case number comes from the anchor text ('Case #9325 - Breast
    Augmentation'); a block whose anchor does not carry one is skipped rather
    than keyed on its slug, because the slug is not a stable identifier here.
    """
    soup = BeautifulSoup(listing_html, "html.parser")
    cases: list[tuple[str, str]] = []
    for block in soup.select("div.patient-content"):
        anchor = block.find("a", href=True)
        if anchor is None:
            continue
        m = re.search(r"Case\s*#\s*(\d+)", anchor.get_text(" ", strip=True))
        if m is None:
            continue
        cases.append((m.group(1), anchor["href"]))
    return cases


def entry_chart_lines(content) -> list[str]:
    """One text line per chart line of a case's div.single-content.

    The chart is <p> blocks whose fields are separated by <br/>, so the text is
    taken with '\\n' as the separator and split on it. Empty lines and the
    bold section headings ('Patient', 'Breast Augmentation Surgery') come
    through as their own lines and are kept: the narrative heading is what
    tells the caller where the chart stops and prose begins.
    """
    lines: list[str] = []
    for block in content.find_all("p"):
        if block.find("p") is not None:
            continue
        for line in block.get_text("\n", strip=True).split("\n"):
            line = line.strip()
            if line:
                lines.append(line)
    return lines


def entry_split_chart(lines: list[str]) -> tuple[dict[str, str], str]:
    """(chart fields, narrative) for a case.

    Everything before the "Doctor's Comments" heading is chart; everything
    after it is the surgeon's narrative. Fields are recovered by scanning each
    line for the known labels, so a line that carries several (the listing
    excerpt prints the whole chart on one line) yields all of them.
    """
    fields: dict[str, str] = {}
    narrative_lines: list[str] = []
    in_narrative = False
    pending_label: str | None = None
    for line in lines:
        if line.strip().lower().rstrip(":") in ENTRY_NARRATIVE_HEADINGS:
            in_narrative = True
            pending_label = None
            continue
        if in_narrative:
            narrative_lines.append(line)
            continue
        matches = list(ENTRY_LABEL_RE.finditer(line))
        if not matches:
            # A label whose value the template pushed onto the next line
            # ('Procedure Type:' then 'Gummy Bear Breast Augmentation.').
            if pending_label is not None:
                fields[pending_label] = _entry_clean_value(line)
                pending_label = None
            continue
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            label = match.group(1).strip()
            value = _entry_clean_value(line[match.end():end])
            if value:
                fields[label] = value
                pending_label = None
            else:
                pending_label = label
    return fields, " ".join(narrative_lines)


def _entry_clean_value(value: str) -> str:
    """Trim the trailing period this platform prints after every chart value."""
    return entry_normalise_primes(value).replace("\xa0", " ").strip().rstrip(".").strip()


def _entry_first_term(text: str) -> str | None:
    """The first impure-procedure term in `text`, prefixed by its class."""
    for term in ENTRY_COMBINED_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", text):
            return f"combined-procedure:{term}"
    for term in ENTRY_REVISION_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", text):
            return f"revision:{term}"
    return None


def entry_purity(fields: dict[str, str], narrative: str) -> str | None:
    """None when the case is a pure primary augmentation, else its rejection class.

    Two screens, and neither alone is sufficient. The chart's own
    'Procedure Type' is the clinic's statement of what was booked and is taken
    at face value. The narrative is where a second procedure that never reached
    the chart shows up ("she underwent removal of her old implants and
    capsulectomy"), so it is screened too - but only sentence by sentence, and
    a sentence carrying a negation or hypothetical marker is not a report of a
    procedure. The URL slug is never read; see this module's docstring.
    """
    chart = _entry_first_term(fields.get("Procedure Type", "").lower())
    if chart is not None:
        return f"chart/{chart}"
    for sentence in ENTRY_SENTENCE_RE.split(narrative.lower()):
        if any(marker in sentence for marker in ENTRY_NEGATION_MARKERS):
            continue
        found = _entry_first_term(sentence)
        if found is not None:
            return f"narrative/{found}"
    return None


def entry_pair_urls(entry) -> list[tuple[str, str]]:
    """[(before_url, after_url)] from a case's div.patient-single sequence.

    The only page-published labels are the sibling <span>Before</span> /
    <span>After</span>; views are not documented at all. Pairs are formed
    positionally from a Before immediately followed by an After, and both URLs
    are lifted to the bare WordPress original.
    """
    tagged: list[tuple[str, str]] = []
    for single in entry.select("div.patient-single"):
        img = single.find("img")
        span = single.find("span")
        if img is None or span is None:
            continue
        src = img.get("src") or img.get("data-lazyload-src") or img.get("data-src") or ""
        phase = span.get_text(strip=True).lower()
        if src and phase in ("before", "after"):
            tagged.append((phase, wp_original(src)))
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(tagged) - 1:
        if tagged[i][0] == "before" and tagged[i + 1][0] == "after":
            pairs.append((tagged[i][1], tagged[i + 1][1]))
            i += 2
        else:
            i += 1
    return pairs


# The listing walk's ceiling. Page N past the end is a plain 404 on this
# platform, so the walk normally ends on its own - but a WordPress gallery that
# 200s past the end with the last page's cases re-rendered (sculpted does
# exactly that) would otherwise loop forever at one fetch per delay interval,
# because the `seen` set dedupes the repeats away while the page keeps parsing
# non-empty. ncps, the largest clinic on this platform, publishes 370 cases
# across 37 pages.
ENTRY_MAX_PAGES = 200


def entry_parse_case(case_html: str, case_id: str, source_url: str) -> CaseData:
    """One Page 1 Solutions case: labelled chart + positional Before/After singles.

    The markup contract is the module docstring's 'entry' section and the
    case-key rule and purity screen are above; this turns them into CaseData. A case
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
    fields, narrative = entry_split_chart(
        entry_chart_lines(content) if content is not None else [])

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
    specs.left_cc = _entry_labelled_cc(fields.get("Left Implant Size", ""))
    specs.right_cc = _entry_labelled_cc(fields.get("Right Implant Size", ""))
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
    profile_text, profile_is_sided = entry_profile(fields)
    classify_brand_shape_profile(specs, " ".join([
        fields.get("Implant Type", ""),
        fields.get("Implant Shape", "") or fields.get("Shape", ""),
        "" if profile_is_sided else profile_text,
    ]))
    if specs.profile is None and not profile_is_sided:
        specs.profile = entry_bare_profile(profile_text)
    chart_text = " ".join(fields.values())
    classify_placement_incision(specs, chart_text)
    case.specs = specs

    rejection = entry_purity(fields, narrative)
    if rejection is not None:
        case.warnings.append(f"not pure augmentation ({rejection})")
        return case
    for i, (before_url, after_url) in enumerate(entry_pair_urls(entry), 1):
        case.pairs.append(ImagePair(key=f"pair{i}", before_url=before_url,
                                    after_url=after_url))
    if not case.pairs:
        case.warnings.append("no usable image pairs")
    return case


def _entry_labelled_cc(value: str) -> float | None:
    """Volume from a labelled implant-size field ('410 cc', '215cc', '410')."""
    m = re.match(r"(\d+(?:\.\d+)?)", value.strip())
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Template 'holder' (psiw): every case inline on one listing, div.patient-holder
# ---------------------------------------------------------------------------
#
# Markup contract, one `div.patient-holder` per case:
#
#   div.patient-holder
#     div.patient.gallery-preview        <- listing thumbnail (before, after)
#     div.gallery-wrap.hide id="<n>"     <- the case, hidden until clicked
#       div.slides > div.item            <- ONE PAIR each: img[0]=before, img[1]=after
#       div.details > p ...              <- the clinic's caption/spec block
#
# Images are referenced by a **relative** path (`./07/03.jpg`) in
# `data-lazyload-src`; the folder is the case's own directory under the gallery
# URL and is the case key. A naive `<img src>` scrape reads zero images here,
# because nothing carries a real `src` until the lazyloader runs.
#
# The image number is positional and documents nothing: within a `.slides
# .item` the FIRST img is the before and the SECOND is the after, which the
# page states independently on its first pair via the `data-before` /
# `data-after` attributes in `.view.s3grid`. It says nothing about the VIEW, so
# every view label here comes from visual annotation.
#
# The clinic's own "Case # NNN" label is NOT unique - plasticsurgerynow
# publishes `Case # KH006` on two different patients and `Case # 1102` on two
# more - so the folder number is the case key and the Case # is recorded as a
# spec field only.

HOLDER_LABELS = [
    "Patient Age", "Age", "Height", "Ht", "Weight", "Wt",
    "Implant Size (Left)", "Implant Size (Right)", "Implant size", "Implant Size",
    "Implant Type", "Implant", "Incision Type", "Incision",
    "Placement", "Left", "Right", "Cup Size", "Size", "Before", "Post", "After",
    "Description", "Procedures", "Details", "Time after surgery",
]
# Longest label first so 'Implant Size (Left)' is never read as bare 'Implant'.
HOLDER_LABEL_RE = re.compile(
    r"\b(" + "|".join(re.escape(lbl) for lbl in
                      sorted(HOLDER_LABELS, key=len, reverse=True)) + r")\s*:",
    re.I)
# Labels whose own text names the value an implant size, so a bare number in
# them is a volume (the standing units ruling). Bare 'Left'/'Right' qualify
# only after an 'Implant size' label has opened a sided sub-block - see
# holder_parse_details.
HOLDER_SIDED_VOLUME_LABELS = {
    "implant size (left)": "left", "implant size (right)": "right",
    "left": "left", "right": "right",
}
HOLDER_VOLUME_LABELS = {"implant size", "implant size (left)",
                       "implant size (right)"}
HOLDER_NARRATIVE_LABELS = {"description", "procedures", "details",
                          "time after surgery"}
HOLDER_BARE_VOLUME_RE = re.compile(r"^(\d{2,4}(?:\.\d+)?)\s*(?:ccs?|ml)?\b", re.I)

# A volume followed straight away by its side, with nothing between them:
# '405 cc left and 360 cc right', '250cc right, 225cc left'. Kept separate from
# the shared TRAILING_SIDE_RE, which reads the 'on the right' phrasing and
# would take '405 cc left' as a bilateral figure.
HOLDER_POSTFIX_SIDE_RE = re.compile(
    rf"(\d+(?:\.\d+)?)\s*{VOLUME_UNIT}\s*[,;]?\s+(left|right)\b", re.I)
# The mirror phrasing: 'right side 405 cc and left side 375 cc'.
HOLDER_SIDE_PREFIX_RE = re.compile(
    rf"\b(left|right)\s+side\s+(\d+(?:\.\d+)?)\s*{VOLUME_UNIT}", re.I)

# Profile abbreviations, per the captain's 2026-08-19 ruling (UHP -> extra-high,
# HP -> high, MP -> moderate). MPP is this family's spelling of 'Moderate
# Profile Plus': plasticsurgerynow publishes both forms for the same product
# ('275 cc MPP gel' in case 55, '275cc Moderate Profile Plus Gels' in case 83).
# Matched case-sensitively as whole words so ordinary prose cannot trip them,
# and only after the spelled-out PROFILE_PATTERNS have had their turn.
HOLDER_PROFILE_ABBREVIATIONS = [
    (re.compile(r"\bUHP\b"), "extra-high"),
    (re.compile(r"\bMPP\b"), "moderate-plus"),
    (re.compile(r"\bHP\b"), "high"),
    (re.compile(r"\bMP\b"), "moderate"),
]

# Procedures that make a case something other than a pure augmentation. The
# screen reads the case TEXT, never the gallery slug or the folder name: this
# gallery is titled 'Augmentation' and still publishes a mastopexy, a tummy
# tuck, a liposuction, a nipple reduction and a congenital-deformity
# reconstruction inside it.
HOLDER_COMBINED_PATTERNS = [
    (re.compile(r"\bmastopex\w*\b", re.I), "mastopexy (augmentation with lift)"),
    (re.compile(r"\bbreast lift\b", re.I), "breast lift"),
    (re.compile(r"\btummy tuck\b|\babdominoplast\w*\b", re.I), "abdominoplasty"),
    (re.compile(r"\bliposuction\b|\blipoaspirate\b", re.I), "liposuction"),
    (re.compile(r"\bnipple reduction\b", re.I), "nipple reduction"),
    (re.compile(r"\bbreast reduction\b", re.I), "breast reduction"),
    (re.compile(r"\b(?:implant )?removal\b|\bexplant\w*\b", re.I), "implant removal"),
    (re.compile(r"\brevision\b", re.I), "revision"),
    (re.compile(r"\breconstruct\w*\b", re.I), "reconstruction"),
    (re.compile(r"\bcongenital\b", re.I), "congenital deformity correction"),
]


def holder_combined_procedure(text: str) -> str | None:
    """The non-augmentation procedure a case documents, or None if pure."""
    for pattern, label in HOLDER_COMBINED_PATTERNS:
        if pattern.search(text):
            return label
    return None


def _holder_split_fields(text: str) -> list[tuple[str, str]]:
    """(label, value) for every labelled field in a details block.

    Each known label ends the previous field's value, so the run-on chart
    'Implant Size (Left): 275 cc Implant Size (Right): 275 cc' yields both
    sides. Splitting at the first ': ' instead would swallow the second.
    """
    matches = list(HOLDER_LABEL_RE.finditer(text))
    fields = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fields.append((match.group(1).strip(), text[match.end():end].strip()))
    return fields


def _holder_labelled_volume(value: str) -> float | None:
    """cc figure from a field whose label already named it an implant size."""
    sided = _parse_fill_side(value)
    if sided is not None and 100 <= sided <= 1000:
        return sided
    m = HOLDER_BARE_VOLUME_RE.match(value.strip())
    if m:
        cc = float(m.group(1))
        return cc if 100 <= cc <= 1000 else None
    return None


def holder_parse_volumes(text: str) -> tuple[float | None, float | None]:
    """(left_cc, right_cc) from a details block, labelled chart or narrative.

    Tried in order: the labelled sided chart fields, then this family's two
    narrative side phrasings, then the shared narrative reader. The first two
    exist because the shared reader mis-read both layouts when this parser was
    written: its prefix branch consumed the rest of the line as one segment, so
    'Implant Size (Left): 350 cc Implant Size (Right): 325 cc' lost the right
    side and reported the left figure as the average, and '405 cc left and 360
    cc right' assigned 360 to the left. The chart half was fixed centrally on
    2026-08-25 and the shared reader handles it now; the narrative half is
    still live. Sixteen of this clinic's 104 cases publish asymmetric volumes,
    so either misread changes the caption's cc.
    """
    left = right = None
    sided_block_open = False
    for label, value in _holder_split_fields(text):
        key = label.lower()
        if key in HOLDER_VOLUME_LABELS:
            sided_block_open = True
        elif key not in HOLDER_SIDED_VOLUME_LABELS:
            # 'Cup Size: Before: 34 A  Post: 32 D' closes the sided block, so a
            # later bare 'Left'/'Right' is not read as an implant size.
            sided_block_open = False
        side = HOLDER_SIDED_VOLUME_LABELS.get(key)
        if side is None:
            continue
        if key in ("left", "right") and not sided_block_open:
            continue
        cc = _holder_labelled_volume(value)
        if cc is None:
            continue
        if side == "left":
            left = cc
        else:
            right = cc
    if left is not None or right is not None:
        return left, right

    for m in HOLDER_POSTFIX_SIDE_RE.finditer(text):
        cc = float(m.group(1))
        if 100 <= cc <= 1000:
            if m.group(2).lower() == "left":
                left = cc
            else:
                right = cc
    for m in HOLDER_SIDE_PREFIX_RE.finditer(text):
        cc = float(m.group(2))
        if 100 <= cc <= 1000:
            if m.group(1).lower() == "left":
                left = cc
            else:
                right = cc
    if left is not None or right is not None:
        return left, right
    return parse_fill_volumes(text)


def holder_parse_details(text: str, specs: CaseSpecs) -> None:
    """Fill specs from one case's details block.

    Three layouts are published on plasticsurgerynow alone and all three are
    the same block of text with different delimiters:

      A. Narrative  - '6 months post-op breast augmentation with 360cc implants.'
      B. Run-on chart - 'Patient Age: 50 Height: 5’7 ... Implant Size (Left): 275 cc'
      C. Tab-delimited chart - "Age: 23\tHt: 5’6”\tWt: 120 Implant: ... Left: 375cc\t\tRight: 350cc"

    Placement and incision are read from the LABELLED chart fields only, never
    from the narrative, per the standing chart-metadata rule.
    """
    specs.summary = re.sub(r"\s+", " ", text).strip()
    chart_parts = []
    for label, value in _holder_split_fields(text):
        value = re.sub(r"\s+", " ", value).strip()
        if not value or label.lower() in HOLDER_NARRATIVE_LABELS:
            continue
        specs.fields.setdefault(label, value)
        chart_parts.append(f"{label}: {value}")
        key = label.lower()
        if key in ("age", "patient age"):
            m = re.match(r"(\d{1,3})\b", value)
            if m and 10 <= int(m.group(1)) <= 100:
                specs.age = int(m.group(1))
        elif key in ("height", "ht"):
            specs.height = value
            specs.height_cm = height_to_cm(value)
        elif key in ("weight", "wt"):
            m = re.match(r"(\d{2,3})\b", value)
            if m:
                specs.weight_lbs = int(m.group(1))
                specs.weight_kg = pounds_to_kg(specs.weight_lbs)
    specs.left_cc, specs.right_cc = holder_parse_volumes(text)
    classify_brand_shape_profile(specs, text)
    if specs.profile is None:
        for pattern, profile in HOLDER_PROFILE_ABBREVIATIONS:
            if pattern.search(text):
                specs.profile = profile
                break
    classify_placement_incision(specs, "\n".join(chart_parts))


def holder_parse_listing(listing_html: str, source_url: str) -> list[CaseData]:
    """Every case inline on one listing page; see the markup contract above."""
    soup = BeautifulSoup(listing_html, "html.parser")
    cases = []
    for holder in soup.select("div.patient-holder"):
        wrap = holder.select_one("div.gallery-wrap")
        if wrap is None:
            continue
        pairs, folder = [], None
        for item in wrap.select("div.slides div.item"):
            srcs = [img.get("data-lazyload-src") or img.get("src")
                    for img in item.select("img")]
            srcs = [s for s in srcs if s]
            if len(srcs) < 2:
                continue
            if folder is None:
                m = re.match(r"\.?/?([^/]+)/", srcs[0])
                if m is None:
                    continue
                folder = m.group(1)
            # './07/03.jpg' is relative to the GALLERY page, not the site root,
            # so it is resolved here rather than left for the caller to prefix
            # with base_url (which would build /07/03.jpg off the domain).
            pairs.append((urljoin(source_url, srcs[0]),
                          urljoin(source_url, srcs[1])))
        if not pairs or folder is None:
            continue
        case = CaseData(case_id=folder, source_url=source_url)
        details = wrap.select_one("div.details")
        text = details.get_text(" ", strip=True) if details is not None else ""
        # 'Case # NNN' is the clinic's own label and is not unique across
        # patients; it is kept as a spec field, never as the case key.
        case_number = None
        m_case = re.search(r"Case\s*#\s*([\w#]+)", text)
        if m_case:
            case_number = m_case.group(1)
            text = text[:m_case.start()] + text[m_case.end():]
        holder_parse_details(text, case.specs)
        if case_number:
            case.specs.fields.setdefault("Case #", case_number)
        combined = holder_combined_procedure(text)
        if combined is not None:
            # Kept in the list with no pairs so the run log accounts for it
            # rather than silently dropping it from the enumeration.
            case.warnings.append(
                f"excluded from the corpus: not a pure breast augmentation "
                f"({combined})")
        else:
            for idx, (before, after) in enumerate(pairs, 1):
                case.pairs.append(ImagePair(key=f"pair{idx}", before_url=before,
                                            after_url=after))
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# Template 'pager' (ciaravino): div.patient cases paged by ul.pager (?page=N)
# ---------------------------------------------------------------------------
#
# What this template fixes:
#
#   * Cases are div.patient blocks rendered ten to a listing page, paginated
#     with ?page=N through ul.pager. The pager enumerates EVERY page number, so
#     the gallery's own page count is the enumeration check - the last pager
#     link times ten, less the short final page.
#   * Every image of a case lives in one numbered asset folder referenced by a
#     RELATIVE path, './378/01.jpg'. That folder number is the case key: the
#     'Case #NNNN' the clinic prints is NOT unique (thebodydoc publishes two
#     consecutive saline cases both labelled Case #2547), and the block's
#     anchor href points at the gallery, not at the case.
#   * The relative path must be resolved against the CANONICAL gallery URL.
#     The block's own anchor points at a different path that serves the same
#     listing (.../breast-augmentation-silicone-implants/2890/), and resolving
#     './378/01.jpg' against that yields a 404.
#   * div.slides holds one div.item per view pair, each with exactly two
#     <img class="feat2"> - odd file first, even second. div.view.s3grid repeats
#     only the FIRST pair, but marks it data-before/data-after, which is what
#     documents the odd/even convention rather than assuming it.
#   * Specs are div.patient-info > div.patient-meta-info, '<strong>Label:</strong>
#     value<br>'. The same block is repeated inside div.details for the popup,
#     so only the patient-info copy is read.
#
# Views are not documented: div.slides items are positional and carry no label,
# and the alt text is one boilerplate string on every image. View labels come
# from the visual-annotation pass; unannotated pairs are skipped, never guessed.

PAGER_FIELD_LABELS = (
    "Age", "Case #", "Height", "Weight", "Gender", "Ethnicity",
    "Implant Size", "Implant Size (Left)", "Implant Size (Right)",
    "Implant Type", "Incision", "Placement", "Procedure", "Patient #",
)
PAGER_LABEL_RE = _label_regex(PAGER_FIELD_LABELS)
PAGER_SIDED_VOLUME_LABELS = {
    "implant size (left)": "left", "implant size (right)": "right",
}
PAGER_VOLUME_LABELS = set(PAGER_SIDED_VOLUME_LABELS) | {"implant size"}
PAGER_NARRATIVE_LABELS = {"procedure"}
PAGER_ASSET_RE = re.compile(r"^\.?/?(\d+)/(\d+)\.(jpe?g|png|webp)$", re.I)
# The same numbered-folder/numbered-file tail, wherever it sits in a URL.
PAGER_ASSET_TAIL_RE = re.compile(r"(?:^|/)(\d+)/(\d+)\.(?:jpe?g|png|webp)$", re.I)
PAGER_CASE_NUMBER_RE = re.compile(r"case\s*#\s*(\d+)", re.I)


def pager_page_count(listing_html: str) -> int | None:
    """Highest ?page=N the gallery's OWN pager offers, or None if unpaginated.

    Read from ul.pager alone. This count is the family's whole enumeration
    check, and a footer nav, a related-content widget or an inline script
    carries ?page= links of its own: an inflated figure walks pages the gallery
    does not have, and on a CMS that serves page 1 for an out-of-range ?page it
    re-collects page 1's blocks under the case ids they already have, where the
    per-page block reconciliation counts them as a match and nothing warns.
    """
    highest = None
    soup = BeautifulSoup(listing_html, "html.parser")
    for link in soup.select("ul.pager a[href]"):
        m = re.search(r"[?&]page=(\d+)", link["href"])
        if m is None:
            continue
        n = int(m.group(1))
        highest = n if highest is None else max(highest, n)
    return highest


def pager_has_pager(listing_html: str) -> bool:
    """Whether the listing published pager markup at all.

    page_count is None for a one-page gallery AND for a listing whose pager
    this parser could not find, and those are not the same claim: the pager is
    the family's only enumeration signal, so 'one page' with no pager markup is
    this parser's result rather than the gallery's own statement, and reading it
    as the end of the set is how 582 declared Etna cases became a confident 450.
    """
    return BeautifulSoup(
        listing_html, "html.parser").select_one("ul.pager") is not None


def pager_listing_case_count(listing_html: str) -> int:
    """div.patient blocks one listing page renders, parsed or not.

    The pager states how many PAGES the gallery has, so it cannot see a case
    the parser dropped inside a page. This is the per-page half of the
    enumeration check.
    """
    return len(BeautifulSoup(listing_html, "html.parser").select("div.patient"))


def _pager_asset_folder(src: str) -> str | None:
    m = PAGER_ASSET_RE.match(src.strip())
    return m.group(1) if m else None


def _pager_asset_ref(src: str) -> tuple[str, int] | None:
    """(asset folder, file number) from ANY spelling of an asset reference.

    Keying a case is a different question and stays on PAGER_ASSET_RE: only the
    platform's relative './44/01.jpg' names a case's own folder there, so an
    absolute CDN path is a block this parser cannot key. But the NUMBERING is
    a property of the file whatever the page spells it as, and the grid and
    the slides do not always spell one image the same way - reading only the
    anchored form there silently drops the evidence instead of using it.
    """
    m = PAGER_ASSET_TAIL_RE.search(src.strip().split("?", 1)[0].split("#", 1)[0])
    return (m.group(1), int(m.group(2))) if m else None


def _pager_descending(before_src: str, after_src: str) -> bool:
    """True when the after asset is numbered BELOW its before, in one folder."""
    before, after = _pager_asset_ref(before_src), _pager_asset_ref(after_src)
    return (before is not None and after is not None
            and before[0] == after[0] and after[1] < before[1])


def _pager_numbering_note(before_src: str, after_src: str,
                        after_first: bool = False) -> str | None:
    """How this couple departs from the numbering in force, or None if it holds.

    The platform habit is odd file before, even file after, ascending. Where
    the page's own markers show this install numbers its after file FIRST, that
    is the convention the case is read against instead.

    Two assets are only comparable within one case's own numbered folder: the
    numbers restart per folder, so a number from another folder says nothing.
    """
    before, after = _pager_asset_ref(before_src), _pager_asset_ref(after_src)
    if before is None or after is None or before[0] != after[0]:
        return None
    before_n, after_n = before[1], after[1]
    if after_first:
        if before_n % 2 == 0 and after_n % 2 == 1 and after_n < before_n:
            return None
        return (f"asset numbering {before_n}/{after_n} departs from this case's "
                f"own even-before/odd-after numbering")
    if before_n % 2 == 1 and after_n % 2 == 0 and after_n > before_n:
        return None
    return (f"asset numbering {before_n}/{after_n} departs from the platform's "
            f"odd-before/even-after convention")


class _PagerMarks:
    """What div.view.s3grid states about which image of a pair is which.

    Two spellings of ONE image have to compare equal. The grid publishes its
    markers as data-before/data-after and the slides publish src; an install
    that renders one relative and the other absolute, or that appends a
    cache-buster, would make every lookup miss and silently reduce the guard to
    the numbering habit with no trace in the output. So every reference is
    canonicalised against the gallery URL before it is compared.

    The markers also state the case's own NUMBERING: a marked pairing whose
    after file is numbered below its before says this install numbers
    after-first, and every slide of the case is then read that way. The grid
    repeats only the first pair, so re-deciding per slide with the marker
    discarded would lose every pair past the first at such a practice.
    """

    def __init__(self, block, gallery_url: str):
        self._gallery_url = gallery_url
        self.pairs: dict[str, str] = {}
        self.after_first = False
        for item in block.select("div.view.s3grid div.item"):
            b = item.select_one("img[data-before]")
            a = item.select_one("img[data-after]")
            if b is None or a is None:
                continue
            self.pairs[self.key(b["data-before"])] = self.key(a["data-after"])
            if _pager_descending(b["data-before"], a["data-after"]):
                self.after_first = True
        self.afters = set(self.pairs.values())

    def key(self, src: str) -> str:
        """One image's comparable form, whatever attribute it was read from."""
        resolved = urljoin(self._gallery_url, (src or "").strip())
        return resolved.split("?", 1)[0].split("#", 1)[0]

    def describes(self, src: str) -> bool:
        """True when the grid names this image on either side of a pairing."""
        key = self.key(src)
        return key in self.pairs or key in self.afters


def _pager_pair_problem(before_src: str, after_src: str, marks: _PagerMarks,
                      ) -> tuple[str | None, str | None]:
    """(contradiction, note) for one div.slides item read in DOM order.

    DOM order alone is too weak to carry a label this consequential: a reversed
    pair teaches the edit model to SHRINK breasts and passes every downstream
    gate silently. Two sources on the page can speak to it - the data-before/
    data-after markers the s3grid publishes, and the platform's odd-first/
    even-second asset numbering - and they do not rank equally. The markers are
    the page's own statement of which image is which, checked in both
    directions because an image the page names as a before turning up in the
    after slot is the same evidence as the pairing itself disagreeing. The
    numbering is a habit: it can raise a suspicion where nothing else speaks,
    but it never overrules a marker, and where a marker establishes the case's
    numbering the whole case is read against THAT.

    So a marker contradiction skips the pair; a marker affirmation keeps it
    whatever the numbering does; and with no marker either way, only a couple
    running against the case's own numbering - the shape of an actual reversal
    - skips. Anything else unconventional is reported and kept.
    """
    before_key, after_key = marks.key(before_src), marks.key(after_src)
    if before_key in marks.pairs and marks.pairs[before_key] != after_key:
        return "contradicts the page's own data-before/data-after pairing", None
    if after_key in marks.pairs:
        return "puts an image the page marks data-before in the after slot", None
    if before_key in marks.afters:
        return "puts an image the page marks data-after in the before slot", None
    note = _pager_numbering_note(before_src, after_src, marks.after_first)
    if marks.pairs.get(before_key) == after_key:
        return None, (f"{note}; kept, the page marks this pairing itself"
                      if note else None)
    before_folder = _pager_asset_folder(before_src)
    after_folder = _pager_asset_folder(after_src)
    if (before_folder is not None and after_folder is not None
            and before_folder != after_folder):
        return (f"pairs asset folder {before_folder} against {after_folder}, "
                f"but a case's images all live in one folder"), None
    if note is not None:
        before_n, after_n = (_pager_asset_ref(before_src)[1],
                             _pager_asset_ref(after_src)[1])
        reversed_shape = (after_n > before_n if marks.after_first
                          else after_n < before_n)
        if reversed_shape:
            direction = "above" if marks.after_first else "below"
            return (f"numbers its after asset ({after_n}) {direction} its "
                    f"before ({before_n})"), None
    return None, f"{note}; kept in DOM order" if note else None


def pager_parse_meta(meta_block, specs: CaseSpecs) -> None:
    """Fill specs from a div.patient-meta-info chart block."""
    prose_parts: list[str] = []
    for line in _br_lines(meta_block):
        fields, leftover = _split_labelled_line(line, PAGER_LABEL_RE)
        if leftover:
            prose_parts.append(leftover)
        for label, value in fields:
            if not value or value.strip() in {"--", "-", "N/A"}:
                continue
            key = label.lower()
            if key in PAGER_NARRATIVE_LABELS:
                prose_parts.append(f"{label}: {value}")
                continue
            specs.fields.setdefault(label, value)
            if key not in PAGER_VOLUME_LABELS:
                continue
            cc = labelled_volume(value)
            if cc is None:
                continue
            side = PAGER_SIDED_VOLUME_LABELS.get(key)
            if side == "left":
                specs.left_cc = cc
            elif side == "right":
                specs.right_cc = cc
            elif specs.left_cc is None and specs.right_cc is None:
                specs.left_cc = specs.right_cc = cc
    specs.summary = " ".join(prose_parts).strip()


def pager_parse_listing_page(listing_html: str, gallery_url: str,
                                      gallery_tag: str) -> list[CaseData]:
    """Every div.patient case rendered on one Page 1 Solutions listing page.

    gallery_url is the CANONICAL listing URL; relative asset paths resolve
    against it. gallery_tag namespaces the case id, because each sub-gallery
    numbers its asset folders from 1 independently.
    """
    soup = BeautifulSoup(listing_html, "html.parser")
    cases: list[CaseData] = []
    for block in soup.select("div.patient"):
        info = block.select_one("div.patient-info")
        srcs = [i.get("src") or i.get("data-before") or i.get("data-after") or ""
                for i in block.select("img")]
        folders = [f for f in (_pager_asset_folder(s) for s in srcs) if f]
        if not folders:
            # The asset folder is the case key, so a block that publishes none
            # cannot be collected - but it is a case the gallery rendered, and
            # dropping it silently is a case that no accounting can sum.
            link = info.select_one("a") if info else None
            m = (PAGER_CASE_NUMBER_RE.search(link.get_text(" ", strip=True))
                 if link is not None else None)
            evidence = (f"Case #{m.group(1)}" if m
                        else next((s for s in srcs if s), "no image published"))
            print(f"  WARN page1solutions/{gallery_tag}: case block "
                  f"({evidence}) publishes no numbered asset path; dropped")
            continue
        folder = folders[0]
        case_id = f"{gallery_tag}-{folder}"
        case = CaseData(case_id=case_id, source_url=gallery_url)

        # div.slides carries every view pair; div.view.s3grid repeats only the
        # first, but its data-before/data-after is what documents which of the
        # two images in an item is which.
        marks = _PagerMarks(block, gallery_url)
        marks_matched = False
        for index, item in enumerate(block.select("div.slides div.item"), 1):
            imgs = [i.get("src", "") for i in item.select("img") if i.get("src")]
            if len(imgs) != 2:
                case.warnings.append(
                    f"slide {index} publishes {len(imgs)} image(s), not 2; skipped")
                continue
            before_src, after_src = imgs[0], imgs[1]
            if marks.describes(before_src) or marks.describes(after_src):
                marks_matched = True
            contradiction, note = _pager_pair_problem(before_src, after_src, marks)
            if contradiction is not None:
                case.warnings.append(f"slide {index} {contradiction}; skipped")
                continue
            if note is not None:
                case.warnings.append(f"slide {index} {note}")
            case.pairs.append(ImagePair(
                key=f"pair{index}",
                before_url=urljoin(gallery_url, before_src),
                after_url=urljoin(gallery_url, after_src)))
        if marks.pairs and not marks_matched:
            # The guard the module docstring calls the page's own statement of
            # which image is which, silently inert.
            case.warnings.append(
                "div.view.s3grid marks a before/after pairing that names none "
                "of the case's slide images; the pairing guard checked nothing")
        if not case.pairs:
            case.warnings.append("no usable image pairs")

        specs = CaseSpecs()
        meta_block = info.select_one("div.patient-meta-info") if info else None
        if meta_block is None:
            case.warnings.append("no div.patient-meta-info chart published")
        else:
            pager_parse_meta(meta_block, specs)

        procedure = ""
        link = info.select_one("a") if info else None
        if link is not None:
            procedure = link.get_text(" ", strip=True)
            m = PAGER_CASE_NUMBER_RE.search(procedure)
            if m:
                specs.fields.setdefault("Case #", m.group(1))
        age = specs.fields.get("Age", "")
        if age.strip().isdigit():
            specs.age = int(age.strip())
        height = specs.fields.get("Height", "")
        if height:
            specs.height = (height.replace("’", "'").replace("”", '"')
                            .replace("“", '"').strip())
            specs.height_cm = height_to_cm(specs.height)
        weight = specs.fields.get("Weight", "")
        m = re.match(r"^(\d{2,3})\s*(?:lbs?|pounds?)?\.?$", weight.strip(), re.I)
        if m:
            specs.weight_lbs = int(m.group(1))
            specs.weight_kg = pounds_to_kg(specs.weight_lbs)

        # Profile and brand come from the implant-size fields only - the
        # clinic's own labelled statement of what was implanted.
        size_text = " ".join(v for k, v in specs.fields.items()
                             if k.lower() in PAGER_VOLUME_LABELS
                             or k.lower() == "implant type")
        classify_brand_shape_profile(specs, size_text)
        if specs.profile is None:
            specs.profile = captain_profile_term(size_text)
        classify_placement_incision(
            specs, " ".join(f"{k}: {v}" for k, v in specs.fields.items()))
        case.specs = specs

        # specs.summary is not optional here: the chart's own 'Procedure:' line
        # is narrative, so pager_parse_meta routes it to the summary
        # and never to specs.fields, and so does any line whose label this
        # parser does not know. The anchor is the gallery's heading, identical
        # on every block of the page - screening on that alone is the mistake
        # that let 86 combined cases through at the Etna clinics.
        term = combined_procedure_term(
            procedure, " ".join(f"{k}: {v}" for k, v in specs.fields.items()),
            specs.summary)
        if term is not None:
            case.warnings.append(
                f"not pure breast augmentation (case text names '{term}'); "
                "excluded by captain ruling")
            case.pairs = []
        cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# Enumeration, one walk per template
# ---------------------------------------------------------------------------


def _collect_item(cfg, fetcher) -> list[CaseData]:
    gallery_path = cfg.gallery_paths[0]
    listing = fetcher.get(cfg.base_url + gallery_path,
                          f"{cfg.slug}_listing.html").decode("utf-8", "replace")
    cases = []
    for case_id in item_list_cases(listing):
        url = f"{cfg.base_url}{gallery_path}{case_id}/"
        html = fetcher.get(url, f"{cfg.slug}_case_{case_id}.html").decode(
            "utf-8", "replace")
        cases.append(item_parse_case(html, case_id, url))
    return cases


def _collect_entry(cfg, fetcher) -> list[CaseData]:
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
        html = sg._fetch_seed(fetcher, url, key)
        if html is None:
            break
        found = entry_list_cases(html)
        if not found:
            break
        for case_number, case_url in found:
            if case_number not in seen:
                seen.add(case_number)
                listed.append((case_number, case_url))
        page += 1
        if page > ENTRY_MAX_PAGES:
            print(f"  WARN {cfg.slug}: listing walk hit the "
                  f"{ENTRY_MAX_PAGES}-page ceiling; the gallery may still "
                  f"be paging, so this count is a floor, not a total")
            break
    print(f"  listing walk: {page - 1} page(s), {len(listed)} case(s) "
          f"(platform publishes no declared total)")
    cases = []
    for case_number, case_url in listed:
        html = fetcher.get(case_url, f"{cfg.slug}_case_{case_number}.html"
                           ).decode("utf-8", "replace")
        cases.append(entry_parse_case(html, case_number, case_url))
    return cases


def _collect_holder(cfg, fetcher) -> list[CaseData]:
    url = cfg.base_url + cfg.gallery_paths[0]
    listing = fetcher.get(url, f"{cfg.slug}_listing.html").decode("utf-8", "replace")
    cases = holder_parse_listing(listing, url)
    # The gallery publishes no case total of its own, so enumeration is
    # checked against the numbering instead: the case folders are a
    # contiguous 1..N run, and a gap would be a case the listing withheld.
    numbers = sorted(int(c.case_id) for c in cases if c.case_id.isdigit())
    if numbers:
        missing = sorted(set(range(1, numbers[-1] + 1)) - set(numbers))
        if missing:
            print(f"  WARN {cfg.slug}: case folders {missing} missing from "
                  f"the listing's 1..{numbers[-1]} run")
        else:
            print(f"  {cfg.slug}: {len(numbers)} case(s), a contiguous "
                  f"1..{numbers[-1]} run; the gallery declares no total")
    return cases


def _collect_pager(cfg, fetcher) -> list[CaseData]:
    cases = []
    for gallery_path in cfg.gallery_paths:
        tag = gallery_path.strip("/").rsplit("/", 1)[-1]
        short = re.sub(r"^breast-augmentation-|-implants$", "", tag) or tag
        paged_url = cfg.base_url + gallery_path
        first = fetcher.get(
            paged_url, f"{cfg.slug}_listing_{tag}_p1.html").decode(
                "utf-8", "replace")
        declared_pages = pager_page_count(first)
        if not pager_has_pager(first):
            print(f"  WARN {cfg.slug}/{tag}: the listing publishes no "
                  f"ul.pager markup, so a one-page sweep is this parser's "
                  f"result rather than the gallery's own statement")
        page_cases = pager_parse_listing_page(first, paged_url, short)
        cases.extend(page_cases)
        rendered_blocks = pager_listing_case_count(first)
        collected_cases = len(page_cases)
        if rendered_blocks == 0:
            print(f"  WARN {cfg.slug}/{tag}: listing page 1 renders no "
                  f"case block(s)")
        pages_walked = 1
        page = 2
        while declared_pages is not None and page <= declared_pages:
            html = sg._fetch_optional(
                fetcher, f"{paged_url}?page={page}",
                f"{cfg.slug}_listing_{tag}_p{page}.html")
            if html is None:
                print(f"  WARN {cfg.slug}/{tag}: page {page} unavailable")
                break
            # The end of the set is a page that RENDERS nothing. A page
            # whose blocks were all dropped renders plenty, and reading it
            # as the end abandons every page after it - so it is walked,
            # and the block reconciliation below is what reports the drop.
            rendered = pager_listing_case_count(html)
            if rendered == 0:
                print(f"  WARN {cfg.slug}/{tag}: page {page} renders no "
                      f"case block(s)")
                break
            more = pager_parse_listing_page(html, paged_url, short)
            cases.extend(more)
            rendered_blocks += rendered
            collected_cases += len(more)
            pages_walked += 1
            page += 1
        # The pager enumerates every page, so it is the enumeration check.
        if declared_pages is not None and pages_walked != declared_pages:
            print(f"  WARN {cfg.slug}/{tag}: walked {pages_walked} of the "
                  f"{declared_pages} page(s) the pager offers")
        else:
            print(f"  {cfg.slug}/{tag}: walked all "
                  f"{declared_pages or 1} listing page(s)")
        # Pages walked says nothing about cases dropped INSIDE a page, so
        # the blocks the listing rendered are reconciled separately.
        if collected_cases != rendered_blocks:
            print(f"  WARN {cfg.slug}/{tag}: collected {collected_cases} "
                  f"case(s) from the {rendered_blocks} case block(s) those "
                  f"page(s) render")
        else:
            print(f"  {cfg.slug}/{tag}: collected all {collected_cases} "
                  f"case block(s) those page(s) render")
    return cases


# ClinicConfig.template -> that template's enumeration.
TEMPLATES = {
    "item": _collect_item,
    "entry": _collect_entry,
    "holder": _collect_holder,
    "pager": _collect_pager,
}
