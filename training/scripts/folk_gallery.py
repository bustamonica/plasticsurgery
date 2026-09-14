#!/usr/bin/env python3
"""Folk Plastic Surgery (Dr Stacey Folk, Denver) - Webflow CMS gallery.

One clinic, one parser. It is filed separately from the RM Gallery 2 family
that the rest of its consent cohort runs on, and separately from the BRAG book
parsers, for a reason worth stating: **its assets are named like BRAG book
exports (`breast-augmentation-before-and-after-<id>_highres.webp`) but the
platform is Webflow.** The prospecting run read the asset naming as the family
and expected `sanantonio` to reach it; nothing about the page is BRAG book.
Asset naming is not the platform - the same mistake `swan` records against Etna
(AGENTS.md).

Markup contract
---------------
One page, no pagination, everything inline. Each case is a
`div.case-display.w-dyn-item` publishing `Patient #`, a post-op timeline, a
free-text implant line and a few labelled fields.

Three things here are not guessable and each one silently corrupts a naive
parse:

1. **The photographs are not in the `<img>` tags.** Each case carries five
   lightbox slots; four are unbound placeholders (`w-dyn-bind-empty`) and the
   `<img>`s inside every slot are responsive `srcset` renditions. The case's
   real set of composites is the JSON in its one populated `script.w-json`
   manifest - 38 manifests, 113 composites, which is every photograph the
   gallery publishes. Reading the thumbnails instead finds one image per case
   and the `-p-500`/`-p-800` `srcset` entries fall under the 400px floor once
   split. Same shape as `choice` (AGENTS.md), a different Webflow build.

2. **Webflow renders EVERY option of a conditional field and hides the ones
   that do not apply** with `w-condition-invisible`. Patient 1's post-op
   timeline publishes as four sibling divs - `1-11 Weeks`, `3-5 Months`,
   `1 Year +`, `6-11 Months` - of which three carry that class. A parser that
   takes the text without honouring it records three timelines the clinic never
   claimed for that patient, and the same applies to any other conditional
   field the build adds later. `_visible_only()` strips them first.

3. **A published RANGE is not a measurement** (the sixsurgery precedent). Seven
   cases publish `Volume: Between 450cc to 500cc` rather than a figure, and
   under the captain's 2026-08-26 ruling a case with no readable volume is
   skipped rather than emitted on the midpoint of a range.

Photographs are side-by-side before|after composites split at the midpoint.
They are NOT one size: the gallery publishes 1800x599, 1800x675, 1800x678 and
896x336 among others, and the small family splits to 448x336, which is under
`ingest.py`'s 400px floor on the short side. That is a real rejection class
here, not a parser bug - see the collection report for the measured split.

Watermark: a "Stacey Folk, MD" script wordmark sits in the bottom-right corner
of EACH half, over the lower abdomen - one mark per half, so it survives the
midpoint split on both sides and is not a label leak. It is cropped rather than
tolerated (captain, 2026-08-19) via `ClinicConfig.bottom_crop_frac`, as a
fraction of WIDTH, because this gallery serves the same framing at several
resolutions and a pixel constant would miss it on the large exports and eat a
sixth of the small ones (the arps precedent).
"""

from __future__ import annotations

import html as _html
import json
import re

import scrape_gallery as sg

FOLK_CASE_RE = re.compile(
    r'<div[^>]*class="[^"]*\bcase-display\b[^"]*\bw-dyn-item\b[^"]*"[^>]*>', re.I)
FOLK_JSON_RE = re.compile(r'<script[^>]*class="w-json"[^>]*>(.*?)</script>', re.I | re.S)
FOLK_PATIENT_RE = re.compile(r"Patient\s*#\s*\|?\s*(\d+)", re.I)
# Webflow's own marker for "this conditional value does not apply to this item".
FOLK_INVISIBLE_RE = re.compile(
    r'<(\w+)[^>]*\bw-condition-invisible\b[^>]*>.*?</\1>', re.I | re.S)


def _strip_noise(html: str) -> str:
    html = re.sub(r"<script(?![^>]*w-json).*?</script>", "", html, flags=re.I | re.S)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.I | re.S)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    return html


def _visible_only(fragment: str) -> str:
    """Drop every conditionally-hidden value Webflow rendered but does not show."""
    previous = None
    while previous != fragment:
        previous = fragment
        fragment = FOLK_INVISIBLE_RE.sub("", fragment)
    return fragment


