#!/usr/bin/env python3
"""Carry staged pairs through to the finished corpus tree.

Input layout:  <staging>/<pair_id>/{before,after}.jpg + meta.json
Output layout: <corpus>/<clinic>/<pair_id>/{before,after}.jpg + meta.json

This is the stage between `ingest.py` and `build_dataset.py`. It is what makes a
pair *count*: a clinic whose pairs never reach `<corpus>/<clinic>/<pair_id>/` is
invisible to every corpus walk, dataset build and audit, however much material
sits in its `staging/` tree. Three clinics - drdanielbarrett, drkolker and
sanantonio - held 745 pairs in staging and nothing in the finished tree, purely
because this step had no script and was done by hand or not at all.

**It performs no de-identification and re-encodes nothing.** Staged images were
already re-encoded by `ingest.py`, which is where EXIF/GPS is stripped; a second
generation of JPEG loss would buy nothing. What this stage does instead is
*verify* that property and refuse to emit an image that still carries EXIF,
which would mean the staging tree did not come from `ingest.py`. There is no
face detection here and none may be added: the consented clinics guarantee no
faces appear in what they publish and the captain holds that assurance (ruling
2026-08-14). See `deidentify.py`'s docstring for what that stage cost when it
existed.

## Stages

`main()` is the composition of six stages, run in this order. Each is owned by
one function, and nothing outside it decides what that stage decides:

| stage       | owner                    | what it owns                                         |
| ----------- | ------------------------ | ---------------------------------------------------- |
| rulings     | `load_rulings`           | retirements, mosaic releases, quarantine holds       |
| selection   | `select_pairs`           | which staged folders this run sees and owns          |
| gates       | `gate_pair`              | one disposition per pair: `PAIR_GATES`, `HALF_GATES` |
| destination | `emit_state`             | what the finished tree already holds for the pair    |
| write       | `write_pairs`            | the copy into the corpus and the quarantine archive  |
| report      | `write_report`           | the per-pair CSV, the summary and the exit code      |

Every pair is admitted or refused for one recorded reason, and `--report` writes
the full enumeration - one row per staged pair, emitted or not. A row reads
`emit` only once that pair is complete in the finished tree; one still reading
`pending` was decided but never written, so an interrupted run under-states what
landed rather than claiming pairs that are not there. Nothing under `staging/`
is read destructively, moved or deleted: this is a carry-through.

Every gate here governs the staging -> finished carry-through and nothing else.
No stage reads the finished tree looking for pairs to withdraw, so none can
retire, re-admit or alter a pair that is already emitted.

## Rulings: retirements (`load_registry`)

`retired_pairs.json` (beside `dataset_schema.json`) enumerates pair ids that
must never reach the finished tree - currently the 176 laterality labels the
captain retired on 2026-08-15, one pair withheld as contested, and all 119
heavenly pairs the captain retired the same day for an on-body watermark that
three clean-up passes each failed to remove. It is an
enumeration and not a rule on purpose: a rule like "every sanantonio lateral" is
a live query over the staging tree, and a later re-annotation would silently
widen or narrow a ruling the captain made over a fixed set of pairs.

The registry is the sole authority on a retirement, and it is keyed by pair id
alone rather than by clinic, so a ruling holds however `--clinic` is spelled.
Deleting an id from the registry is therefore sufficient to let the pair emit
again. It gates this stage and only this stage: an entry keeps a pair from being
carried out of `staging/`, and has no effect on a pair already sitting in the
finished tree - nothing here reads that tree looking for pairs to withdraw, so
removing one takes a change to the corpus tree itself. Do not read a registry
entry as a statement that the pair is absent from every count. Adding a *class*
of withholding is not a data-only edit: a section this stage does not know stops
the run rather than being read as "no retirements" (see `load_registry`).

Retiring is not deleting. The staged pair stays where it is, and `--quarantine`
records a copy under the corpus quarantine convention
(`<quarantine>/<reason>/<clinic>/<pair_id>/`) so the images and their consent
metadata survive with the reason attached. That copy is an archive, not a second
authority: the retirement subdirectories are skipped when the tree is read back
as a hold (see `quarantined_ids`).

The 119 `retired_watermark` heavenly pairs are the one exception to "this stage
is what retires a pair": they were already in the finished tree, not `staging/`,
so this script never touched them - they were moved out of
`<corpus>/heavenly/` into `<quarantine>/retired-watermark/heavenly/` directly,
by hand, once. The registry entry exists only to stop a future re-scrape or
re-stage of heavenly from carrying them back in; see
`~/firstmate/data/ba-viz-emit-backlog/quarantine/MANIFEST.md` for that move. The
same hand-move on 2026-08-16 cleared 63 of those ids out of two non-canonical
duplicate-ingest dumps at the corpus root into
`<quarantine>/retired-watermark-staging-dup/heavenly/` and
`<quarantine>/retired-watermark-corpus-staging-dup/heavenly/`. Those two buckets
are archives of the same registry entry, so they are listed in
`QUARANTINE_DIRS` - not because this stage writes them, but so the registry
stays the sole authority over all 119 rather than 56.

## Rulings: mosaic releases (`load_mosaic_cleared`)

Re-running does NOT undo a mosaic hold: `detect_mosaic` runs again and re-holds
identically. The release is `mosaic_false_positives.json` (`--mosaic-cleared`),
an enumeration of pair ids a human opened and judged false, each carrying the
clinic, the ruling and the evidence it was judged on - see `load_mosaic_cleared`
for the schema, which is required rather than conventional because an entry
without evidence is not reviewable. A missing file means no releases, which is
the safe direction; a malformed one refuses the run, exactly as `load_registry`
refuses rather than reading a broken registry as "no retirements". The release
is as loud as the hold: a released pair reads `mosaic-released` in `--report`
and prints a `RELEASE` line naming the evidence, so it can never be mistaken for
a pair the gate did not flag, and a run's holds and releases are both countable
from the summary. It releases a mosaic hold and nothing else - a listed pair
that also trips censorship, the size floor, the EXIF check or the duplicate
check is still held on that ground, under that disposition, because those are
decided before the mosaic gate is reached. The same ordering puts retirement
ahead of it, so a clearance can never re-admit a retired pair.

That is why the file's first and only entry, `drdanielbarrett-9122-side-right`, is
INERT today. It is a hand-checked false positive and is recorded as one, but the
pair is also retired under `retired_laterality` in `retired_pairs.json`
(2026-08-15), and retirement is decided before any image is opened. The entry
takes effect only if that retirement is lifted; until then no pair on disk is
released by this file, and the mechanism has not been exercised on a real pair.

## Selection: one clinic per run (`select_pairs`)

`--clinic` selects both which staged pairs are carried and where they go.
`ingest.py` writes a flat staging tree (`<staging>/<pair_id>/`), so
`data/staging` mixes every clinic in `data/raw`; a pair whose id does not carry
the `--clinic` prefix is another clinic's business and is reported as
`other-clinic` rather than emitted under this one. Skipping is never silent -
those pairs get their own row in the report and their own line in the summary,
because a pair that quietly goes missing is the same class of fault as a pair
quietly overwritten. A `--clinic` that matches no staged id at all is operator
error and the run refuses.

## Gates (`gate_pair`)

The order of `PAIR_GATES` and `HALF_GATES` is the contract: the first gate to
refuse names the disposition, so a ruling is reported as a ruling - a retired
pair reads as retired even if it would also have failed a technical gate.

### The mosaic gate

`mosaic.py` runs here as well as in `ingest.py`, for exactly the reason the EXIF
check does: a staging tree need not have come from the current `ingest.py`, and
every one on disk was ingested before the gate landed on 2026-08-26. The standing
rule (captain, 2026-08-14) is that mosaic is dropped at ingest AND never emitted,
and this is the second half of it. It keeps its own disposition, `mosaic`, and is
never folded into `censored`: the two gates carry separate measured tables and
have to stay separable in a report and in a future re-measure.

It HOLDS AND REPORTS. Nothing is moved, copied or deleted, `staging/` is left
exactly as it was found, and there is no `QUARANTINE_DIRS` entry. That is
deliberate: of the 16 flags the corpus sweep produced, 8 are real mosaic and 8
are drdanielbarrett's burned-in watermark lettering, so a silent drop here would
destroy consented data through an already-measured false-positive family. The
detail names the half and carries the detector's own box, cell size, cell count
and grid alignment, so the region can be found without re-running anything.

Measured over every staging tree on disk before this shipped - 1,053 pairs across
aips 70, arps 55, drdanielbarrett 298, drkolker 240, sanantonio 207 and swan 183
(`~/firstmate/data/ba-viz-mosaic-detector-adopt/staging-sweep.jsonl`): the gate
holds 8 pairs, all drdanielbarrett and all the watermark family. Its real cost on
today's staging is ZERO pairs. Seven of the eight are already in the finished
tree, which this stage never overwrites, and the eighth,
`drdanielbarrett-9122-side-right`, is retired regardless of what this gate
decides. Every other clinic holds zero - sanantonio's 207 staged pairs included,
which contain the eight mosaicked cases and corroborate `mosaic.py`'s LIMITS from
the other direction. That zero-real-cost figure measures false positives only,
not misses.

This gate's recall is measurably LOWER than `ingest.py`'s, and that is a
measured bound (`mosaic.py` LIMITS item 6). `ingest.py` checks the originally
published pixels, but this stage checks the quality-95 JPEG re-encode that
`ingest.py` wrote to `staging/`, and that re-encode can erase a small-cell
mosaic. Worked example: aips `PA015103_2`, clinic mosaic over the arm.
`detect_mosaic` flags the raw published image (5px cells, 15 of them, grid
alignment 2.46x, box x=632 y=68 25x25px) and finds nothing on the same image
after the re-encode. In an E2E run over base-commit staging, 2 mosaicked aips
pairs gave 1 held and 1 emitted here, while the current `ingest.py` rejects
both. So a clean emit is NOT evidence that a pre-gate staging tree is
mosaic-free. Measuring that exposure is the follow-up
`ba-viz-mosaic-pregate-raw-sweep`, a measure-only sweep of the raw intake of
every clinic whose staging predates the gate, which has not been run.

## Destination: re-running (`emit_state`)

Staging is append-only, so the second run over a clinic mostly meets pairs it
already carried. A destination that is byte-identical to the staged pair reads
as `already-emitted` and is not a failure; one that exists and *differs* is a
real conflict, reads as `emit-failed` and is said loudly. Neither is ever
rewritten - the never-overwrite rule is absolute. `--dry-run` consults the
destinations too, so it predicts what the real run will do.
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Callable, Optional, Tuple

import numpy as np
from PIL import Image

from censorship import detect_censorship
from ingest import MIN_DIMENSION, validate_meta
from mosaic import detect_mosaic

DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent / "retired_pairs.json"
DEFAULT_MOSAIC_CLEARED = (
    Path(__file__).resolve().parent.parent / "mosaic_false_positives.json"
)

# Dispositions that mean "this pair is carried into the finished tree". A
# released mosaic flag emits like any admitted pair but keeps its own name all
# the way to the report, so a release never reads as a pair the gate passed.
EMITTABLE = ("emit", "mosaic-released")

# Quarantine subdirectory per retirement class, matching the existing tree at
# ~/firstmate/data/ba-viz-emit-backlog/quarantine/ (deidentify-blur-damage/,
# view-mislabel/). Doubles as the set of buckets that are archives of a ruling
# rather than holds in their own right, which is why the two heavenly
# duplicate-dump buckets are listed here even though this stage never writes
# them: their pairs are already named by `retired_pairs.json`, and reading them
# back as unconditional holds would put 63 of the 119 beyond the registry edit
# that is meant to undo the ruling.
QUARANTINE_DIRS = {
    "retired-laterality": "retired-laterality",
    "withheld-contested": "withheld-contested",
    "retired-watermark": "retired-watermark",
    "retired-watermark-staging-dup": "retired-watermark-staging-dup",
    "retired-watermark-corpus-staging-dup": "retired-watermark-corpus-staging-dup",
    "retired-combined-procedure": "retired-combined-procedure",
}

# The withheld classes this stage knows how to honour: registry section -> the
# disposition and quarantine subdirectory a pair in it is given.
REGISTRY_SECTIONS = {
    "retired_laterality": "retired-laterality",
    "withheld_contested": "withheld-contested",
    "retired_watermark": "retired-watermark",
    "retired_combined_procedure": "retired-combined-procedure",
}
REGISTRY_PREAMBLE = ("_comment",)


# ---------------------------------------------------------------------------
# Stage 1: rulings
# ---------------------------------------------------------------------------


def load_registry(path: Path) -> dict[str, str]:
    """Map pair_id -> reason for every pair the registry withholds.

    Keyed by pair id and not by clinic: the ids are globally unique and already
    clinic-prefixed, so a misspelled or variant `--clinic` cannot quietly let a
    retired pair through. A duplicate id would make that assumption false, so it
    is refused here rather than silently collapsed.

    Fail-closed on a registry this stage cannot fully read. Both known sections
    must be present with their `pairs` mapping, and an unrecognised top-level
    section stops the run: reading it as "no retirements" would emit the very
    pairs a ruling names, into a tree nothing can undo. The cost, taken
    deliberately, is that adding a withheld class is a code change here and not
    a data-only edit.
    """
    data = json.loads(path.read_text())

    unknown = sorted(set(data) - set(REGISTRY_SECTIONS) - set(REGISTRY_PREAMBLE))
    if unknown:
        raise ValueError(
            f"{path}: unrecognised section(s) {', '.join(unknown)}. This stage only "
            f"honours {', '.join(REGISTRY_SECTIONS)}; a new withheld class has to be "
            "added to REGISTRY_SECTIONS in emit_corpus.py (and given a quarantine "
            "subdirectory in QUARANTINE_DIRS) before its pairs are withheld. Refusing "
            "to run rather than emit pairs a ruling names."
        )

    withheld: dict[str, str] = {}
    for key, reason in REGISTRY_SECTIONS.items():
        section = data.get(key)
        if not isinstance(section, dict) or not isinstance(section.get("pairs"), dict):
            raise ValueError(
                f"{path}: section '{key}' is missing or carries no 'pairs' mapping. "
                "Every known withheld class must be readable before this stage can "
                "gate on the registry at all."
            )
        for pairs in section["pairs"].values():
            for pair_id in pairs:
                if pair_id in withheld:
                    raise ValueError(
                        f"{path}: {pair_id} is listed twice - pair ids must be unique "
                        "for a ruling to be unambiguous"
                    )
                withheld[pair_id] = reason
    return withheld


MOSAIC_CLEARED_FIELDS = ("clinic", "ruling", "evidence")


def load_mosaic_cleared(path: Path) -> dict[str, str]:
    """Map pair_id -> evidence for every mosaic flag a human judged false.

    The mosaic gate is measured wrong about half the times it fires, so it needs
    a release or it is a one-way ratchet that loses consented data every time it
    is wrong. This is that release, and it is deliberately the narrowest thing
    that works: an enumeration of pair ids, never a rule or a pattern, for the
    same reason `retired_pairs.json` is enumerated - a rule is a live query over
    a staging tree, so a later re-annotation could widen a judgement made over a
    fixed set of pairs. It releases a `mosaic` hold and nothing else; every other
    gate is decided before the mosaic gate and still binds.

    Every entry must carry all of `MOSAIC_CLEARED_FIELDS`, and the pair id must
    agree with the entry's own clinic. An entry without evidence is not
    reviewable, so the schema requires it rather than trusting convention.

    The two failure modes are deliberately different. A MISSING file means no
    releases: the gate holds, which is the direction that cannot lose data. A
    MALFORMED file REFUSES the run rather than being read as an empty allow-list
    - the same discipline `load_registry` applies in the opposite direction,
    because silently ignoring a file someone edited is how a judgement goes
    missing.
    """
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(
            f"{path}: not valid JSON ({e}). Refusing to run rather than read an "
            "unreadable allow-list as 'nothing is cleared', which would hold "
            "pairs a human has already judged."
        ) from e

    unknown = sorted(set(data) - {"cleared"} - set(REGISTRY_PREAMBLE))
    if unknown:
        raise ValueError(
            f"{path}: unrecognised section(s) {', '.join(unknown)}. This file holds "
            "one section, 'cleared'; it is not a general override and must not grow "
            "one by accident."
        )

    cleared = data.get("cleared")
    if not isinstance(cleared, dict):
        raise ValueError(
            f"{path}: section 'cleared' is missing or is not a mapping of pair id to "
            "entry. Delete the file to mean 'nothing is cleared'; an unreadable one "
            "stops the run."
        )

    releases: dict[str, str] = {}
    for pair_id, entry in cleared.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: {pair_id} is not an entry object")
        missing = [f for f in MOSAIC_CLEARED_FIELDS if not str(entry.get(f, "")).strip()]
        if missing:
            raise ValueError(
                f"{path}: {pair_id} is missing {', '.join(missing)}. Every release "
                "records the clinic, the ruling and what was actually looked at - an "
                "entry without those cannot be reviewed and is not honoured."
            )
        if not pair_id.startswith(f"{entry['clinic']}-"):
            raise ValueError(
                f"{path}: {pair_id} does not belong to clinic '{entry['clinic']}'. "
                "The id is the key, so a mismatch is a typo in one of the two and "
                "could release a pair nobody judged."
            )
        releases[pair_id] = entry["evidence"]
    return releases


def quarantined_ids(quarantine: Path | None) -> dict[str, str]:
    """Map pair_id -> quarantine reason for everything already in the tree.

    The corpus quarantine is <quarantine>/<reason>/<clinic>/<pair_id>/. A pair
    held there has been withdrawn from training by an earlier ruling and must
    not be re-emitted by this stage on its own initiative.

    The retirement subdirectories (`QUARANTINE_DIRS`) are the exception, and are
    skipped: those are governed by `retired_pairs.json`, so reading them back as
    a hold would double-count the same ruling and make it irreversible by the
    registry edit that is meant to undo it. Every other subdirectory holds
    unconditionally.
    """
    if quarantine is None or not quarantine.exists():
        return {}
    archived = set(QUARANTINE_DIRS.values())
    held = {}
    for pair_dir in quarantine.glob("*/*/*"):
        reason = pair_dir.parent.parent.name
        if reason in archived or not pair_dir.is_dir():
            continue
        held[pair_dir.name] = reason
    return held


@dataclass(frozen=True)
class Rulings:
    """Every human decision this run honours, read once before any pair is seen.

    `withheld` is `retired_pairs.json` (pair id -> retirement disposition),
    `mosaic_cleared` is `mosaic_false_positives.json` (pair id -> evidence) and
    `quarantine_held` is the quarantine tree read back as holds (pair id ->
    bucket). All three are keyed by pair id alone, never by clinic.
    """

    withheld: dict[str, str]
    mosaic_cleared: dict[str, str]
    quarantine_held: dict[str, str]


def load_rulings(registry: Path, mosaic_cleared: Path, quarantine: Path | None) -> Rulings:
    """Stage 1. Read every ruling, refusing on any file this stage cannot fully read."""
    return Rulings(
        withheld=load_registry(registry),
        mosaic_cleared=load_mosaic_cleared(mosaic_cleared),
        quarantine_held=quarantined_ids(quarantine),
    )


# ---------------------------------------------------------------------------
# Stage 2: selection
# ---------------------------------------------------------------------------


def select_pairs(staging: Path, clinic: str, corpus: Path) -> list[Path] | None:
    """Stage 2. Every staged pair folder, or None (after saying why) to refuse.

    Every pair directory, not just the ones carrying meta.json: ingest.py
    copies meta.json last, so an interrupted ingest leaves a pair without one,
    and that has to read as invalid-meta in the report rather than vanish from
    the arithmetic.

    Pair ids are '<clinic>-<case>-<view>', so a --clinic that matches none of
    them is operator error, not a naming variant. Matching up to the separator
    catches the dropped character as well as the added one; left to run either
    would quietly open a new clinic subdirectory in the finished tree that
    nothing can undo, since the never-overwrite guard cannot fire on
    destinations that are all new.
    """
    pair_folders = sorted(p for p in staging.glob("*") if p.is_dir())
    if not pair_folders:
        print(f"No staged pairs under {staging} - run ingest.py first")
        return None

    if not any(owns_pair(folder, clinic) for folder in pair_folders):
        observed = sorted({folder.name.split("-")[0] for folder in pair_folders})
        print(
            f"--clinic '{clinic}' matches no staged pair id under {staging} "
            f"(observed prefix: {', '.join(observed)}) - refusing to open "
            f"{corpus / clinic} in the finished corpus tree"
        )
        return None
    return pair_folders


def owns_pair(folder: Path, clinic: str) -> bool:
    """Whether this run's --clinic owns a staged pair at all.

    ingest.py stages every clinic into one flat tree, so the prefix decides per
    pair whether this run owns it. Another clinic's pair is not a rejection and
    is never written under this --clinic; it is carried by that clinic's own
    run.
    """
    return folder.name.startswith(f"{clinic}-")


# ---------------------------------------------------------------------------
# Stage 3: gates
# ---------------------------------------------------------------------------

# A gate returns None to pass the pair on, or (disposition, detail) to stop it.
Refusal = Optional[Tuple[str, str]]


def read_image(path: Path) -> tuple[str, tuple[int, int], np.ndarray, bool]:
    """(pixel hash, size, BGR pixels, carries_exif) for a staged image.

    The array is BGR because that is the layout `censorship.py` expects. The
    file is not rewritten - this stage only reads.
    """
    with Image.open(path) as img:
        carries_exif = bool(img.getexif())
        rgb = img.convert("RGB")
        bgr = np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])
        return hashlib.sha256(rgb.tobytes()).hexdigest(), rgb.size, bgr, carries_exif


@dataclass
class Half:
    """One staged image of a pair, read at most once and only when a gate asks."""

    stem: str
    path: Path

    @cached_property
    def _read(self) -> tuple[str, tuple[int, int], np.ndarray, bool]:
        return read_image(self.path)

    @property
    def digest(self) -> str:
        return self._read[0]

    @property
    def size(self) -> tuple[int, int]:
        return self._read[1]

    @property
    def pixels(self) -> np.ndarray:
        return self._read[2]

    @property
    def carries_exif(self) -> bool:
        return self._read[3]


@dataclass
class Candidate:
    """One staged pair on its way through the gates.

    `seen_hashes` is the run's pixel-hash -> '<pair_id>/<stem>' record, shared
    across pairs. A pair's own hashes collect in `pending` and are committed to
    it only once both halves have passed every gate, so a pair refused on its
    after half never shadows a later pair carrying its before half.
    """

    folder: Path
    rulings: Rulings
    seen_hashes: dict[str, str]
    meta: dict = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)
    released: list[str] = field(default_factory=list)

    @property
    def pair_id(self) -> str:
        return self.folder.name


def retirement_gate(c: Candidate) -> Refusal:
    """A pair `retired_pairs.json` names reads as its ruling, whatever else it is."""
    reason = c.rulings.withheld.get(c.pair_id)
    if reason:
        return reason, "listed in retired_pairs.json"
    return None


def quarantine_gate(c: Candidate) -> Refusal:
    """A pair held in the quarantine tree by an earlier ruling is not re-emitted."""
    if c.pair_id in c.rulings.quarantine_held:
        return "quarantined", f"held in quarantine as {c.rulings.quarantine_held[c.pair_id]}"
    return None


def meta_gate(c: Candidate) -> Refusal:
    """The pair's meta.json exists, parses and passes ingest.py's schema check."""
    try:
        c.meta = json.loads((c.folder / "meta.json").read_text())
    except FileNotFoundError:
        return "invalid-meta", "no meta.json"
    except json.JSONDecodeError as e:
        return "invalid-meta", f"meta.json is not valid JSON ({e})"

    errors = validate_meta(c.meta, c.folder)
    if errors:
        return "invalid-meta", "; ".join(errors)
    return None


def missing_image_gate(c: Candidate, half: Half) -> Refusal:
    if not half.path.exists():
        return "missing-image", f"no {half.stem}.jpg"
    return None


def size_floor_gate(c: Candidate, half: Half) -> Refusal:
    if min(half.size) < MIN_DIMENSION:
        return "too-small", (
            f"{half.stem} is {half.size[0]}x{half.size[1]}, floor is {MIN_DIMENSION}"
        )
    return None


def exif_gate(c: Candidate, half: Half) -> Refusal:
    if half.carries_exif:
        return "carries-exif", (
            f"{half.stem} still carries EXIF - this staging tree did not come "
            "from ingest.py; re-run ingest rather than stripping here"
        )
    return None


def duplicate_gate(c: Candidate, half: Half) -> Refusal:
    """Pixels already carried by an earlier pair of this run."""
    if half.digest in c.seen_hashes:
        return "duplicate", f"{half.stem} is a duplicate of {c.seen_hashes[half.digest]}"
    return None


def censorship_gate(c: Candidate, half: Half) -> Refusal:
    marks = detect_censorship(half.pixels)
    if marks:
        return "censored", f"{half.stem}: " + "; ".join(marks)
    return None


def mosaic_gate(c: Candidate, half: Half) -> Refusal:
    """Holds a mosaic flag unless `mosaic_false_positives.json` releases the pair.

    A release is recorded rather than passed silently, so the pair reaches the
    report as `mosaic-released` and never as a pair this gate did not flag. It
    is the last gate on purpose: every other ground for holding the pair is
    decided before a release can be consulted.
    """
    blocks = detect_mosaic(half.pixels)
    if blocks:
        evidence = c.rulings.mosaic_cleared.get(c.pair_id)
        if evidence is None:
            return "mosaic", f"{half.stem}: " + "; ".join(blocks)
        c.released.append(f"{half.stem}: " + "; ".join(blocks))
    return None


def images_gate(c: Candidate) -> Refusal:
    """Run `HALF_GATES` over the before half fully, then the after half."""
    for stem in ("before", "after"):
        half = Half(stem, c.folder / f"{stem}.jpg")
        for gate in HALF_GATES:
            refusal = gate(c, half)
            if refusal is not None:
                return refusal
        c.pending[half.digest] = f"{c.pair_id}/{stem}"
    return None


# The order is the contract: the first refusal names the disposition. Rulings
# come first so a ruling is reported as a ruling, and within a half the mosaic
# gate comes last so a release can only ever lift a mosaic hold.
PAIR_GATES: tuple[Callable[[Candidate], Refusal], ...] = (
    retirement_gate,
    quarantine_gate,
    meta_gate,
    images_gate,
)
HALF_GATES: tuple[Callable[[Candidate, Half], Refusal], ...] = (
    missing_image_gate,
    size_floor_gate,
    exif_gate,
    duplicate_gate,
    censorship_gate,
    mosaic_gate,
)


def gate_pair(folder: Path, rulings: Rulings, seen_hashes: dict[str, str]) -> tuple[str, str]:
    """Stage 3. Decide one staged pair. Returns (disposition, detail).

    Disposition is "emit", "mosaic-released", or the reason it is not emitted.
    """
    c = Candidate(folder, rulings, seen_hashes)
    for gate in PAIR_GATES:
        refusal = gate(c)
        if refusal is not None:
            return refusal

    seen_hashes.update(c.pending)
    if c.released:
        return "mosaic-released", (
            "; ".join(c.released)
            + f" - released as a false positive: {rulings.mosaic_cleared[c.pair_id]}"
        )
    return "emit", ""


# ---------------------------------------------------------------------------
# Stage 4: destination
# ---------------------------------------------------------------------------

PAIR_FILES = ("before.jpg", "after.jpg", "meta.json")


def pair_matches(folder: Path, dest: Path) -> bool:
    """True when the finished pair carries the staged pair's bytes.

    All three files must be present and byte-identical; a missing half or a
    changed byte is a conflict rather than a re-run. Anything *else* in the
    destination directory is ignored: this recognition exists to let a
    legitimate re-run succeed, so it must not be defeated by a `.DS_Store`
    Finder drops into a pair directory - that would report real corruption where
    there is none. The cost, taken knowingly, is that an unexpected extra file
    in a finished pair is not this script's to surface.

    Matching the images by stem recognises the SAME BYTES filed under any
    extension, nothing more. A finished pair genuinely stored in another format
    (`.png`, `.webp`, or a re-encoded `.jpeg`) is a different encoding of the
    photograph and cannot be byte-identical to the staged JPEG, so re-emitting
    one reports as differing rather than as already emitted - deliberately, since
    byte equality is the only check here that catches real corruption.
    """
    for stem in ("before", "after"):
        found = [p for p in dest.glob(f"{stem}.*") if p.is_file()]
        if len(found) != 1 or found[0].read_bytes() != (folder / f"{stem}.jpg").read_bytes():
            return False
    finished_meta = dest / "meta.json"
    return (
        finished_meta.is_file()
        and finished_meta.read_bytes() == (folder / "meta.json").read_bytes()
    )


def emit_state(folder: Path, dest: Path) -> tuple[str, str]:
    """Stage 4. Decide an eligible pair against whatever is already in the finished tree."""
    if not dest.exists():
        return "emit", ""
    if pair_matches(folder, dest):
        return "already-emitted", f"{dest} is already this pair, byte for byte"
    return "emit-failed", (
        f"{dest} already exists and differs from the staged pair - refusing to "
        "overwrite a finished corpus pair"
    )


@dataclass
class Decision:
    """One staged pair's outcome, and the report row that records it."""

    folder: Path
    disposition: str
    detail: str
    row: dict[str, str]


