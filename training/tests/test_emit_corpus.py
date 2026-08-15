"""The staging -> finished-corpus carry-through (emit_corpus.py).

Two things are under test and they are different in kind.

The first is the emit stage's behaviour, exercised on synthetic torsos in
tmp_path - no patient imagery is committed, ever.

The second is `retired_pairs.json` itself. It is data, but it encodes a captain
ruling over a fixed set of 176 pairs, and the acceptance criterion for this
corpus is that not one of them reaches the finished tree. So the enumeration is
asserted directly - count, shape and the four named exceptions - and then
asserted again through the emit stage, which is the only thing that can put a
pair in the corpus.
"""

import csv
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import emit_corpus
from conftest import make_torso
from ingest import MIN_DIMENSION, VALID_VIEWS

REGISTRY = Path(__file__).resolve().parent.parent / "retired_pairs.json"
CALIBRATED = {
    "sanantonio-23999-oblique-right",
    "sanantonio-24007-oblique-right",
    "sanantonio-24034-oblique-right",
    "sanantonio-24169-oblique-right",
}


@pytest.fixture()
def registry() -> dict:
    return json.loads(REGISTRY.read_text())


@pytest.fixture()
def make_staged(tmp_path):
    """Factory: write a staging-layout pair (<staging>/<pair_id>/) as ingest would."""

    def _make(
        pair_id: str,
        staging: Path = None,
        view: str = "front",
        size: tuple[int, int] = (768, 1024),
        images: tuple = None,
        meta_overrides: dict = None,
        exif: bool = False,
        seed: int = 7,
    ) -> Path:
        folder = (staging or tmp_path / "staging") / pair_id
        folder.mkdir(parents=True, exist_ok=True)
        for stem, offset in (("before", 0), ("after", 11)):
            bgr = images[offset > 0] if images else make_torso(size=size, seed=seed + offset)
            img = Image.fromarray(bgr[:, :, ::-1])
            if exif:
                stamp = Image.Exif()
                stamp[271] = "Canon"  # Make
                img.save(folder / f"{stem}.jpg", "JPEG", quality=95, exif=stamp)
            else:
                img.save(folder / f"{stem}.jpg", "JPEG", quality=95)
        meta = {
            "pair_id": pair_id,
            "view": view,
            "shape": "unknown",
            "volume_cc": 350,
            "consent_ref": "clinic-agreement-2026-08",
            "clothing": "nude",
        }
        meta.update(meta_overrides or {})
        (folder / "meta.json").write_text(json.dumps(meta))
        return folder

    return _make


def run(tmp_path, clinic="clinic01", quarantine=None, extra=(), staging=None, report=None):
    argv = [
        str(staging or tmp_path / "staging"),
        str(tmp_path / "corpus"),
        "--clinic",
        clinic,
        "--report",
        str(report or tmp_path / "report.csv"),
    ]
    if quarantine is not None:
        argv += ["--quarantine", str(quarantine)]
    return emit_corpus.main(argv + list(extra))


def view_of(pair_id: str) -> str:
    """The pair id's own view, by suffix rather than by segment position.

    Some ids carry an extra case segment (`sanantonio-23818-2-side-right`), so
    counting segments would build metadata the ingest schema rejects and the
    fixture would no longer be an otherwise-emittable pair.
    """
    return next(view for view in VALID_VIEWS if pair_id.endswith(f"-{view}"))


def dispositions(tmp_path, report=None) -> dict[str, str]:
    with (report or tmp_path / "report.csv").open() as fh:
        return {row["pair_id"]: row["disposition"] for row in csv.DictReader(fh)}


# --- The retirement enumeration -------------------------------------------


