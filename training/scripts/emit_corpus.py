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

Every pair is admitted or refused for one recorded reason, and `--report` writes
the full enumeration - one row per staged pair, emitted or not. A row reads
`emit` only once that pair is complete in the finished tree; one still reading
`pending` was decided but never written, so an interrupted run under-states what
landed rather than claiming pairs that are not there. Nothing under `staging/`
is read destructively, moved or deleted: this is a carry-through.

## One clinic per run

`--clinic` selects both which staged pairs are carried and where they go.
`ingest.py` writes a flat staging tree (`<staging>/<pair_id>/`), so
`data/staging` mixes every clinic in `data/raw`; a pair whose id does not carry
the `--clinic` prefix is another clinic's business and is reported as
`other-clinic` rather than emitted under this one. Skipping is never silent -
those pairs get their own row in the report and their own line in the summary,
because a pair that quietly goes missing is the same class of fault as a pair
quietly overwritten. A `--clinic` that matches no staged id at all is operator
error and the run refuses.

## Re-running

Staging is append-only, so the second run over a clinic mostly meets pairs it
already carried. A destination that is byte-identical to the staged pair reads
as `already-emitted` and is not a failure; one that exists and *differs* is a
real conflict, reads as `emit-failed` and is said loudly. Neither is ever
rewritten - the never-overwrite rule is absolute. `--dry-run` consults the
destinations too, so it predicts what the real run will do.