def decide(
    pair_folders: list[Path], clinic: str, corpus: Path, rulings: Rulings, dry_run: bool
) -> list[Decision]:
    """Selection, gates and destination for every staged pair, in folder order.

    The destination has the last word, but only when it has one to say: a
    clash or a byte-identical re-run replaces the disposition, while a clean
    emit leaves `mosaic-released` standing so the release survives into the
    report. An emittable row reads `pending` until `write_pairs` lands it.
    """
    seen_hashes: dict[str, str] = {}
    decisions: list[Decision] = []
    for folder in pair_folders:
        if not owns_pair(folder, clinic):
            disposition, detail = "other-clinic", f"not a {clinic} pair id"
        else:
            disposition, detail = gate_pair(folder, rulings, seen_hashes)
            if disposition in EMITTABLE:
                state, state_detail = emit_state(folder, corpus / clinic / folder.name)
                if state != "emit":
                    disposition, detail = state, state_detail

        reported = "pending" if disposition in EMITTABLE and not dry_run else disposition
        decisions.append(Decision(
            folder, disposition, detail,
            {"pair_id": folder.name, "disposition": reported, "detail": detail},
        ))
        if disposition == "emit-failed":
            print(f"FAIL {folder.name}: {detail}")
        elif disposition == "mosaic-released":
            print(f"RELEASE {folder.name}: mosaic flag judged false - {detail}")
        elif disposition not in EMITTABLE + ("other-clinic",):
            print(f"HOLD {folder.name}: {disposition} - {detail}")
    return decisions