class TestRetirementRegistry:
    def test_retires_exactly_the_176_pairs_the_captain_ruled_on(self, registry):
        pairs = registry["retired_laterality"]["pairs"]
        assert len(pairs["sanantonio"]) == 134
        assert len(pairs["drdanielbarrett"]) == 42
        assert sum(len(v) for v in pairs.values()) == 176

    def test_no_front_pair_is_ever_retired(self, registry):
        # Both clinics' front views are unaffected by the laterality error and
        # are the bulk of what these clinics contribute.
        for clinic_pairs in registry["retired_laterality"]["pairs"].values():
            assert not [p for p in clinic_pairs if p.endswith("-front")]

    def test_sanantonio_retires_only_laterals(self, registry):
        pairs = registry["retired_laterality"]["pairs"]["sanantonio"]
        assert all(p.endswith(("oblique-left", "oblique-right", "side-left", "side-right"))
                   for p in pairs)

    def test_drdanielbarrett_retires_only_right_labels(self, registry):
        # The clinic only ever photographs one side, so '-right' is the error.
        pairs = registry["retired_laterality"]["pairs"]["drdanielbarrett"]
        assert all(p.endswith(("oblique-right", "side-right")) for p in pairs)

    def test_the_four_calibrated_sanantonio_pairs_are_not_retired(self, registry):
        # They were re-annotated after an explicit calibration pass and sit
        # outside the ruling; three were confirmed by landmark in the audit.
        retired = set(registry["retired_laterality"]["pairs"]["sanantonio"])
        assert not (CALIBRATED & retired)

    def test_the_contested_calibrated_pair_is_withheld_separately(self, registry):
        # The audit called 24007 'contested - unknown' and said in terms that
        # its three confirmations do not clear the fourth. Withheld, not
        # retired: it is our call, not the captain's ruling.
        withheld = registry["withheld_contested"]["pairs"]["sanantonio"]
        assert withheld == ["sanantonio-24007-oblique-right"]
        assert "sanantonio-24007-oblique-right" not in set(
            registry["retired_laterality"]["pairs"]["sanantonio"]
        )

    def test_every_withheld_pair_carries_a_reason(self, registry):
        for section in ("retired_laterality", "withheld_contested"):
            assert registry[section]["why"].strip()
            assert registry[section]["ruling"].strip()

    def test_no_pair_id_appears_twice(self, registry):
        ids = [
            p
            for section in ("retired_laterality", "withheld_contested")
            for pairs in registry[section]["pairs"].values()
            for p in pairs
        ]
        assert len(ids) == len(set(ids))


