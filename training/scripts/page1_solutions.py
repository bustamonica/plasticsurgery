#!/usr/bin/env python3
"""Parser for the Page 1 Solutions before/after gallery platform.

Page 1 Solutions builds the gallery for several consented practices; this
module is the one parser for that family, imported by ``scrape_gallery.py``
and configured per clinic through ``ClinicConfig``. First clinic on it:
``ncps`` (North Coast Plastic Surgery, Dr Gregory Park, Oceanside CA).

Markup contract
---------------

Listing (``/<gallery-root>/<procedure>/`` and ``.../page/N/``):

* One ``div.patient-content`` per case, each opening with an anchor whose text
  is ``Case #<number> - <Procedure>`` and whose href is the case page.
* Pagination is WordPress-style ``/page/N/``; page N+1 past the end is a plain
  404. **The platform publishes no case total**, so the count reconciles
  against pages-until-404 rather than against a declared figure.

Case page:

* ``div.patient-entry`` holds a ``div.single-content`` chart followed by the
  images. The chart is ``<p>`` blocks of ``Label: value`` lines separated by
  ``<br/>``; values often carry a trailing period, and label spelling varies
  on the same site (``Implant Profile:`` and bare ``Profile:``,
  ``Implant Shape:`` and bare ``Shape:``).
* Images are single views - no composites, no grids - in
  ``div.patient-single``, each carrying a sibling ``<span>`` reading exactly
  ``Before`` or ``After``. Pairs are formed positionally from that
  before-then-after order, exactly as the influx/BRAG galleries are.
* Every ``src`` is a WordPress ``-300x300`` derivative; ``page1_full_res()``
  strips the suffix for the bare original.

Two things here are NOT guessable and are why this parser exists:

1. **The case key is the ``Case #<number>`` in the anchor text, not the URL
   slug.** 12 of ncps's 370 cases publish a word slug
   (``ideal-breast-implant-2``, ``gummy-bear-implants-4``, and one whose slug
   is the bare procedure name), and two numeric slugs disagree with their own
   case number (slug ``8901`` is Case #12688). The case numbers are unique
   across the gallery; the slugs are not a stable key.
2. **The purity screen is ``Procedure Type:`` in the chart plus the narrative,
   never the slug.** ncps publishes ``gummy-bear-implants-4`` and
   ``silicone-breast-augmentation`` as slugs on cases whose chart says plain
   Breast Augmentation, and publishes ``Revision Breast Augmentation`` under a
   numeric slug that looks like every other case.

Views are not documented anywhere on the page (the only labels are
Before/After), so every view label comes from the visual-annotation JSON, the
same contract the influx and BRAG clinics use.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import re

from bs4 import BeautifulSoup

# Chart labels this platform publishes. Read as a set of alternatives rather
# than 'split at the first colon' because the same site prints two spellings
# for the same field (`Implant Profile:` / `Profile:`), and because a value can
# itself contain a colon-free parenthetical that a naive split would keep.
# Longest first so `Implant Profile` wins over `Profile`.
PAGE1_FIELD_LABELS = (
    "Gender", "Age", "Height", "Weight", "Months Post-Op", "Procedure Type",
    "Implant Placement", "Incision Site", "Pre-Op Cup Size", "Post-Op Cup Size",
    "Implant Type", "Implant Profile", "Profile", "Implant Shape", "Shape",
    "Implant Surface", "Left Implant Size", "Right Implant Size",
)
PAGE1_LABEL_RE = re.compile(
    r"\b(" + "|".join(re.escape(label) for label in
                      sorted(PAGE1_FIELD_LABELS, key=len, reverse=True))
    + r")\s*:\s*", re.I)

# The chart's narrative heading. Everything under it is the surgeon's prose and
# must not reach classify_placement_incision() - see that function's docstring.
PAGE1_NARRATIVE_HEADINGS = ("doctor's comments", "doctor’s comments")

# WordPress derivative suffix: '...-1of10-300x300.jpg' -> '...-1of10.jpg'.
PAGE1_SIZE_SUFFIX_RE = re.compile(r"-\d+x\d+(?=\.[A-Za-z0-9]+$)")

# Feet/inches on this platform are typed with PRIME and DOUBLE PRIME (5′ 6″),
# which the shared FEET_INCHES_RE does not accept. Normalised here rather than
# by widening that regex, so no other clinic's parsing changes.
PAGE1_PRIMES = {"′": "'", "″": '"', "’": "'", "”": '"'}

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
PAGE1_COMBINED_TERMS = (
    "lift", "mastopexy", "mommy makeover", "tummy tuck", "abdominoplasty",
    "reduction", "reconstruction", "liposuction", "explant",
    "exchange", "capsulectomy", "gynecomastia", "fat transfer", "fat grafting",
)
PAGE1_REVISION_TERMS = ("revision", "re-augmentation", "replacement")

# A narrative that NAMES a procedure has not necessarily reported one: this
# surgeon's prose routinely explains what the patient declined or what was
# merely discussed ("she did not want to have breast lift ... and opted for a
# breast augmentation alone", "different options ... were discussed including
# reduction of the larger breast"). Screening the narrative without this costs
# four pure ncps cases outright, and it is the same trap CLAUDE.md records for
# marina's dual-plane-or-subglandular paragraph. The chart's own
# 'Procedure Type' field is never negated, so this applies to prose only.
PAGE1_NEGATION_MARKERS = (
    "did not", "didn't", "does not", "doesn't", "without", "no need",
    "declined", "instead of", "rather than", "avoid", "opted for",
    "options", "discussed", "considered", "chose not", "would have",
    "cannot", "can't", "unwilling", "refused", "not want", "alone",
)
# Sentence boundary for that negation scope. Kept simple on purpose: the prose
# here is plain clinical narrative with no abbreviations that end in a period.
PAGE1_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


# A labelled 'Implant Profile:' field whose value is the bare projection word
# ('High', 'Moderate') documents a profile as surely as 'High Profile' does -
# the same reasoning the captain applied to a bare number inside a labelled
# implant-size field. Only exact whole-value matches decode, so 'Moderate High'
# (six ncps cases, and not a term in the captain's 2026-08-19 mapping) and
# 'Classic Profile' stay undocumented rather than being guessed into an enum.
PAGE1_BARE_PROFILES = {
    "moderate": "moderate",
    "moderate plus": "moderate-plus",
    "moderate+": "moderate-plus",
    "high": "high",
    "extra high": "extra-high",
    "ultra high": "extra-high",
}
# 'Implant Profile: Moderate+ Left, High Profile Right' - one case, two
# profiles. The schema records one, so a sided field is left undocumented
# rather than resolved to whichever side the regex happened to reach first.
PAGE1_SIDED_PROFILE_RE = re.compile(r"\b(left|right)\b", re.I)


def page1_profile_field(fields: dict[str, str]) -> str:
    """The case's labelled profile value, under either spelling the site uses."""
    return fields.get("Implant Profile", "") or fields.get("Profile", "")