# ---------------------------------------------------------------------------
# Stage 5: write
# ---------------------------------------------------------------------------


def copy_pair(folder: Path, dest: Path) -> None:
    """Copy a pair verbatim. Never overwrites: an existing destination aborts.

    The pair lands whole or not at all. `mkdir()` without `exist_ok` succeeding
    proves the directory did not exist before this call, so tearing it down when
    a copy fails part-way can only remove what this call itself wrote - the
    finished corpus tree is never left holding half a pair, which a walk over
    pair directories would read as a real one.
    """
    if dest.exists():
        raise FileExistsError(
            f"{dest} already exists - refusing to overwrite a finished corpus pair"
        )
    dest.mkdir(parents=True)
    try:
        for name in PAIR_FILES:
            shutil.copyfile(folder / name, dest / name)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def write_pairs(
    decisions: list[Decision], corpus: Path, clinic: str, quarantine: Path | None
) -> int:
    """Stage 5. Copy every decided pair to its destination. Returns the archive-failure count.

    A row reads "emit" only once that pair is complete on disk: a run that stops
    part-way - a clash, an IO error, a Ctrl-C - leaves a report that under-states
    what landed rather than claiming pairs that are not there. The
    never-overwrite rule is absolute: a clash costs that one pair, never the
    pair already in the finished tree.

    A pair that could not be emitted says so on its own row, so only the
    quarantine archive - which keeps the ruling as its disposition - has a
    failure to count here.
    """
    archive_failures = 0
    for d in decisions:
        if d.disposition in EMITTABLE:
            try:
                copy_pair(d.folder, corpus / clinic / d.folder.name)
            except OSError as e:
                d.row["disposition"] = "emit-failed"
                d.row["detail"] = str(e)
                print(f"FAIL {d.folder.name}: not emitted - {e}")
            else:
                d.row["disposition"] = d.disposition
            continue

        sub = QUARANTINE_DIRS.get(d.disposition)
        if not sub or not quarantine:
            continue
        dest = quarantine / sub / clinic / d.folder.name
        if dest.exists():
            continue
        try:
            copy_pair(d.folder, dest)
        except OSError as e:
            archive_failures += 1
            d.row["detail"] = f"{d.row['detail']} (quarantine copy failed: {e})"
            print(f"FAIL {d.folder.name}: held but not archived - {e}")
    return archive_failures