class TestRetiredPairsNeverEmit:
    """The acceptance criterion, exercised through the only code that can emit."""

    @pytest.mark.parametrize(
        "pair_id",
        [
            "sanantonio-24021-oblique-right",
            "sanantonio-24143-side-left",
            "drdanielbarrett-34421-oblique-right",
            "drdanielbarrett-98732-side-right",
        ],
    )
    def test_a_retired_pair_is_held_back(self, tmp_path, make_staged, pair_id):
        clinic = pair_id.split("-")[0]
        make_staged(pair_id)
        make_staged(f"{clinic}-99999-front")
        run(tmp_path, clinic=clinic)
        assert not (tmp_path / "corpus" / clinic / pair_id).exists()
        assert dispositions(tmp_path)[pair_id] == "retired-laterality"

    def test_the_whole_ruling_is_held_back_at_once(self, tmp_path, make_staged, registry):
        """Every one of the 176 retired ids, staged as an otherwise-emittable pair.

        Each carries valid metadata over distinct images above the size floor, so
        the retirement gate is the only thing that can hold it back: delete that
        check and every one of them emits. Each clinic gets its own staging tree,
        so neither run is deciding the other's pairs.
        """
        for clinic, pairs in registry["retired_laterality"]["pairs"].items():
            staging = tmp_path / f"staging-{clinic}"
            report = tmp_path / f"report-{clinic}.csv"
            for index, pair_id in enumerate(pairs):
                make_staged(
                    pair_id,
                    staging=staging,
                    view=view_of(pair_id),
                    size=(MIN_DIMENSION, MIN_DIMENSION),
                    seed=1000 + index * 100,
                )
            control = f"{clinic}-00000-front"
            make_staged(control, staging=staging, size=(MIN_DIMENSION, MIN_DIMENSION), seed=1)

            run(tmp_path, clinic=clinic, staging=staging, report=report)

            decided = dispositions(tmp_path, report=report)
            assert not {p: decided[p] for p in pairs if decided[p] != "retired-laterality"}
            assert decided[control] == "emit"
            assert {p.name for p in (tmp_path / "corpus" / clinic).iterdir()} == {control}

    def test_a_retired_pair_reads_as_retired_even_if_it_would_also_fail_a_gate(
        self, tmp_path, make_staged
    ):
        # drdanielbarrett-90631-side-right is both retired and censorship-flagged
        # on the real corpus. A ruling must be reported as a ruling.
        censored = make_torso()
        h, w = censored.shape[:2]
        cv2.rectangle(censored, (int(w * 0.35), int(h * 0.35)),
                      (int(w * 0.65), int(h * 0.50)), (150, 175, 205), -1)
        make_staged("drdanielbarrett-90631-side-right", images=(censored, censored))
        make_staged("drdanielbarrett-00000-front")
        run(tmp_path, clinic="drdanielbarrett")
        assert dispositions(tmp_path)["drdanielbarrett-90631-side-right"] == "retired-laterality"

    def test_the_registry_clinic_key_does_not_gate_the_ruling(self, tmp_path, make_staged):
        # The lookup is keyed on the pair id, not on the clinic the registry
        # happens to file it under: a pair retired under any key stays retired.
        pair_id = "sanantonio-24021-oblique-right"
        make_staged(pair_id, view="oblique-right")
        make_staged("sanantonio-00000-front", seed=41)

        registry = json.loads(REGISTRY.read_text())
        pairs = registry["retired_laterality"]["pairs"]
        pairs["sanantonio"] = [p for p in pairs["sanantonio"] if p != pair_id]
        pairs["sanantonio-2026-rescrape"] = [pair_id]
        edited = tmp_path / "retired_pairs.json"
        edited.write_text(json.dumps(registry))

        run(tmp_path, clinic="sanantonio", extra=["--registry", str(edited)])
        assert dispositions(tmp_path)[pair_id] == "retired-laterality"
        assert not (tmp_path / "corpus" / "sanantonio" / pair_id).exists()
        assert (tmp_path / "corpus" / "sanantonio" / "sanantonio-00000-front").exists()

    def test_deleting_a_pair_from_the_registry_lets_it_emit_again(
        self, tmp_path, make_staged
    ):
        # The documented recovery path: the registry is the sole authority on a
        # retirement, and the quarantine copy this stage writes is an archive of
        # that ruling, not a second one that outlives it.
        pair_id = "sanantonio-24021-oblique-right"
        make_staged(pair_id)
        quarantine = tmp_path / "quarantine"
        (quarantine / "retired-laterality" / "sanantonio" / pair_id).mkdir(parents=True)

        registry = json.loads(REGISTRY.read_text())
        registry["retired_laterality"]["pairs"]["sanantonio"] = [
            p for p in registry["retired_laterality"]["pairs"]["sanantonio"] if p != pair_id
        ]
        edited = tmp_path / "retired_pairs.json"
        edited.write_text(json.dumps(registry))

        run(tmp_path, clinic="sanantonio", quarantine=quarantine,
            extra=["--registry", str(edited)])
        assert dispositions(tmp_path)[pair_id] == "emit"
        assert (tmp_path / "corpus" / "sanantonio" / pair_id / "before.jpg").exists()

    def test_a_retired_pair_is_quarantined_not_lost(self, tmp_path, make_staged):
        # "Retire rather than delete": images and consent metadata survive, with
        # the reason attached, under the corpus quarantine convention.
        pair_id = "sanantonio-24021-oblique-right"
        staged = make_staged(pair_id)
        make_staged("sanantonio-00000-front")
        quarantine = tmp_path / "quarantine"
        run(tmp_path, clinic="sanantonio", quarantine=quarantine)

        held = quarantine / "retired-laterality" / "sanantonio" / pair_id
        assert {p.name for p in held.iterdir()} == {"before.jpg", "after.jpg", "meta.json"}
        assert json.loads((held / "meta.json").read_text())["consent_ref"]
        # ...and the staged original is untouched.
        assert (staged / "before.jpg").exists()


# --- Emit-stage behaviour --------------------------------------------------


