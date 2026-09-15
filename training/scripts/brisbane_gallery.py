#!/usr/bin/env python3
"""Brisbane Cosmetic Clinic (Dr Georgina Konrat) - one Elementor page.

One clinic, one parser: a bespoke WordPress/Elementor build that no other
consented clinic shares. The prospecting report files it as "bespoke
WordPress", and it is not the Elementor CAROUSEL `wyten` reads - this page uses
Elementor's GALLERY widget, with a different markup and a different caption
arrangement.

Markup contract
---------------
**Everything is on one page** (`/galleries/breast-augmentation/`): no case
pages, no pagination, no AJAX, and no declared total. The enumeration check is
therefore the page's own count of gallery widgets, and the collection report
says so rather than implying a reconciliation against a published figure.

**A case is one `elementor-widget-gallery` plus the text-editor widget that
FOLLOWS it.** The page reads heading, a boilerplate paragraph, then gallery,
caption, gallery, caption ... and ends on a caption. Pairing a caption with the
gallery BEFORE it is the only reading under which every gallery has a caption
and no caption is orphaned, and it was confirmed against the photographs: the
caption `Pectus excavatum, moderate asymmetry` follows the `_22` gallery, whose
front views show a pectus deformity, and `major asymmetry right breast twice
size of the left` follows the `_6` gallery, whose before-front shows exactly
that. Reading the caption before the gallery would hand every case its
neighbour's implant.

**The photographs are not `<img>` tags.** Each gallery item is a
`div.e-gallery-image` whose photograph is its `data-thumbnail` attribute (the
full-size upload - there is no `-WxH` WordPress derivative to strip), with
`data-width`/`data-height` beside it. A plain `<img>` scrape finds only the
logos.

**Four photographs per case, published as `Breast_Augmentation_<N>{a,b,c,d}`,
and the page documents neither before/after nor the view.** Opening all ten
cases shows one fixed protocol: `a` before-front, `b` before-oblique, `c`
after-front, `d` after-oblique. So the pairs are (a, c) and (b, d), and the
pair key is the letter couple - the clinic's own filename suffix, which does
not drift the way a running count does. The views still come from the
annotation file, like every gallery that does not document them. The two halves
of a pair are NOT always the same resolution (1000x1200 beside 1539x1847 on
case 1); the aspect ratio matches, and ingest resizes, so this is a size
difference and not a framing one.

**The case number** is the `<N>` of the clinic's own filenames, the same number
the widget's `aria-label` carries ('Breast augmentation 14a'); the page order
is not numeric (1, 14, 12, 22, 8, ...), and the filename number is what
survives a reorder.

Caption vocabulary
------------------
One line per case: age, presentation, then the implant as a MANUFACTURER STYLE
CODE with its size - Mentor `CPG 322-330 cc`, Motiva `ERSD-285 cc`,
`ERSF – 315Q` - and a gloss in brackets ('(anatomical moderate profile)').

- **Volume is read only where the clinic wrote the unit**: the figure before
  `cc`, sided by a `Right`/`Left` that precedes it. Three captions give the
  size inside a model code with NO unit (`CPG 323-300`, `CPG 323-390 left and
  right breasts`, `ERSF – 315Q`). A model code that encodes a size is not a
  documented volume until a captain ruling says it may be decoded - the
  Natrelle `SRM-445` precedent (AGENTS.md) - so those cases publish no
  volume here and are reported, not guessed. A caption that sizes one side
  only, names a side twice, or gives two different unsided figures records
  no volume either, and says why.
- **Profile comes from the clinic's own gloss**, never from the code. The
  glosses are not consistent with the codes (CPG 323 is glossed 'moderate' on
  one case and 'high' on three), which is exactly why the code is not decoded.
  'round low profile' has no schema value and is omitted; 'full volume' is not a
  profile word; 'implants of different sizes and profiles' names none.
- **Brand is never inferred from a style code** (`CPG` is Mentor's, `ERS*`
  Motiva's): the page names no manufacturer, so brand stays unknown.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

import rm_gallery2
import scrape_gallery as sg

BRISBANE_FILE_RE = re.compile(r"_(\d+)([a-d])\.(?:jpe?g|png|webp)$", re.I)
BRISBANE_AGE_RE = re.compile(r"^\s*(\d{2})\s*yrs?\b", re.I)
# '<Right|Left>? <code> <size> cc'. The size is the three digits hard before
# the unit; a style number sits between the code and a hyphen/dash before it
# ('CPG 321-245 cc'), and the Motiva codes run straight into the size
# ('ERSD-285 cc'). Only a figure the clinic wrote 'cc' after is a volume.
BRISBANE_VOLUME_RE = re.compile(
    r"(?:\b(right|left)\b[^0-9]{0,12})?(?:\b\d{3}\s*[-–]\s*)?\b(\d{3})\s*cc\b", re.I)
# The clinic's projection gloss, in brackets after the code.
BRISBANE_PROFILE_RE = re.compile(
    r"\((?:[^()]*?\b)?(high|moderate)\s+profile\)", re.I)
# The two photographs of each pair, by the clinic's filename suffix - the
# protocol verified on all ten cases (module docstring).
BRISBANE_PAIRS = (("ac", "a", "c"), ("bd", "b", "d"))


def brisbane_volumes(caption: str) -> tuple[float | None, float | None, str | None]:
    """(left_cc, right_cc, warning) where the caption writes the unit.

    Anything this cannot read unambiguously - one side named and the other
    not, a side named twice with different figures, or two different unsided
    figures - returns no volume and says why, rather than recording half an
    answer as the whole one (the `bragbook_rev.rev_volumes` rule).
    """
    sides: dict[str, float] = {}
    plain: list[float] = []
    for m in BRISBANE_VOLUME_RE.finditer(caption):
        cc = float(m.group(2))
        if not 100 <= cc <= 1000:
            continue
        if m.group(1):
            side = m.group(1).lower()
            if side in sides and sides[side] != cc:
                return None, None, (f"the {side} breast is named twice with "
                                    f"different figures ({sides[side]:g}cc, {cc:g}cc)")
            sides[side] = cc
        else:
            plain.append(cc)
    if sides:
        if len(sides) == 2:
            return sides["left"], sides["right"], None
        (side, cc), = sides.items()
        return None, None, (f"volume published for the {side} breast only "
                            f"({cc:g}cc); the other side's figure is not "
                            f"attributed in the caption")
    distinct = list(dict.fromkeys(plain))
    if len(distinct) == 1:
        return distinct[0], distinct[0], None
    if len(distinct) > 1:
        return None, None, (f"caption publishes different unsided figures "
                            f"({', '.join(f'{f:g}' for f in distinct)})")
    return None, None, None


def brisbane_profile(caption: str) -> str | None:
    found = {m.group(1).lower() for m in BRISBANE_PROFILE_RE.finditer(caption)}
    return found.pop() if len(found) == 1 else None


def brisbane_cases(listing_html: str) -> list[tuple[list[dict], str]]:
    """(gallery items, caption) per case, in page order.

    Each item is {'url', 'width', 'height', 'label'}. A gallery with no caption
    after it gets '' rather than borrowing the next case's.
    """
    soup = BeautifulSoup(listing_html, "html.parser")
    widgets = soup.select("div.elementor-widget")
    cases: list[tuple[list[dict], str]] = []
    pending: list[dict] | None = None
    for widget in widgets:
        kind = widget.get("data-widget_type", "")
        if kind == "gallery.default":
            if pending is not None:
                cases.append((pending, ""))
            pending = [
                {"url": item.get("data-thumbnail", ""),
                 "width": item.get("data-width", ""),
                 "height": item.get("data-height", ""),
                 "label": item.get("aria-label", "")}
                for item in widget.select("div.e-gallery-image")]
        elif kind == "text-editor.default" and pending is not None:
            cases.append((pending, " ".join(widget.get_text(" ", strip=True).split())))
            pending = None
    if pending is not None:
        cases.append((pending, ""))
    return cases


def brisbane_parse_listing(listing_html: str, source_url: str) -> list[sg.CaseData]:
    """Every published case, pure or not (the wyten rule)."""
    cases: list[sg.CaseData] = []
    for index, (items, caption) in enumerate(brisbane_cases(listing_html), start=1):
        by_letter: dict[str, str] = {}
        numbers: set[str] = set()
        for item in items:
            m = BRISBANE_FILE_RE.search(item["url"])
            if m is None:
                continue
            numbers.add(m.group(1))
            by_letter[m.group(2).lower()] = item["url"]
        single = len(numbers) == 1
        case_id = numbers.pop() if single else f"widget{index}"
        case = sg.CaseData(case_id=case_id, source_url=source_url)
        if not single:
            case.warnings.append(
                "gallery widget's filenames do not carry one case number; "
                "keyed by page position")

        specs = sg.CaseSpecs(summary=caption)
        m = BRISBANE_AGE_RE.match(caption)
        if m:
            specs.age = int(m.group(1))
        specs.left_cc, specs.right_cc, volume_warning = brisbane_volumes(caption)
        sg.classify_brand_shape_profile(specs, caption)
        # Brand is never inferred from a style code, and the shared classifier
        # only fires on a manufacturer's NAME, which this page never prints -
        # but its profile patterns would read a gloss meant for one side as the
        # whole case's, so the clinic's own bracketed gloss replaces them.
        specs.profile = brisbane_profile(caption)
        case.specs = specs
        if not caption:
            case.warnings.append("no caption follows this gallery")
        elif volume_warning:
            case.warnings.append(f"no volume recorded: {volume_warning}")
        # No trailing \b: Motiva's code runs its size into a letter ('315Q').
        elif sg.volume_cc(specs) is None and re.search(r"\b\d{3}", caption):
            case.warnings.append(
                "implant size published only inside a model code, with no unit; "
                "not decoded (the SRM-445 precedent)")

        reason = rm_gallery2.rm_impure_reason(caption)
        if reason:
            case.warnings.append(f"excluded: not a pure augmentation ({reason})")
            cases.append(case)
            continue
        for key, before, after in BRISBANE_PAIRS:
            if before in by_letter and after in by_letter:
                case.pairs.append(sg.ImagePair(
                    key=key, before_url=by_letter[before], after_url=by_letter[after]))
        extra = sorted(set(by_letter) - {"a", "b", "c", "d"})
        if extra or len(by_letter) != 4:
            case.warnings.append(
                f"expected photographs a-d, found {sorted(by_letter)}; "
                "only complete a/c and b/d couples are paired")
        if not case.pairs:
            case.warnings.append("no usable image pairs")
        cases.append(case)
    return cases


def collect(cfg: sg.ClinicConfig, fetcher: sg.PoliteFetcher) -> list[sg.CaseData]:
    """The one listing page; duplicate patients dropped on their pixels."""
    url = sg.gallery_url(cfg, cfg.gallery_paths[0])
    listing = fetcher.get(url, f"{cfg.slug}_listing.html").decode("utf-8", "replace")
    cases = brisbane_parse_listing(listing, url)
    print(f"  {url}: {len(cases)} gallery widget(s)")
    seen_hashes: list = []
    for case in cases:
        sg._drop_duplicate_patient(fetcher, cfg, case, seen_hashes)
    return cases