def _text(fragment: str) -> str:
    fragment = re.sub(r"<script.*?</script>", "", fragment, flags=re.I | re.S)
    fragment = re.sub(r"</(div|p|h\d|li)>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", "\n", fragment)
    fragment = _html.unescape(fragment)
    lines = [re.sub(r"\s+", " ", line).strip() for line in fragment.split("\n")]
    return "\n".join(line for line in lines if line)


def folk_case_blocks(listing_html: str) -> list[str]:
    """The gallery's case blocks, in published order."""
    html = _strip_noise(listing_html)
    starts = [m.start() for m in FOLK_CASE_RE.finditer(html)]
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(html)
        blocks.append(html[start:end])
    return blocks


def folk_composites(block: str) -> list[str]:
    """Every composite URL this case publishes, from its lightbox manifest."""
    urls: list[str] = []
    for match in FOLK_JSON_RE.finditer(block):
        try:
            payload = json.loads(_html.unescape(match.group(1)))
        except ValueError:
            continue
        for item in payload.get("items", []):
            url = item.get("url")
            if url and item.get("type", "image") == "image" and url not in urls:
                urls.append(url)
    return urls


# Purity is screened on the case's own published text, never on the asset name -
# every photograph in this gallery is named `breast-augmentation-...` regardless
# of what the case says.
FOLK_IMPURE_PATTERNS = [
    (re.compile(r"\bmastopexy\b", re.I), "mastopexy"),
    (re.compile(r"\blift(s|ed|ing)?\b", re.I), "breast lift"),
    (re.compile(r"\breduction\b", re.I), "reduction"),
    (re.compile(r"\brevision\b", re.I), "revision"),
    (re.compile(r"\bexplant\w*\b|\bcapsulectomy\b", re.I), "explant"),
    (re.compile(r"\breplac\w*\b[^.]{0,60}\bimplant|\bimplants?\b[^.]{0,60}\breplac\w*", re.I),
     "implant exchange"),
    (re.compile(r"\bexchange\b", re.I), "implant exchange"),
    (re.compile(r"\bmommy makeover\b", re.I), "mommy makeover"),
    (re.compile(r"\bfat (transfer|graft\w*)\b", re.I), "fat transfer"),
    (re.compile(r"\breconstruction\b", re.I), "breast reconstruction"),
    (re.compile(r"\babdominoplasty\b|\btummy tuck\b", re.I), "abdominoplasty"),
    (re.compile(r"\bliposuction\b", re.I), "liposuction"),
]