## Retirements

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
"""

# Python >= 3.9 compat: allows PEP 604/585 annotation syntax on older interpreters.
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from censorship import detect_censorship
from ingest import MIN_DIMENSION, validate_meta

DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent / "retired_pairs.json"

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
}

# The withheld classes this stage knows how to honour: registry section -> the
# disposition and quarantine subdirectory a pair in it is given.
REGISTRY_SECTIONS = {
    "retired_laterality": "retired-laterality",
    "withheld_contested": "withheld-contested",
    "retired_watermark": "retired-watermark",
}
REGISTRY_PREAMBLE = ("_comment",)


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


def check_pair(
    folder: Path,
    withheld: dict[str, str],
    quarantine_held: dict[str, str],
    seen_hashes: dict[str, str],
) -> tuple[str, str]:
    """Decide one staged pair. Returns (disposition, detail).

    Disposition is "emit", or the reason it is not emitted. Ordered so that a
    ruling is reported as a ruling: a retired pair reads as retired even if it
    would also have failed a technical gate.
    """
    pair_id = folder.name

    reason = withheld.get(pair_id)
    if reason:
        return reason, "listed in retired_pairs.json"

    if pair_id in quarantine_held:
        return "quarantined", f"held in quarantine as {quarantine_held[pair_id]}"

    try:
        meta = json.loads((folder / "meta.json").read_text())
    except FileNotFoundError:
        return "invalid-meta", "no meta.json"
    except json.JSONDecodeError as e:
        return "invalid-meta", f"meta.json is not valid JSON ({e})"

    errors = validate_meta(meta, folder)
    if errors:
        return "invalid-meta", "; ".join(errors)

    pending: dict[str, str] = {}
    for stem in ("before", "after"):
        src = folder / f"{stem}.jpg"
        if not src.exists():
            return "missing-image", f"no {stem}.jpg"
        digest, size, pixels, carries_exif = read_image(src)
        if min(size) < MIN_DIMENSION:
            return "too-small", f"{stem} is {size[0]}x{size[1]}, floor is {MIN_DIMENSION}"
        if carries_exif:
            return "carries-exif", (
                f"{stem} still carries EXIF - this staging tree did not come "
                "from ingest.py; re-run ingest rather than stripping here"
            )
        if digest in seen_hashes:
            return "duplicate", f"{stem} is a duplicate of {seen_hashes[digest]}"
        marks = detect_censorship(pixels)
        if marks:
            return "censored", f"{stem}: " + "; ".join(marks)
        pending[digest] = f"{pair_id}/{stem}"

    seen_hashes.update(pending)
    return "emit", ""


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
    """Decide an eligible pair against whatever is already in the finished tree."""
    if not dest.exists():
        return "emit", ""
    if pair_matches(folder, dest):
        return "already-emitted", f"{dest} is already this pair, byte for byte"
    return "emit-failed", (
        f"{dest} already exists and differs from the staged pair - refusing to "
        "overwrite a finished corpus pair"
    )


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
    args: argparse.Namespace, decisions: list[tuple[Path, str, str]], rows: list[dict]
) -> int:
    """Copy every decided pair to its destination. Returns the archive-failure count.

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
    for (folder, disposition, _), row in zip(decisions, rows):
        if disposition == "emit":
            try:
                copy_pair(folder, args.corpus / args.clinic / folder.name)
            except OSError as e:
                row["disposition"] = "emit-failed"
                row["detail"] = str(e)
                print(f"FAIL {folder.name}: not emitted - {e}")
            else:
                row["disposition"] = "emit"
            continue

        sub = QUARANTINE_DIRS.get(disposition)
        if not sub or not args.quarantine:
            continue
        dest = args.quarantine / sub / args.clinic / folder.name
        if dest.exists():
            continue
        try:
            copy_pair(folder, dest)
        except OSError as e:
            archive_failures += 1
            row["detail"] = f"{row['detail']} (quarantine copy failed: {e})"
            print(f"FAIL {folder.name}: held but not archived - {e}")
    return archive_failures


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

    withheld = load_registry(args.registry)
    quarantine_held = quarantined_ids(args.quarantine)
    # Every pair directory, not just the ones carrying meta.json: ingest.py
    # copies meta.json last, so an interrupted ingest leaves a pair without one,
    # and that has to read as invalid-meta in the report rather than vanish from
    # the arithmetic.
    pair_folders = sorted(p for p in args.staging.glob("*") if p.is_dir())
    if not pair_folders:
        print(f"No staged pairs under {args.staging} - run ingest.py first")
        return 1

    # Pair ids are '<clinic>-<case>-<view>', so a --clinic that matches none of
    # them is operator error, not a naming variant. Matching up to the separator
    # catches the dropped character as well as the added one; left to run either
    # would quietly open a new clinic subdirectory in the finished tree that
    # nothing can undo, since the never-overwrite guard cannot fire on
    # destinations that are all new.
    prefix = f"{args.clinic}-"
    if not any(folder.name.startswith(prefix) for folder in pair_folders):
        observed = sorted({folder.name.split("-")[0] for folder in pair_folders})
        print(
            f"--clinic '{args.clinic}' matches no staged pair id under {args.staging} "
            f"(observed prefix: {', '.join(observed)}) - refusing to open "
            f"{args.corpus / args.clinic} in the finished corpus tree"
        )
        return 1

    seen_hashes: dict[str, str] = {}
    decisions: list[tuple[Path, str, str]] = []
    rows: list[dict[str, str]] = []
    for folder in pair_folders:
        # ingest.py stages every clinic into one flat tree, so the prefix decides
        # per pair whether this run owns it at all. Another clinic's pair is not
        # a rejection and is never written under this --clinic; it is carried by
        # that clinic's own run.
        if not folder.name.startswith(prefix):
            disposition, detail = "other-clinic", f"not a {args.clinic} pair id"
        else:
            disposition, detail = check_pair(folder, withheld, quarantine_held, seen_hashes)
            if disposition == "emit":
                disposition, detail = emit_state(
                    folder, args.corpus / args.clinic / folder.name
                )

        decisions.append((folder, disposition, detail))
        reported = "pending" if disposition == "emit" and not args.dry_run else disposition
        rows.append({"pair_id": folder.name, "disposition": reported, "detail": detail})
        if disposition == "emit-failed":
            print(f"FAIL {folder.name}: {detail}")
        elif disposition not in ("emit", "other-clinic"):
            print(f"HOLD {folder.name}: {disposition} - {detail}")

    # The corpus tree is the audit surface, so whatever this run writes must be
    # recorded even when it stops early: the report and the summary are written
    # from `rows` in the finally block, and a pair that could not be written is
    # rewritten there as its own disposition rather than raising out of main.
    archive_failures = 0
    try:
        if not args.dry_run:
            archive_failures = write_pairs(args, decisions, rows)
    finally:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["pair_id", "disposition", "detail"])
                writer.writeheader()
                writer.writerows(rows)

        counts: dict[str, int] = {}
        for row in rows:
            counts[row["disposition"]] = counts.get(row["disposition"], 0) + 1
        emitted = counts.get("emit", 0)
        skipped = counts.get("other-clinic", 0)
        failed = counts.get("emit-failed", 0) + archive_failures
        print(f"\n{args.clinic}: {len(pair_folders)} staged")
        for disposition, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {count:4d}  {disposition}")
        where = "would emit" if args.dry_run else f"-> {args.corpus / args.clinic}"
        print(f"{emitted} emitted ({where})")
        if skipped:
            print(f"{skipped} staged pairs skipped as another clinic's - run them under theirs")
        if failed:
            print(f"{failed} failures - see the rows above")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