class TestEmit:
    def test_a_clean_pair_reaches_the_corpus_verbatim(self, tmp_path, make_staged):
        staged = make_staged("clinic01-0001-front")
        assert run(tmp_path) == 0
        dest = tmp_path / "corpus" / "clinic01" / "clinic01-0001-front"
        for name in ("before.jpg", "after.jpg", "meta.json"):
            assert (dest / name).read_bytes() == (staged / name).read_bytes()

    def test_staging_is_never_modified(self, tmp_path, make_staged):
        staged = make_staged("clinic01-0001-front")
        before = {p.name: p.read_bytes() for p in staged.iterdir()}
        run(tmp_path)
        assert {p.name: p.read_bytes() for p in staged.iterdir()} == before

    def test_dry_run_writes_nothing(self, tmp_path, make_staged):
        make_staged("clinic01-0001-front")
        run(tmp_path, extra=["--dry-run"])
        assert not (tmp_path / "corpus").exists()

    def test_report_enumerates_every_staged_pair_exactly_once(self, tmp_path, make_staged):
        make_staged("clinic01-0001-front", seed=1)
        make_staged("sanantonio-00000-front", seed=2)
        make_staged("sanantonio-24021-oblique-right", seed=3)
        run(tmp_path, clinic="sanantonio")
        assert dispositions(tmp_path) == {
            "clinic01-0001-front": "other-clinic",
            "sanantonio-00000-front": "emit",
            "sanantonio-24021-oblique-right": "retired-laterality",
        }

    def test_another_clinics_staged_pairs_are_never_written_under_this_clinic(
        self, tmp_path, make_staged, capsys
    ):
        # ingest.py stages every clinic into one flat tree, so --clinic has to
        # decide per pair. Misfiling one is unrecoverable: the never-overwrite
        # guard cannot fire on a destination that is new, and nothing under
        # raw/ or staging/ may be deleted to reconstruct the correct state.
        make_staged("clinic01-0001-front", seed=1)
        make_staged("clinic01-0002-front", seed=2)
        make_staged("sanantonio-00000-front", seed=3)

        assert run(tmp_path, clinic="sanantonio") == 0
        rows = dispositions(tmp_path)
        assert rows["clinic01-0001-front"] == "other-clinic"
        assert rows["clinic01-0002-front"] == "other-clinic"
        assert {p.name for p in (tmp_path / "corpus").iterdir()} == {"sanantonio"}
        assert {p.name for p in (tmp_path / "corpus" / "sanantonio").iterdir()} == {
            "sanantonio-00000-front"
        }
        assert "2 staged pairs skipped as another clinic's" in capsys.readouterr().out

    def test_another_clinics_retired_pair_is_not_archived_under_this_clinic(
        self, tmp_path, make_staged
    ):
        # The quarantine copy is written under --clinic, so a retired pair that
        # is not this clinic's must not be archived there either.
        make_staged("drdanielbarrett-34421-oblique-right", seed=1)
        make_staged("sanantonio-00000-front", seed=2)
        quarantine = tmp_path / "quarantine"

        run(tmp_path, clinic="sanantonio", quarantine=quarantine)
        assert dispositions(tmp_path)["drdanielbarrett-34421-oblique-right"] == "other-clinic"
        assert not (quarantine / "retired-laterality" / "sanantonio").exists()

    def test_an_existing_corpus_pair_is_never_overwritten(self, tmp_path, make_staged):
        make_staged("clinic01-0001-front")
        dest = tmp_path / "corpus" / "clinic01" / "clinic01-0001-front"
        dest.mkdir(parents=True)
        (dest / "before.jpg").write_bytes(b"earlier emit")
        assert run(tmp_path) == 1
        assert (dest / "before.jpg").read_bytes() == b"earlier emit"
        assert dispositions(tmp_path)["clinic01-0001-front"] == "emit-failed"

    def test_a_second_run_carries_the_new_pairs_and_succeeds(self, tmp_path, make_staged):
        # Staging is append-only, so this is the normal shape of every run after
        # the first. Already-emitted pairs are not failures and are not rewritten.
        make_staged("clinic01-0001-front", seed=1)
        assert run(tmp_path) == 0
        dest = tmp_path / "corpus" / "clinic01" / "clinic01-0001-front"
        written_at = {p.name: p.stat().st_mtime_ns for p in dest.iterdir()}

        make_staged("clinic01-0002-front", seed=2)
        assert run(tmp_path) == 0
        assert dispositions(tmp_path) == {
            "clinic01-0001-front": "already-emitted",
            "clinic01-0002-front": "emit",
        }
        assert {p.name: p.stat().st_mtime_ns for p in dest.iterdir()} == written_at
        assert (tmp_path / "corpus" / "clinic01" / "clinic01-0002-front").exists()

    def test_a_corpus_pair_that_differs_is_a_loud_failure(self, tmp_path, make_staged, capsys):
        # A destination that is NOT the staged pair is a real conflict: someone
        # has to look at it, so it stays a failure however the run is re-tried.
        staged = make_staged("clinic01-0001-front")
        dest = tmp_path / "corpus" / "clinic01" / "clinic01-0001-front"
        dest.mkdir(parents=True)
        for name in ("before.jpg", "after.jpg", "meta.json"):
            (dest / name).write_bytes(b"a different pair emitted earlier")

        assert run(tmp_path) == 1
        assert dispositions(tmp_path)["clinic01-0001-front"] == "emit-failed"
        for name in ("before.jpg", "after.jpg", "meta.json"):
            assert (dest / name).read_bytes() == b"a different pair emitted earlier"
        assert (staged / "before.jpg").exists()
        assert "FAIL clinic01-0001-front" in capsys.readouterr().out

    def test_a_run_that_holds_every_pair_is_not_a_failure(self, tmp_path, make_staged):
        # Nothing went wrong: the ruling held every staged pair back.
        make_staged("sanantonio-24021-oblique-right", seed=1)
        make_staged("sanantonio-24143-side-left", seed=2)
        assert run(tmp_path, clinic="sanantonio") == 0

    def test_dry_run_predicts_what_the_real_run_will_do(self, tmp_path, make_staged):
        make_staged("clinic01-0001-front", seed=1)
        run(tmp_path)
        make_staged("clinic01-0002-front", seed=2)

        assert run(tmp_path, extra=["--dry-run"]) == 0
        assert dispositions(tmp_path) == {
            "clinic01-0001-front": "already-emitted",
            "clinic01-0002-front": "emit",
        }
        assert not (tmp_path / "corpus" / "clinic01" / "clinic01-0002-front").exists()

    def test_a_clash_still_leaves_a_report_of_what_was_written(self, tmp_path, make_staged):
        # The corpus tree is the audit surface: a run that stops short must
        # never leave pairs in it with no record of which ones.
        make_staged("clinic01-0001-front", seed=1)
        make_staged("clinic01-0002-front", seed=2)
        clash = tmp_path / "corpus" / "clinic01" / "clinic01-0002-front"
        clash.mkdir(parents=True)
        (clash / "before.jpg").write_bytes(b"earlier emit")

        assert run(tmp_path) == 1
        rows = dispositions(tmp_path)
        assert rows == {"clinic01-0001-front": "emit", "clinic01-0002-front": "emit-failed"}
        assert (tmp_path / "corpus" / "clinic01" / "clinic01-0001-front" / "before.jpg").exists()
        assert (clash / "before.jpg").read_bytes() == b"earlier emit"

    def test_an_unwritable_corpus_is_reported_not_raised(self, tmp_path, make_staged):
        make_staged("clinic01-0001-front")
        # A plain file where the clinic directory belongs: mkdir fails with an
        # OSError that is not a clash, the same shape as a full or read-only disk.
        (tmp_path / "corpus").mkdir()
        (tmp_path / "corpus" / "clinic01").write_text("not a directory")
        assert run(tmp_path) == 1
        assert dispositions(tmp_path)["clinic01-0001-front"] == "emit-failed"

    def test_a_failed_copy_leaves_no_half_written_pair(
        self, tmp_path, make_staged, monkeypatch
    ):
        # A pair lands whole or not at all: a corpus walk over pair directories
        # must never meet one holding an image with no meta.json, and the fault
        # must not make the pair permanently unemittable.
        make_staged("clinic01-0001-front", seed=1)
        make_staged("clinic01-0002-front", seed=2)
        real_copyfile = shutil.copyfile

        def fail_after_the_first_image(src, dst):
            if Path(dst).name == "after.jpg" and Path(dst).parent.name == "clinic01-0002-front":
                raise OSError(28, "No space left on device")
            return real_copyfile(src, dst)

        monkeypatch.setattr(emit_corpus.shutil, "copyfile", fail_after_the_first_image)
        assert run(tmp_path) == 1
        rows = dispositions(tmp_path)
        assert rows["clinic01-0001-front"] == "emit"
        assert rows["clinic01-0002-front"] == "emit-failed"
        assert not (tmp_path / "corpus" / "clinic01" / "clinic01-0002-front").exists()

        monkeypatch.undo()
        run(tmp_path)
        assert dispositions(tmp_path)["clinic01-0002-front"] == "emit"

    def test_an_interrupted_run_never_claims_a_pair_it_did_not_write(
        self, tmp_path, make_staged, monkeypatch
    ):
        # Ctrl-C partway through: the report must under-state what landed rather
        # than claim pairs that are not in the tree.
        make_staged("clinic01-0001-front", seed=1)
        make_staged("clinic01-0002-front", seed=2)
        real_copyfile = shutil.copyfile

        def interrupt_on_the_second_pair(src, dst):
            if Path(dst).parent.name == "clinic01-0002-front":
                raise KeyboardInterrupt
            return real_copyfile(src, dst)

        monkeypatch.setattr(emit_corpus.shutil, "copyfile", interrupt_on_the_second_pair)
        with pytest.raises(KeyboardInterrupt):
            run(tmp_path)

        rows = dispositions(tmp_path)
        assert rows["clinic01-0001-front"] == "emit"
        assert rows["clinic01-0002-front"] == "pending"
        assert not (tmp_path / "corpus" / "clinic01" / "clinic01-0002-front").exists()

    @pytest.mark.parametrize("clinic", ["sanantonio-2026", "sanantoni", "sanantonios"])
    def test_a_clinic_argument_matching_no_staged_pair_is_refused(
        self, tmp_path, make_staged, capsys, clinic
    ):
        # A typo would silently open a new clinic subtree in the finished corpus
        # that the never-overwrite guard cannot catch and nothing may undo. A
        # dropped character is as likely as an added one, so the match runs to
        # the id separator rather than being a bare prefix.
        make_staged("sanantonio-00001-front")
        assert run(tmp_path, clinic=clinic) == 1
        assert not (tmp_path / "corpus").exists()
        assert "observed prefix: sanantonio" in capsys.readouterr().out

    def test_a_staged_pair_without_meta_is_reported_not_skipped(self, tmp_path, make_staged):
        # ingest.py copies meta.json last, so an interrupted ingest leaves the
        # images alone in the pair directory. --report enumerates every staged
        # pair, so that one has to read as invalid-meta rather than vanish.
        make_staged("clinic01-0001-front", seed=1)
        half_ingested = make_staged("clinic01-0002-front", seed=2)
        (half_ingested / "meta.json").unlink()

        run(tmp_path)
        rows = dispositions(tmp_path)
        assert rows == {"clinic01-0001-front": "emit", "clinic01-0002-front": "invalid-meta"}
        assert not (tmp_path / "corpus" / "clinic01" / "clinic01-0002-front").exists()

    def test_empty_staging_is_an_error(self, tmp_path):
        (tmp_path / "staging").mkdir()
        assert run(tmp_path) == 1