# ---------------------------------------------------------------------------
# Stage 6: report
# ---------------------------------------------------------------------------


def write_report(
    rows: list[dict[str, str]],
    report: Path | None,
    clinic: str,
    corpus: Path,
    staged: int,
    dry_run: bool,
    archive_failures: int,
) -> int:
    """Stage 6. Write the per-pair CSV and print the summary. Returns the failure count."""
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["pair_id", "disposition", "detail"])
            writer.writeheader()
            writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["disposition"]] = counts.get(row["disposition"], 0) + 1
    emitted = sum(counts.get(d, 0) for d in EMITTABLE)
    skipped = counts.get("other-clinic", 0)
    failed = counts.get("emit-failed", 0) + archive_failures
    print(f"\n{clinic}: {staged} staged")
    for disposition, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4d}  {disposition}")
    where = "would emit" if dry_run else f"-> {corpus / clinic}"
    print(f"{emitted} emitted ({where})")
    if skipped:
        print(f"{skipped} staged pairs skipped as another clinic's - run them under theirs")
    if failed:
        print(f"{failed} failures - see the rows above")
    return failed


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging", type=Path, help="Staging directory to carry pairs from")
    parser.add_argument("corpus", type=Path, help="Root of the finished corpus tree")
    parser.add_argument(
        "--clinic",
        required=True,
        help="Clinic name: carries only staged pairs whose id starts with it, "
        "into that subdirectory of the corpus",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help="retired_pairs.json (default: beside dataset_schema.json)",
    )
    parser.add_argument(
        "--mosaic-cleared",
        type=Path,
        default=DEFAULT_MOSAIC_CLEARED,
        help="mosaic_false_positives.json: mosaic flags a human judged false. "
        "Releases a mosaic hold and nothing else (default: beside dataset_schema.json)",
    )
    parser.add_argument(
        "--quarantine",
        type=Path,
        default=None,
        help="Corpus quarantine tree: pairs already held there are not emitted, "
        "and pairs retired by this run are copied into it",
    )
    parser.add_argument("--report", type=Path, default=None, help="Write a per-pair CSV here")
    parser.add_argument(
        "--dry-run", action="store_true", help="Decide every pair but write nothing"
    )
    args = parser.parse_args(argv)

    rulings = load_rulings(args.registry, args.mosaic_cleared, args.quarantine)
    pair_folders = select_pairs(args.staging, args.clinic, args.corpus)
    if pair_folders is None:
        return 1
    decisions = decide(pair_folders, args.clinic, args.corpus, rulings, args.dry_run)

    # The corpus tree is the audit surface, so whatever this run writes must be
    # recorded even when it stops early: the report and the summary are written
    # from the rows in the finally block, and a pair that could not be written
    # is rewritten there as its own disposition rather than raising out of main.
    archive_failures = 0
    try:
        if not args.dry_run:
            archive_failures = write_pairs(
                decisions, args.corpus, args.clinic, args.quarantine
            )
    finally:
        failed = write_report(
            [d.row for d in decisions], args.report, args.clinic, args.corpus,
            len(pair_folders), args.dry_run, archive_failures,
        )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
