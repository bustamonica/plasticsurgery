"""Tests for morphengine.implants.db — SPEC.md §3 (db list) and §2.9 starter data."""

from importlib import resources
from pathlib import Path

import pytest

from morphengine.implants.db import ImplantDB
from morphengine.implants.schema import ImplantParams, ImplantSKU, Placement, Shape

DATA_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "morphengine"
    / "implants"
    / "data"
    / "implants.json"
)

# Canonical profile ordering for the dimensional-sanity property test.
PROFILE_RANK = {"low": 0, "moderate": 1, "moderate plus": 2, "high": 3, "ultra high": 4}


@pytest.fixture(scope="module")
def db() -> ImplantDB:
    return ImplantDB.from_json()


# ---------------------------------------------------------------------------
# JSON loads; all records validate; >= 12 SKUs; all illustrative_placeholder
# ---------------------------------------------------------------------------

def test_bundled_json_resource_is_shipped():
    resource = resources.files("morphengine.implants.data").joinpath("implants.json")
    assert resource.is_file()


def test_json_loads_and_all_records_validate(db):
    skus = db.find()
    assert len(skus) >= 12
    assert all(isinstance(s, ImplantSKU) for s in skus)


def test_from_json_explicit_path_matches_bundled(db):
    from_path = ImplantDB.from_json(DATA_PATH)
    assert [s.sku_id for s in from_path.find()] == [s.sku_id for s in db.find()]


def test_all_records_illustrative_placeholder(db):
    for sku in db.find():
        assert sku.values_status == "illustrative_placeholder"
        assert "PLACEHOLDER" in sku.source


def test_sku_ids_unique(db):
    ids = [s.sku_id for s in db.find()]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# SPEC §2.9 starter-data coverage
# ---------------------------------------------------------------------------

def test_starter_data_coverage(db):
    skus = db.find()
    brands = {s.brand for s in skus}
    assert {"Mentor", "Natrelle", "Motiva", "Sientra"} <= brands
    volumes = [s.volume_cc for s in skus]
    assert min(volumes) >= 175 and max(volumes) <= 650
    assert len({s.profile_class for s in skus}) >= 3
    assert len([s for s in skus if s.shape is Shape.ANATOMICAL]) >= 2
    assert {s.shape for s in skus} == {Shape.ROUND, Shape.ANATOMICAL}
    # SPEC anchor: 350 cc high-profile round ~ base 11.5-12.5 cm, proj 4.5-5.0 cm
    anchor = db.get("mentor-memorygel-350-hp")
    assert 11.5 <= anchor.base_width_cm <= 12.5
    assert 4.5 <= anchor.projection_cm <= 5.0


# ---------------------------------------------------------------------------
# find() filters
# ---------------------------------------------------------------------------

def test_find_no_filters_returns_all(db):
    assert len(db.find()) >= 12


def test_find_by_brand(db):
    for brand in ("Mentor", "Natrelle", "Motiva", "Sientra"):
        hits = db.find(brand=brand)
        assert hits, f"no SKUs for brand {brand}"
        assert all(s.brand == brand for s in hits)


def test_find_by_cc_range(db):
    hits = db.find(min_cc=300, max_cc=400)
    assert hits
    assert all(300 <= s.volume_cc <= 400 for s in hits)
    assert db.find(min_cc=10_000) == []


def test_find_by_profile_class(db):
    hits = db.find(profile_class="high")
    assert hits
    assert all(s.profile_class == "high" for s in hits)


def test_find_by_shape_accepts_str_and_enum(db):
    assert db.find(shape="anatomical") == db.find(shape=Shape.ANATOMICAL)
    assert all(s.shape is Shape.ANATOMICAL for s in db.find(shape="anatomical"))


def test_find_by_placement(db):
    hits = db.find(placement=Placement.SUBMUSCULAR)
    assert hits
    assert all(Placement.SUBMUSCULAR in s.placement_options for s in hits)
    # string form is equivalent
    assert db.find(placement="dual-plane") == db.find(placement=Placement.DUAL_PLANE)


def test_find_filters_combine(db):
    hits = db.find(brand="Mentor", shape="round", min_cc=300, profile_class="high")
    assert hits
    assert all(
        s.brand == "Mentor" and s.shape is Shape.ROUND and s.volume_cc >= 300
        and s.profile_class == "high"
        for s in hits
    )


