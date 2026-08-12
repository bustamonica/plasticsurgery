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

20 more consented clinics (2026-08 batch) are registered in CLINICS below,
one parser family per gallery platform (Influx legacy/Growthstack/S3, Etna
Interactive, Webflow, Studio 3 Marketing/DatoCMS, WordPress-custom, and a
handful of bespoke builds); see each parser function's docstring for its
specific markup contract.
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
from urllib.parse import unquote, urljoin, urlsplit

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
        gallery_paths=["/gallery/breast/breast-augmentation/"], kind="wny"),
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
    # Set (with before_url == after_url) when before_url/after_url point at a
    # shared multi-panel grid composite (e.g. drrohrich's 2x2, teitelbaum's
    # 2x3): grid_shape is (rows, cols); before_cell/after_cell are (row, col)
    # of this pair's two panels within that grid.
    grid_shape: tuple[int, int] | None = None
    before_cell: tuple[int, int] | None = None
    after_cell: tuple[int, int] | None = None


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
    if specs.profile is None and "motiva" in lower:
        for pattern, profile in MOTIVA_PROFILE_PATTERNS:
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


def influx_swiper_parse_case(case_html: str, case_id: str, source_url: str,
                             gallery_path: str) -> CaseData:
    """Single-view images labeled only 'Before'/'After' by a sibling span.

    No front/oblique/side labels exist anywhere on the page (unlike
    drkolker's alt-text convention), so images are paired positionally
    (before immediately followed by after, in document order) and every
    view label comes from visual-inspection annotations.
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
    if detail is not None:
        for p in detail.find_all("p"):
            text = p.get_text(" ", strip=True)
            if not text:
                continue
            label, sep, value = text.partition(": ")
            if sep and value:
                specs.fields[label.strip()] = value.strip()
            elif not specs.summary:
                specs.summary = text
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
        specs.height = specs.fields.get("Height", "")
    if specs.weight_lbs is None:
        m = re.match(r"(\d+)", specs.fields.get("Weight", ""))
        if m:
            specs.weight_lbs = int(m.group(1))
    vol_ccs = _cc_numbers(specs.fields.get("Implant volume", ""))
    if vol_ccs:
        specs.left_cc = specs.right_cc = vol_ccs[0]
    if specs.left_cc is None and specs.summary:
        specs.left_cc, specs.right_cc = parse_fill_volumes(specs.summary)
    haystack = " ".join([specs.summary, *specs.fields.values()])
    classify_brand_shape_profile(specs, haystack)
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
    if cfg.kind == "mya":
        listing = fetcher.get(cfg.base_url + cfg.gallery_paths[0],
                              f"{cfg.slug}_listing.html").decode("utf-8", "replace")
        return mya_parse_listing(listing, cfg.base_url + cfg.gallery_paths[0])
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
            if pair.grid_shape is not None:
                full_url = (pair.before_url if pair.before_url.startswith("http")
                            else cfg.base_url + pair.before_url)
                data = fetcher.get(full_url, image_cache_key(cfg.slug, full_url))
                rows, cols = pair.grid_shape
                before_data = crop_grid_cell(data, rows, cols, pair.before_cell)
                after_data = crop_grid_cell(data, rows, cols, pair.after_cell)
                (pair_dir / "before.jpg").write_bytes(before_data)
                (pair_dir / "after.jpg").write_bytes(after_data)
            elif pair.split_composite:
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
