"""dataset_schema.json is the delivery contract with the clinics.

These tests pin the 2026-08 extension (placement, incision and the patient frame
metrics) and, above all, the invariant that makes it safe: none of those fields
may reach a caption. `build_caption()` here and `buildCustomModelPrompt()` in
lib/prompt.ts must stay byte-identical, so a field the schema gains but the
caption ignores is a field that cannot break parity.
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from build_dataset import build_caption

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "dataset_schema.json"
NEW_FIELDS = [
    "placement", "incision", "height_cm", "weight_kg", "bra_size_before", "chest_width_cm",
]


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def validator(schema):
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


@pytest.fixture()
def meta() -> dict:
    return {
        "pair_id": "clinic01-0001",
        "shape": "round",
        "volume_cc": 350,
        "view": "front",
        "consent_ref": "clinic01-agreement-2026-05",
        "clothing": "nude",
    }


class TestRequiredSetIsUnchanged:
    def test_required_fields(self, schema):
        assert schema["required"] == ["pair_id", "shape", "volume_cc", "view", "consent_ref"]

    @pytest.mark.parametrize("field", NEW_FIELDS)
    def test_new_fields_are_optional(self, validator, meta, field):
        assert field not in meta
        assert validator.is_valid(meta)


class TestNewFieldsAreAccepted:
    def test_a_fully_specified_chart_row(self, validator, meta):
        meta.update(
            placement="dual-plane", incision="inframammary",
            height_cm=168, weight_kg=57.5, bra_size_before="32B", chest_width_cm=31.5,
        )
        assert sorted(validator.iter_errors(meta), key=str) == []

    @pytest.mark.parametrize(
        "value", ["submuscular", "subglandular", "subfascial", "dual-plane", "unknown"]
    )
    def test_placement_enum(self, validator, meta, value):
        assert validator.is_valid(dict(meta, placement=value))

    @pytest.mark.parametrize(
        "value", ["inframammary", "periareolar", "transaxillary", "unknown"]
    )
    def test_incision_enum(self, validator, meta, value):
        assert validator.is_valid(dict(meta, incision=value))

    def test_bra_size_is_free_text_because_markets_differ(self, validator, meta):
        # '10C' is the Australian equivalent of '32C'; the schema does not
        # normalise sizing systems, it records what the clinic wrote.
        assert validator.is_valid(dict(meta, bra_size_before="10C (AU)"))


class TestNewFieldsAreValidated:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("placement", "over-the-muscle"),
            ("incision", "belly-button"),
            ("height_cm", 6),        # feet, not centimetres
            ("weight_kg", 300),
            ("chest_width_cm", 400),  # millimetres, not centimetres
            ("bra_size_before", 32),  # must stay a string
        ],
    )
    def test_out_of_contract_values_are_rejected(self, validator, meta, field, value):
        assert not validator.is_valid(dict(meta, **{field: value}))


class TestCaptionParityIsUntouched:
    """The hard constraint: the new fields must be invisible to the caption."""

    def test_caption_is_identical_with_and_without_the_new_fields(self, meta):
        enriched = dict(
            meta, placement="submuscular", incision="periareolar",
            height_cm=168, weight_kg=57.5, bra_size_before="32B", chest_width_cm=31.5,
        )
        assert build_caption(enriched) == build_caption(meta)

    @pytest.mark.parametrize("field", NEW_FIELDS)
    def test_no_new_field_leaks_any_wording_into_the_caption(self, meta, field):
        caption = build_caption(dict(meta, **{field: "submuscular"}))
        assert build_caption(meta) == caption
        assert "submuscular" not in caption

    def test_build_dataset_source_never_reads_the_new_fields(self):
        # Belt and braces: a future edit that starts reading one of these would
        # break parity with lib/prompt.ts silently, since the caption tests only
        # cover the values they happen to pass.
        source = (SCHEMA_PATH.parent / "scripts" / "build_dataset.py").read_text()
        for field in NEW_FIELDS:
            assert field not in source


class TestExistingContract:
    def test_clothing_enum_is_unchanged(self, schema):
        assert schema["properties"]["clothing"]["enum"] == ["nude", "bra", "top"]

    def test_volume_bounds_are_unchanged(self, schema):
        volume = schema["properties"]["volume_cc"]
        assert (volume["minimum"], volume["maximum"]) == (100, 1000)

    def test_schema_documents_that_the_new_fields_never_reach_the_model(self, schema):
        assert "build_caption()" in schema["description"]