# ---------------------------------------------------------------------------
# get() / KeyError
# ---------------------------------------------------------------------------

def test_get_roundtrip(db):
    sku = db.get("mentor-memorygel-350-hp")
    assert sku.sku_id == "mentor-memorygel-350-hp"
    assert sku.brand == "Mentor"


def test_get_unknown_sku_raises_keyerror_with_clear_message(db):
    with pytest.raises(KeyError) as excinfo:
        db.get("no-such-sku")
    assert "no-such-sku" in str(excinfo.value)


# ---------------------------------------------------------------------------
# to_params
# ---------------------------------------------------------------------------

def test_to_params_mapping(db):
    sku = db.get("mentor-memorygel-350-hp")
    params = db.to_params(sku.sku_id, Placement.SUBMUSCULAR)
    assert isinstance(params, ImplantParams)
    assert params.volume_cc == sku.volume_cc
    assert params.base_width_cm == sku.base_width_cm
    assert params.projection_cm == sku.projection_cm
    assert params.shape is sku.shape
    assert params.placement is Placement.SUBMUSCULAR


def test_to_params_accepts_str_placement(db):
    params = db.to_params("mentor-memorygel-350-hp", "dual-plane")
    assert params.placement is Placement.DUAL_PLANE


def test_to_params_rejects_placement_not_in_options(db):
    # natrelle-410-300-mod offers only submuscular / dual-plane
    with pytest.raises(ValueError) as excinfo:
        db.to_params("natrelle-410-300-mod", Placement.SUBGLANDULAR)
    assert "subglandular" in str(excinfo.value)


def test_to_params_rejects_invalid_placement_value(db):
    with pytest.raises(ValueError):
        db.to_params("mentor-memorygel-350-hp", "under-the-bed")


def test_to_params_unknown_sku_raises_keyerror(db):
    with pytest.raises(KeyError):
        db.to_params("no-such-sku", Placement.SUBMUSCULAR)


# ---------------------------------------------------------------------------
# Dimensional sanity: at fixed volume, higher profile => smaller base width
# AND larger projection (per shape), over the starter set.
# ---------------------------------------------------------------------------

def test_dimensional_sanity_profile_vs_dimensions(db):
    groups: dict[tuple[Shape, float], list[ImplantSKU]] = {}
    for sku in db.find():
        groups.setdefault((sku.shape, sku.volume_cc), []).append(sku)

    checked = 0
    for (shape, volume), skus in groups.items():
        by_profile: dict[str, ImplantSKU] = {}
        for sku in skus:
            assert sku.profile_class not in by_profile, (
                f"ambiguous fixture data: two SKUs share shape={shape}, "
                f"volume={volume}, profile={sku.profile_class!r}"
            )
            by_profile[sku.profile_class] = sku
        ordered = sorted(by_profile.values(), key=lambda s: PROFILE_RANK[s.profile_class])
        for lower, higher in zip(ordered, ordered[1:]):
            assert higher.base_width_cm < lower.base_width_cm, (
                f"{shape.value} {volume} cc: {higher.profile_class!r} base width "
                f"{higher.base_width_cm} not < {lower.profile_class!r} {lower.base_width_cm}"
            )
            assert higher.projection_cm > lower.projection_cm, (
                f"{shape.value} {volume} cc: {higher.profile_class!r} projection "
                f"{higher.projection_cm} not > {lower.profile_class!r} {lower.projection_cm}"
            )
            checked += 1
    # the property is actually exercised, not vacuously true
    assert checked >= 4


def test_dimensions_scale_monotonically_with_volume_per_profile(db):
    """Plausibility: within (shape, profile_class), bigger cc => wider base."""
    groups: dict[tuple[Shape, str], list[ImplantSKU]] = {}
    for sku in db.find():
        groups.setdefault((sku.shape, sku.profile_class), []).append(sku)
    for skus in groups.values():
        ordered = sorted(skus, key=lambda s: s.volume_cc)
        for small, big in zip(ordered, ordered[1:]):
            assert big.base_width_cm > small.base_width_cm
            assert big.projection_cm > small.projection_cm
