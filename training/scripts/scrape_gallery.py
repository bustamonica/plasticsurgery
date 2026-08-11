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
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests
from bs4 import BeautifulSoup

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
PROFILE_PATTERNS = [
    (re.compile(r"\b(extra[- ]high|ultra[- ]high)\b", re.I), "extra-high"),
    (re.compile(r"\bmoderate[- ](profile[- ])?plus\b", re.I), "moderate-plus"),
    (re.compile(r"\bhigh\s+(profile|projection)\b", re.I), "high"),
    (re.compile(r"\bmoderate\s+(profile|projection)\b", re.I), "moderate"),
]


@dataclass
class ClinicConfig:
    slug: str
    consent_ref: str
    base_url: str
    gallery_paths: list[str]
    kind: str  # parser implementation key


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


@dataclass
class ImagePair:
    key: str  # pair key within the case, e.g. 'front'/'oblique'/'side' or 'pair1'
    before_url: str
    after_url: str
    view_hint: str | None = None  # page-documented view label, if any
    # True when before_url/after_url point at the same side-by-side composite
    # image (left half before, right half after) that must be split on save.
    split_composite: bool = False


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


class PoliteFetcher:
    def __init__(self, cache_dir: Path, delay: float = 2.0, offline: bool = False):
        self.cache_dir = cache_dir
        self.delay = delay
        self.offline = offline
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request = 0.0
        self.requests_made = 0

    def get(self, url: str, cache_key: str) -> bytes:
        path = self.cache_dir / cache_key
        if path.exists():
            return path.read_bytes()
        if self.offline:
            raise FileNotFoundError(f"offline mode and no cache entry for {url}")
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        resp = self.session.get(url, timeout=60)
        self._last_request = time.monotonic()
        self.requests_made += 1
        resp.raise_for_status()
        data = resp.content
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data


def image_cache_key(clinic: str, url: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    name = re.sub(r"[^\w.()-]+", "_", name)
    digest = hashlib.sha256(url.encode()).hexdigest()[:8]
    return f"images/{clinic}/{urlsplit(url).netloc}_{digest}_{name}"


# ---------------------------------------------------------------------------
# Shared spec helpers
# ---------------------------------------------------------------------------


def _parse_fill_side(segment: str) -> float | None:
    """Final cc for one breast from text like '270 filled to 285cc' or '185cc'."""
    m = re.search(r"filled\s+to\s*(\d+(?:\.\d+)?)\s*cc", segment, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*cc", segment, re.I)
    return float(m.group(1)) if m else None


# Implant volumes only: schema bounds are 100-1000cc. Thousands-grouped numbers
# ('1,600cc of fat' from combined lipo cases) are parsed in full and then
# dropped by the range filter so they never pollute implant volumes.
CC_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*cc", re.I)


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
            r"(\d+(?:\.\d+)?)\s*cc\s*(?:on the\s+)?\((left|right|l|r)\)", text, re.I):
        assign(m.group(2).lower(), float(m.group(1)))
    for m in re.finditer(r"(\d{3})\s*\((left|right|l|r)\)", text, re.I):
        assign(m.group(2).lower(), float(m.group(1)))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*cc\s+on the\s+(left|right)\b", text, re.I):
        assign(m.group(2).lower(), float(m.group(1)))
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
        if keyword in lower:
            specs.brand = brand
            break
    if re.search(r"\bround\b", lower):
        specs.shape = "round"
    elif re.search(r"\b(teardrop|anatomic|shaped)\b", lower):
        specs.shape = "teardrop"
    for pattern, profile in PROFILE_PATTERNS:
        if pattern.search(haystack):
            specs.profile = profile
            break


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


def split_composite_image(data: bytes) -> tuple[bytes, bytes]:
    """Split a side-by-side before|after composite into (before, after) JPEGs.

    The split is the exact horizontal midpoint. Raises ValueError for
    portrait/square images, where a left|right split cannot be assumed.
    """
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(data))
    if img.width <= img.height:
        raise ValueError(
            f"composite image is not landscape ({img.width}x{img.height}); "
            "cannot assume a left|right before|after split")
    half = img.width // 2
    out = []
    for box in ((0, 0, half, img.height), (half, 0, img.width, img.height)):
        buf = io.BytesIO()
        img.crop(box).convert("RGB").save(buf, format="JPEG", quality=95)
        out.append(buf.getvalue())
    return out[0], out[1]


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
    if laterality_source:
        details.append(f"view labels: {laterality_source}")
    if details:
        parts.append(". ".join(details) + ".")
    return "\n".join(parts)


def resolve_view(pair: ImagePair, annotations: dict) -> tuple[str | None, str | None]:
    """(schema view, annotation source) for a pair, or (None, ...) to skip."""
    pair_ann = annotations.get("pairs", {}).get(pair.key, {})
    if pair_ann.get("view") in SCHEMA_VIEWS:
        return pair_ann["view"], "visual inspection of downloaded images"
    if pair.view_hint == "front":
        return "front", None
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


def collect_cases(cfg: ClinicConfig, fetcher: PoliteFetcher) -> list[CaseData]:
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
    args = parser.parse_args()

    cfg = CLINICS[args.clinic]
    cache_dir = args.cache_dir or (args.out / ".scraper-cache")
    fetcher = PoliteFetcher(cache_dir, delay=args.delay, offline=args.offline)
    annotations_all = json.loads(args.annotations.read_text()) if args.annotations else {}

    cases = collect_cases(cfg, fetcher)
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
            if pair.split_composite:
                full_url = (pair.before_url if pair.before_url.startswith("http")
                            else cfg.base_url + pair.before_url)
                data = fetcher.get(full_url, image_cache_key(cfg.slug, full_url))
                try:
                    before_data, after_data = split_composite_image(data)
                except ValueError as exc:
                    print(f"    SKIP {pair.key}: {exc}")
                    skipped += 1
                    continue
                (pair_dir / "before.jpg").write_bytes(before_data)
                (pair_dir / "after.jpg").write_bytes(after_data)
            else:
                for stem, url in (("before", pair.before_url), ("after", pair.after_url)):
                    full_url = url if url.startswith("http") else cfg.base_url + url
                    ext = Path(urlsplit(full_url).path).suffix or ".jpg"
                    data = fetcher.get(full_url, image_cache_key(cfg.slug, full_url))
                    (pair_dir / f"{stem}{ext.lower()}").write_bytes(data)
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