def page1_profile(fields: dict[str, str]) -> tuple[str, bool]:
    """(profile text to classify, is_sided) for a case's labelled profile field.

    An empty string means there is nothing to classify; `is_sided` marks the
    field as documenting a different profile per breast, which the caller must
    leave undocumented.
    """
    value = page1_profile_field(fields)
    if not value:
        return "", False
    if PAGE1_SIDED_PROFILE_RE.search(value):
        return value, True
    return value, False


def page1_bare_profile(value: str) -> str | None:
    """Schema profile for a bare labelled value ('High'), else None."""
    return PAGE1_BARE_PROFILES.get(value.strip().lower().rstrip("."))


def page1_full_res(url: str) -> str:
    """Bare WordPress original for a '-WxH' derivative URL."""
    return PAGE1_SIZE_SUFFIX_RE.sub("", url)


def page1_normalise_primes(text: str) -> str:
    """Typographic primes/quotes to ASCII, so heights parse as 5' 6\"."""
    for prime, ascii_char in PAGE1_PRIMES.items():
        text = text.replace(prime, ascii_char)
    return text


def page1_list_cases(listing_html: str) -> list[tuple[str, str]]:
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


def page1_chart_lines(content) -> list[str]:
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


def page1_split_chart(lines: list[str]) -> tuple[dict[str, str], str]:
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
        if line.strip().lower().rstrip(":") in PAGE1_NARRATIVE_HEADINGS:
            in_narrative = True
            pending_label = None
            continue
        if in_narrative:
            narrative_lines.append(line)
            continue
        matches = list(PAGE1_LABEL_RE.finditer(line))
        if not matches:
            # A label whose value the template pushed onto the next line
            # ('Procedure Type:' then 'Gummy Bear Breast Augmentation.').
            if pending_label is not None:
                fields[pending_label] = _page1_clean_value(line)
                pending_label = None
            continue
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            label = match.group(1).strip()
            value = _page1_clean_value(line[match.end():end])
            if value:
                fields[label] = value
                pending_label = None
            else:
                pending_label = label
    return fields, " ".join(narrative_lines)


def _page1_clean_value(value: str) -> str:
    """Trim the trailing period this platform prints after every chart value."""
    return page1_normalise_primes(value).replace("\xa0", " ").strip().rstrip(".").strip()


def _page1_first_term(text: str) -> str | None:
    """The first impure-procedure term in `text`, prefixed by its class."""
    for term in PAGE1_COMBINED_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", text):
            return f"combined-procedure:{term}"
    for term in PAGE1_REVISION_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", text):
            return f"revision:{term}"
    return None


def page1_purity(fields: dict[str, str], narrative: str) -> str | None:
    """None when the case is a pure primary augmentation, else its rejection class.

    Two screens, and neither alone is sufficient. The chart's own
    'Procedure Type' is the clinic's statement of what was booked and is taken
    at face value. The narrative is where a second procedure that never reached
    the chart shows up ("she underwent removal of her old implants and
    capsulectomy"), so it is screened too - but only sentence by sentence, and
    a sentence carrying a negation or hypothetical marker is not a report of a
    procedure. The URL slug is never read; see this module's docstring.
    """
    chart = _page1_first_term(fields.get("Procedure Type", "").lower())
    if chart is not None:
        return f"chart/{chart}"
    for sentence in PAGE1_SENTENCE_RE.split(narrative.lower()):
        if any(marker in sentence for marker in PAGE1_NEGATION_MARKERS):
            continue
        found = _page1_first_term(sentence)
        if found is not None:
            return f"narrative/{found}"
    return None


def page1_pair_urls(entry) -> list[tuple[str, str]]:
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
            tagged.append((phase, page1_full_res(src)))
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(tagged) - 1:
        if tagged[i][0] == "before" and tagged[i + 1][0] == "after":
            pairs.append((tagged[i][1], tagged[i + 1][1]))
            i += 2
        else:
            i += 1
    return pairs
