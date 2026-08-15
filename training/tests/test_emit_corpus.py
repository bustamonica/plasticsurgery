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
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import emit_corpus
from conftest import make_torso

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


def run(tmp_path, clinic="clinic01", quarantine=None, extra=()):
    argv = [
        str(tmp_path / "staging"),
        str(tmp_path / "corpus"),
        "--clinic",
        clinic,
        "--report",
        str(tmp_path / "report.csv"),
    ]
    if quarantine is not None:
        argv += ["--quarantine", str(quarantine)]
    return emit_corpus.main(argv + list(extra))


def dispositions(tmp_path) -> dict[str, str]:
    with (tmp_path / "report.csv").open() as fh:
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
        """Stage every one of the 176 retired ids; none may reach the corpus."""
        for clinic, pairs in registry["retired_laterality"]["pairs"].items():
            for pair_id in pairs:
                (tmp_path / "staging" / pair_id).mkdir(parents=True)
                (tmp_path / "staging" / pair_id / "meta.json").write_text("{}")

        for clinic in ("sanantonio", "drdanielbarrett"):
            make_staged(f"{clinic}-00000-front")
            run(tmp_path, clinic=clinic)
            emitted = {p.name for p in (tmp_path / "corpus" / clinic).iterdir()}
            retired = set(registry["retired_laterality"]["pairs"][clinic])
            assert not (emitted & retired)
            assert emitted == {f"{clinic}-00000-front"}

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

    def test_a_misspelled_clinic_argument_still_holds_the_ruling(self, tmp_path, make_staged):
        # --clinic only chooses the destination subdirectory. Pair ids are
        # globally unique and clinic-prefixed, so a variant spelling must not
        # let a retired pair through into a fresh corpus subtree.
        pair_id = "sanantonio-24021-oblique-right"
        make_staged(pair_id)
        make_staged("sanantonio-00000-front")
        run(tmp_path, clinic="sanantonio-2026")
        assert not (tmp_path / "corpus" / "sanantonio-2026" / pair_id).exists()
        assert dispositions(tmp_path)[pair_id] == "retired-laterality"
        assert (tmp_path / "corpus" / "sanantonio-2026" / "sanantonio-00000-front").exists()

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
        make_staged("clinic01-0001-front")
        make_staged("clinic01-0002-front")
        make_staged("sanantonio-24021-oblique-right")
        run(tmp_path, clinic="sanantonio")
        rows = dispositions(tmp_path)
        assert len(rows) == 3
        assert rows["sanantonio-24021-oblique-right"] == "retired-laterality"

    def test_an_existing_corpus_pair_is_never_overwritten(self, tmp_path, make_staged):
        make_staged("clinic01-0001-front")
        dest = tmp_path / "corpus" / "clinic01" / "clinic01-0001-front"
        dest.mkdir(parents=True)
        (dest / "before.jpg").write_bytes(b"earlier emit")
        assert run(tmp_path) == 1
        assert (dest / "before.jpg").read_bytes() == b"earlier emit"
        assert dispositions(tmp_path)["clinic01-0001-front"] == "emit-failed"

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