class TestGates:
    def test_a_pair_already_in_quarantine_is_not_re_emitted(self, tmp_path, make_staged):
        make_staged("clinic01-0001-front")
        make_staged("clinic01-0002-front")
        quarantine = tmp_path / "quarantine"
        (quarantine / "deidentify-blur-damage" / "clinic01" / "clinic01-0001-front").mkdir(
            parents=True
        )
        run(tmp_path, quarantine=quarantine)
        assert dispositions(tmp_path)["clinic01-0001-front"] == "quarantined"
        assert not (tmp_path / "corpus" / "clinic01" / "clinic01-0001-front").exists()
        assert (tmp_path / "corpus" / "clinic01" / "clinic01-0002-front").exists()

    def test_only_the_retirement_archive_defers_to_the_registry(self, tmp_path, make_staged):
        # The subdirectories this stage writes are an archive of a registry
        # ruling, so an id there but not in the registry emits. Every other
        # quarantine reason is an independent hold and still binds.
        for seed, pair_id in enumerate(
            ("clinic01-0001-front", "clinic01-0002-front", "clinic01-0003-front"), start=1
        ):
            make_staged(pair_id, seed=seed)
        quarantine = tmp_path / "quarantine"
        for reason, pair_id in (
            ("retired-laterality", "clinic01-0001-front"),
            ("withheld-contested", "clinic01-0002-front"),
            ("deidentify-blur-damage", "clinic01-0003-front"),
        ):
            (quarantine / reason / "clinic01" / pair_id).mkdir(parents=True)

        run(tmp_path, quarantine=quarantine)
        rows = dispositions(tmp_path)
        assert rows["clinic01-0001-front"] == "emit"
        assert rows["clinic01-0002-front"] == "emit"
        assert rows["clinic01-0003-front"] == "quarantined"

    def test_a_censored_pair_is_not_emitted(self, tmp_path, make_staged):
        censored = make_torso()
        h, w = censored.shape[:2]
        cv2.circle(censored, (int(w * 0.40), int(h * 0.40)), int(min(h, w) * 0.09), (0, 0, 0), -1)
        make_staged("clinic01-0001-front", images=(censored, censored))
        make_staged("clinic01-0002-front")
        run(tmp_path)
        assert dispositions(tmp_path)["clinic01-0001-front"] == "censored"

    def test_a_pair_below_the_size_floor_is_not_emitted(self, tmp_path, make_staged):
        make_staged("clinic01-0001-front", size=(399, 500))
        make_staged("clinic01-0002-front")
        run(tmp_path)
        assert dispositions(tmp_path)["clinic01-0001-front"] == "too-small"

    def test_a_pair_at_the_floor_is_emitted(self, tmp_path, make_staged):
        # drkolker publishes at 418px; MIN_DIMENSION is 400 to admit it.
        make_staged("clinic01-0001-front", size=(418, 418))
        run(tmp_path)
        assert dispositions(tmp_path)["clinic01-0001-front"] == "emit"

    def test_an_image_still_carrying_exif_is_not_emitted(self, tmp_path, make_staged):
        # Staged images come from ingest.py, which re-encodes and so strips
        # EXIF. One that still carries it did not, and this stage refuses it
        # rather than stripping - stripping here would hide the real fault.
        make_staged("clinic01-0001-front", exif=True)
        make_staged("clinic01-0002-front")
        run(tmp_path)
        assert dispositions(tmp_path)["clinic01-0001-front"] == "carries-exif"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"consent_ref": ""},
            {"volume_cc": None},
            {"view": "oblique"},
            {"shape": "square"},
            {"pair_id": "clinic01-9999-front"},
        ],
    )
    def test_invalid_metadata_is_not_emitted(self, tmp_path, make_staged, overrides):
        make_staged("clinic01-0001-front", meta_overrides=overrides)
        make_staged("clinic01-0002-front")
        run(tmp_path)
        assert dispositions(tmp_path)["clinic01-0001-front"] == "invalid-meta"

    def test_a_duplicate_pair_is_not_emitted_twice(self, tmp_path, make_staged):
        same = make_torso(seed=3)
        make_staged("clinic01-0001-front", images=(same, same))
        make_staged("clinic01-0002-front", images=(same, same))
        run(tmp_path)
        assert dispositions(tmp_path)["clinic01-0002-front"] == "duplicate"

    def test_pixels_reach_the_corpus_unaltered(self, tmp_path, make_staged):
        # Captain ruling 2026-08-14: the consented clinics guarantee no faces
        # appear in what they publish. The stage that detected and blurred them
        # destroyed 85 pairs across ten clinics and was removed; nothing here
        # may reintroduce it, and nothing here re-encodes either. Whatever a
        # pair looks like in staging is what lands in the corpus.
        torso = make_torso()
        staged = make_staged("clinic01-0001-front", images=(torso, torso.copy()))
        run(tmp_path)
        dest = tmp_path / "corpus" / "clinic01" / "clinic01-0001-front"
        assert (dest / "before.jpg").read_bytes() == (staged / "before.jpg").read_bytes()
        assert np.array_equal(
            cv2.imread(str(dest / "before.jpg")), cv2.imread(str(staged / "before.jpg"))
        )