def folk_impure_reason(case_text: str) -> str | None:
    """The combined/revision procedure this case's own text names, or None."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n", case_text):
        if not sentence.strip():
            continue
        if re.search(r"\b(?:no|not|without|declin\w*|instead of|rather than"
                     r"|discussed|option)\b", sentence, re.I):
            continue
        for pattern, reason in FOLK_IMPURE_PATTERNS:
            if pattern.search(sentence):
                return reason
    return None


# A range is not a measurement: 'Volume: Between 450cc to 500cc' and
# 'Between 250cc and 300cc' are both refused rather than averaged.
FOLK_RANGE_RE = re.compile(
    r"\b(?:between\s*)?\d{2,4}\s*(?:cc|ml)?\s*(?:-|–|—|\bto\b|\band\b)\s*\d{2,4}\s*(?:cc|ml)\b",
    re.I)
FOLK_VOLUME_RE = re.compile(r"(\d{2,4})\s*(?:cc|ml)\b", re.I)
FOLK_SIDE_RE = re.compile(r"\b(left|right)\b", re.I)


def folk_parse_volumes(case_text: str) -> tuple[float | None, float | None]:
    """(left_cc, right_cc) for a case, or (None, None) when it publishes none.

    This clinic writes the volume BEFORE the side - '300 cc (right side), 250cc
    (left side)' - which is the ordering the shared narrative reader gets
    backwards (AGENTS.md), so the side is taken from the text that FOLLOWS each
    figure, within its own comma-delimited segment.
    """
    text = " ".join(case_text.split())
    if FOLK_RANGE_RE.search(text):
        return None, None
    sides: dict[str, float] = {}
    plain: float | None = None
    for match in FOLK_VOLUME_RE.finditer(text):
        value = float(match.group(1))
        # The side can sit several words after its figure - patient 10 writes
        # "250cc moderate classic (right), 300cc moderate profile plus (left)"
        # - so the window runs to the end of this figure's own comma-delimited
        # segment rather than a fixed short distance. A narrower window read
        # that case as one-sided and lost the left breast entirely.
        segment = text[match.end():match.end() + 60].split(",")[0]
        side_match = FOLK_SIDE_RE.search(segment)
        if side_match:
            sides.setdefault(side_match.group(1).lower(), value)
        elif plain is None:
            plain = value
    if sides:
        return sides.get("left"), sides.get("right")
    if plain is not None:
        return plain, plain
    return None, None


# Profile: only the schema's four values are decoded. This gallery also
# publishes 'low-profile plus' (Natrelle Inspira's LP+), which has NO schema
# equivalent - 'low' is not in the enum - so it is deliberately left
# undocumented rather than rounded up to moderate.
FOLK_PROFILE_PATTERNS = [
    (re.compile(r"\b(?:extra|ultra)[- ]high\b", re.I), "extra-high"),
    (re.compile(r"\blow[- ]profile\s*plus\b|\blow[- ]plus\b", re.I), None),
    (re.compile(r"\bmoderate[- ](?:profile[- ])?plus\b", re.I), "moderate-plus"),
    # The plain patterns must NOT also fire on their own "... plus" form, or a
    # single "moderate profile plus" reads as two different profiles and the
    # conflict guard below throws away a projection the clinic did state.
    (re.compile(r"\bhigh[- ](?:profile|projection)\b(?!\s*plus\b)", re.I), "high"),
    (re.compile(r"\bmoderate[- ](?:profile|projection)\b(?!\s*plus\b)", re.I), "moderate"),
]


def folk_profile(case_text: str) -> str | None:
    """The projection this case documents, or None.

    Returns None when the case documents DIFFERENT projections for the two
    breasts - patient 10 has "moderate classic (right)" against "moderate
    profile plus (left)" - because `profile` is one field per pair and picking
    either side would record half a truth as the whole one. The arps rule:
    report a contradiction, never resolve it silently.
    """
    text = " ".join(case_text.split())
    found = []
    for pattern, profile in FOLK_PROFILE_PATTERNS:
        if pattern.search(text) and profile is not None:
            found.append(profile)
    if len(set(found)) > 1:
        return None
    return found[0] if found else None


def folk_parse_listing(listing_html: str, source_url: str) -> list[sg.CaseData]:
    """Every published case, pure or not.

    An impure case is returned carrying its reason and no pairs so the run
    accounts for all 38 published patients rather than silently shrinking the
    gallery (the wyten rule).
    """
    cases: list[sg.CaseData] = []
    for index, block in enumerate(folk_case_blocks(listing_html), start=1):
        visible = _visible_only(block)
        text = _text(visible)
        patient = FOLK_PATIENT_RE.search(text.replace("\n", " | "))
        case_id = patient.group(1) if patient else str(index)
        case = sg.CaseData(case_id=case_id, source_url=source_url)

        specs = sg.CaseSpecs()
        body = "\n".join(line for line in text.split("\n")
                         if not re.match(r"^(Patient\s*#|\d+)$", line.strip(), re.I))
        specs.summary = " ".join(
            line for line in body.split("\n")
            if not line.rstrip().endswith(":")).strip()
        for label, value in re.findall(r"^([A-Za-z][A-Za-z ()/-]{2,28}):\s*$\n(.+)$",
                                       body, re.M):
            specs.fields.setdefault(label.strip().lower(), value.strip())
        specs.left_cc, specs.right_cc = folk_parse_volumes(body)
        sg.classify_brand_shape_profile(specs, body)
        specs.profile = folk_profile(body)
        sg.classify_placement_incision(specs, body)
        case.specs = specs

        # A case can publish a DIFFERENT implant per breast - patient 10 has
        # "250cc moderate classic (right), 300cc moderate profile plus (left)"
        # - and `profile` is one field per pair, so whatever was decoded there
        # describes at most one side. Brand product families ("Classic") are
        # deliberately not decoded into profiles (the Natrelle model-code
        # precedent), so the mismatch cannot always be detected from the
        # profile words alone; the differing sided volumes are the signal that
        # survives. Warned rather than dropped or silently resolved (arps).
        if (specs.profile is not None and specs.left_cc is not None
                and specs.right_cc is not None and specs.left_cc != specs.right_cc):
            case.warnings.append(
                f"case publishes a different implant per breast "
                f"(left {specs.left_cc:g}cc, right {specs.right_cc:g}cc); "
                f"profile '{specs.profile}' may describe one side only")

        reason = folk_impure_reason(body)
        if reason:
            case.warnings.append(f"excluded: not a pure augmentation ({reason})")
            cases.append(case)
            continue
        for pair_index, url in enumerate(folk_composites(block), start=1):
            case.pairs.append(sg.ImagePair(
                key=f"pair{pair_index}", before_url=url, after_url=url,
                split_composite=True))
        if not case.pairs:
            case.warnings.append("no composites published for this case")
        cases.append(case)
    return cases
