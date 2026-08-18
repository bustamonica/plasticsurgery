"""The dataset build: which pairs it admits, and what it writes.

Caption wording is covered by test_captions.py. What matters here is the tree
the trainer actually sees - that a retired clinic cannot re-enter it through a
non-clinic directory, that no patient spans train and val, and that nothing
bound for a training pod carries source metadata.
"""

import json

import pytest
from PIL import Image

from build_dataset import case_id, find_pair_folders, main


def photoshop_exif() -> Image.Exif:
    """The metadata shape actually found on 14 finished-corpus images."""
    exif = Image.Exif()
    exif[305] = "Adobe Photoshop 26.0 (Macintosh)"   # Software
    exif[306] = "2025:01:12 22:37:36"                # DateTime
    return exif


def write_pair(folder, meta, ext=".jpg", exif=None, size=(64, 80)):
    folder.mkdir(parents=True, exist_ok=True)
    for stem, colour in (("before", (200, 180, 170)), ("after", (190, 170, 160))):
        path = folder / f"{stem}{ext}"
        image = Image.new("RGB", size, colour)
        if exif is None:
            image.save(path)
        else:
            image.save(path, exif=exif)
    (folder / "meta.json").write_text(json.dumps(meta))
    return folder


def meta_for(pair_id, view="front", **extra):
    return dict(
        pair_id=pair_id, view=view, shape="round", volume_cc=350,
        consent_ref="ref", clothing="nude", **extra,
    )


@pytest.fixture()
def corpus(tmp_path):
    """A clinic-partitioned corpus tree, plus the _staging trap beside it."""
    root = tmp_path / "corpus"
    for i in range(1, 13):
        for view in ("front", "side-left"):
            write_pair(root / "goodclinic" / f"goodclinic-{i}-{view}",
                       meta_for(f"goodclinic-{i}-{view}", view))
    # The real corpus root carries a pre-emit _staging tree that still holds
    # pairs a captain ruling removed from the finished tree.
    write_pair(root / "_staging" / "retired-1-front", meta_for("retired-1-front"))
    write_pair(root / ".scraper-cache" / "junk-1-front", meta_for("junk-1-front"))
    return root


def build(src, out, extra=()):
    import sys

    argv = sys.argv
    sys.argv = ["build_dataset.py", str(src), str(out), *extra]
    try:
        return main()
    finally:
        sys.argv = argv


def manifest(out):
    return [json.loads(line) for line in (out / "manifest.jsonl").read_text().splitlines()]


class TestSourceLayouts:
    def test_reads_the_clinic_partitioned_corpus_tree(self, corpus):
        folders, _ = find_pair_folders(corpus)
        assert len(folders) == 24
        assert all(f.parent.name == "goodclinic" for f in folders)

    def test_reads_the_flat_clean_tree(self, tmp_path):
        flat = tmp_path / "clean"
        write_pair(flat / "clinic01-0001-front", meta_for("clinic01-0001-front"))
        folders, _ = find_pair_folders(flat)
        assert [f.name for f in folders] == ["clinic01-0001-front"]

    def test_underscore_and_dot_directories_are_skipped_and_reported(self, corpus):
        folders, notes = find_pair_folders(corpus)
        assert not any("retired-1" in str(f) or "junk-1" in str(f) for f in folders)
        assert any("_staging/ (1 pair folder(s))" in n for n in notes)
        assert any(".scraper-cache/" in n for n in notes)


class TestAdmission:
    def test_a_retired_clinic_cannot_re_enter_through_staging(self, corpus, tmp_path):
        out = tmp_path / "dataset"
        assert build(corpus, out) == 0
        assert "retired-1-front" not in {r["pair_id"] for r in manifest(out)}
        assert not list(out.glob("**/retired-1*"))

    def test_a_pair_with_no_volume_cannot_train(self, tmp_path):
        src = tmp_path / "clean"
        write_pair(src / "a-1-front", meta_for("a-1-front"))
        no_volume = meta_for("a-2-front")
        del no_volume["volume_cc"]
        write_pair(src / "a-2-front", no_volume)
        out = tmp_path / "dataset"
        assert build(src, out) == 0
        assert {r["pair_id"] for r in manifest(out)} == {"a-1-front"}

    def test_a_duplicate_pair_id_across_clinics_is_an_error_not_an_overwrite(self, tmp_path):
        src = tmp_path / "corpus"
        write_pair(src / "clinic-a" / "x-1-front", meta_for("x-1-front"))
        write_pair(src / "clinic-b" / "x-1-front", meta_for("x-1-front"))
        assert build(src, tmp_path / "dataset") == 1


class TestSplit:
    def test_no_patient_spans_train_and_val(self, corpus, tmp_path):
        out = tmp_path / "dataset"
        assert build(corpus, out) == 0
        rows = manifest(out)
        cases = {"train": set(), "val": set()}
        for row in rows:
            cases[row["split"]].add(case_id(row["meta"]))
        assert cases["train"] & cases["val"] == set()
        assert cases["val"], "the split should hold out at least one patient"

    def test_case_id_strips_only_the_view_suffix(self):
        assert case_id(meta_for("sanantonio-23818-2-side-left", "side-left")) == (
            "sanantonio-23818-2"
        )
        assert case_id({"pair_id": "odd-id-no-view"}) == "odd-id-no-view"


class TestWhatReachesThePod:
    def test_source_metadata_never_reaches_the_dataset(self, tmp_path):
        src = tmp_path / "clean"
        write_pair(src / "a-1-front", meta_for("a-1-front"), exif=photoshop_exif())
        assert Image.open(src / "a-1-front" / "before.jpg").getexif(), "fixture must carry EXIF"

        out = tmp_path / "dataset"
        assert build(src, out) == 0
        for image in out.glob("**/*.jpg"):
            assert not dict(Image.open(image).getexif()), image

    @pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".png", ".webp"])
    def test_every_source_extension_lands_as_one_jpg(self, tmp_path, ext):
        src = tmp_path / "clean"
        write_pair(src / "a-1-front", meta_for("a-1-front"), ext=ext)
        out = tmp_path / "dataset"
        assert build(src, out) == 0
        row = manifest(out)[0]
        assert row["target"].endswith("a-1-front.jpg")
        assert row["control"].endswith("a-1-front.jpg")
        assert (out / row["target"]).exists() and (out / row["control"]).exists()

    def test_each_target_image_is_paired_with_its_caption(self, corpus, tmp_path):
        out = tmp_path / "dataset"
        assert build(corpus, out) == 0
        for row in manifest(out):
            caption = (out / row["target"]).with_suffix(".txt")
            assert caption.read_text() == row["instruction"] + "\n"
            assert row["instruction"].startswith("The photograph is a ")
