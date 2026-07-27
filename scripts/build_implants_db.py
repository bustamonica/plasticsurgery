#!/usr/bin/env python3
"""Regenerate implants.json from vendored manufacturer source tables (SPEC rev.5).

Reads ``implants/data/sources/*.json`` — verbatim extractions from official
manufacturer documents with citations — applies the inclusion policy below,
asserts dimensional invariants, and emits ``implants/data/implants.json``.

Inclusion policy (rev.5):
- Smooth-shell tables only. Mentor SILTEX / Inspira textured / Sientra textured
  variants differ <=0.2 cm or share dims; documented in SPEC, not duplicated.
- Motiva Round skipped: dimension-identical to Ergonomix per the manufacturer.
- Motiva TrueFixation: only Full-projection variants (FF/MF/LF); Corsé-
  projection anatomicals omitted from v0 menu. Not FDA-approved (international).
- Natrelle 410 skipped entirely: withdrawn from the US market July 2019
  (BIOCELL recall); historical tables remain in sources/ for provenance.
- Sientra Low Plus skipped (late-addition profile; keeps the canonical
  5-rank ladder unambiguous); Sientra Xtra High 245 cc dropped (present only
  in MDC-0270 R11 2022; 2019 catalog and current sientra.com start at 275).
- ``profile_class`` is a canonical 5-rank ladder (low < moderate <
  moderate plus < high < ultra high) for cross-brand comparison;
  ``profile_label`` preserves the manufacturer's own marketing name.
- Anatomical records carry ``height_cm``; Sientra round-base shaped implants
  publish width == height, so height_cm mirrors the width (manufacturer-stated).
"""

import json
import math
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "src" / "morphengine" / "implants" / "data"
SOURCES = DATA / "sources"
OUT = DATA / "implants.json"

ROUND_PLACEMENTS = ["subglandular", "submuscular", "dual-plane"]
ANAT_PLACEMENTS = ["submuscular", "dual-plane"]

# ---------------------------------------------------------------------------
# Source citation strings (kept per-record so every SKU is self-documenting)
# ---------------------------------------------------------------------------
MENTOR_ROUND_SRC = (
    "MENTOR Product Catalog PN 020827-181217 (Eff. Jan 2019), smooth table "
    "pp.7-8 — mentordirect.com/PSG/020827-181217 Product Catalog 2020.pdf; "
    "cross-checked jnjmedtech.com (exact match)"
)
MENTOR_SHAPE_SRC = (
    "MENTOR Product Catalog PN 020827-181217 (Eff. Jan 2019), MemoryShape "
    "SILTEX tables pp.3-4 — mentordirect.com; cross-checked regional Mentor "
    "catalogs + FDA PMA P060028 (style MM)"
)
NATRELLE_SRC = (
    "Natrelle Product Catalog (Allergan US, ~2017), smooth Inspira tables "
    "pp.14-20; cross-checked Allergan 2015 INSPIRA sales tool + Allergan UK "
    "2016 catalog (exact match). Dims shared by Responsive/SoftTouch/Cohesive."
)
MOTIVA_SRC = (
    "Motiva Product Catalogue / Implant Matrix (motiva.health, captured "
    "2026-07); cross-checked 2020 manufacturer catalogue + FDA PMA P230005 "
    "(exact match on overlapping sizes)"
)
SIENTRA_SRC = (
    "Sientra OPUS Luxe Round Catalog MDC-0343 R3 (2019) + Quick Reference "
    "MDC-0270 R11 (2022, sientra.com); cross-checked MDC-0177 (2017 catalog) "
    "(exact match)"
)
SIENTRA_CURVE_SRC = (
    "Sientra Teardrop Quick Reference MDC-0400 R4 (2021, sientra.com); "
    "cross-checked MDC-0177 (2017 catalog) on overlapping sizes (exact match)"
)

# ---------------------------------------------------------------------------
# Series inclusion spec:
# (source_file, product_line_prefix, profile_label_prefix) -> SKU metadata
# ---------------------------------------------------------------------------
SERIES = [
    # --- Mentor MemoryGel round (smooth) ---
    dict(src="mentor", line_pfx="MemoryGel", label_pfx="Moderate Classic",
         brand="Mentor", line="MemoryGel", line_slug="memorygel",
         pclass="moderate", plabel="Moderate Classic", idcode="mod",
         shape="round", source=MENTOR_ROUND_SRC),
    dict(src="mentor", line_pfx="MemoryGel", label_pfx="Moderate Plus",
         brand="Mentor", line="MemoryGel", line_slug="memorygel",
         pclass="moderate plus", plabel="Moderate Plus", idcode="modplus",
         shape="round", source=MENTOR_ROUND_SRC),
    dict(src="mentor", line_pfx="MemoryGel", label_pfx="High Profile",
         brand="Mentor", line="MemoryGel", line_slug="memorygel",
         pclass="high", plabel="High Profile", idcode="hp",
         shape="round", source=MENTOR_ROUND_SRC),
    dict(src="mentor", line_pfx="MemoryGel", label_pfx="Ultra High Profile",
         brand="Mentor", line="MemoryGel", line_slug="memorygel",
         pclass="ultra high", plabel="Ultra High Profile", idcode="uhp",
         shape="round", source=MENTOR_ROUND_SRC),
    # --- Mentor MemoryShape anatomical (SILTEX) ---
    dict(src="mentor", line_pfx="MemoryShape", label_pfx="MM -",
         brand="Mentor", line="MemoryShape", line_slug="memoryshape",
         pclass="moderate", plabel="MM (Style 321)", idcode="mm",
         shape="anatomical", source=MENTOR_SHAPE_SRC),
    dict(src="mentor", line_pfx="MemoryShape", label_pfx="MM+ -",
         brand="Mentor", line="MemoryShape", line_slug="memoryshape",
         pclass="moderate plus", plabel="MM+ (Style 322)", idcode="mmplus",
         shape="anatomical", source=MENTOR_SHAPE_SRC),
    dict(src="mentor", line_pfx="MemoryShape", label_pfx="MH -",
         brand="Mentor", line="MemoryShape", line_slug="memoryshape",
         pclass="high", plabel="MH (Style 323)", idcode="mh",
         shape="anatomical", source=MENTOR_SHAPE_SRC),
    # --- Natrelle Inspira round (smooth; dims gel-independent) ---
    dict(src="natrelle", line_pfx="Inspira", label_pfx="Low (",
         brand="Natrelle", line="Inspira", line_slug="inspira",
         pclass="low", plabel="Low (SRL/SSL/SCL)", idcode="low",
         shape="round", source=NATRELLE_SRC),
    dict(src="natrelle", line_pfx="Inspira", label_pfx="Low-Plus (",
         brand="Natrelle", line="Inspira", line_slug="inspira",
         pclass="moderate", plabel="Low-Plus (SRLP/SSLP/SCLP)", idcode="mod",
         shape="round", source=NATRELLE_SRC),
    dict(src="natrelle", line_pfx="Inspira", label_pfx="Moderate (",
         brand="Natrelle", line="Inspira", line_slug="inspira",
         pclass="moderate plus", plabel="Moderate (SRM/SSM/SCM)", idcode="modplus",
         shape="round", source=NATRELLE_SRC),
    dict(src="natrelle", line_pfx="Inspira", label_pfx="Full (",
         brand="Natrelle", line="Inspira", line_slug="inspira",
         pclass="high", plabel="Full (SRF/SSF/SCF)", idcode="hp",
         shape="round", source=NATRELLE_SRC),
    dict(src="natrelle", line_pfx="Inspira", label_pfx="Extra-Full (",
         brand="Natrelle", line="Inspira", line_slug="inspira",
         pclass="ultra high", plabel="Extra-Full (SRX/SSX/SCX)", idcode="uhp",
         shape="round", source=NATRELLE_SRC),
    # Natrelle 410: EXCLUDED (US withdrawal July 2019) — kept in sources only.
    # --- Motiva Ergonomix ---
    dict(src="motiva", line_pfx="Ergonomix", label_pfx="Mini",
         brand="Motiva", line="Ergonomix", line_slug="ergonomix",
         pclass="low", plabel="Mini", idcode="low",
         shape="round", source=MOTIVA_SRC),
    dict(src="motiva", line_pfx="Ergonomix", label_pfx="Demi",
         brand="Motiva", line="Ergonomix", line_slug="ergonomix",
         pclass="moderate", plabel="Demi", idcode="mod",
         shape="round", source=MOTIVA_SRC),
    dict(src="motiva", line_pfx="Ergonomix", label_pfx="Full",
         brand="Motiva", line="Ergonomix", line_slug="ergonomix",
         pclass="high", plabel="Full", idcode="hp",
         shape="round", source=MOTIVA_SRC),
    dict(src="motiva", line_pfx="Ergonomix", label_pfx="Corsé",
         brand="Motiva", line="Ergonomix", line_slug="ergonomix",
         pclass="ultra high", plabel="Corsé", idcode="uhp",
         shape="round", source=MOTIVA_SRC),
    # --- Motiva Ergonomix2 ---
    dict(src="motiva", line_pfx="Ergonomix2", label_pfx="Mini",
         brand="Motiva", line="Ergonomix2", line_slug="ergonomix2",
         pclass="low", plabel="Mini", idcode="low",
         shape="round", source=MOTIVA_SRC),
    dict(src="motiva", line_pfx="Ergonomix2", label_pfx="Demi",
         brand="Motiva", line="Ergonomix2", line_slug="ergonomix2",
         pclass="moderate", plabel="Demi", idcode="mod",
         shape="round", source=MOTIVA_SRC),
    dict(src="motiva", line_pfx="Ergonomix2", label_pfx="Full",
         brand="Motiva", line="Ergonomix2", line_slug="ergonomix2",
         pclass="high", plabel="Full", idcode="hp",
         shape="round", source=MOTIVA_SRC),
    dict(src="motiva", line_pfx="Ergonomix2", label_pfx="Corsé",
         brand="Motiva", line="Ergonomix2", line_slug="ergonomix2",
         pclass="ultra high", plabel="Corsé", idcode="uhp",
         shape="round", source=MOTIVA_SRC),
    # Motiva Round: EXCLUDED (dimension-identical to Ergonomix per manufacturer).
    # --- Motiva Anatomical TrueFixation: Full-projection variants only ---
    # (product_line carries the height class: variants repeat the same width
    # ladder, so merging them breaks every dimensional invariant)
    dict(src="motiva", line_pfx="Anatomical TrueFixation", label_pfx="FF ",
         brand="Motiva", line="Anatomical TrueFixation Full-height", line_slug="truefix-f",
         pclass="high", plabel="FF (Full height, Full projection)", idcode="ff",
         shape="anatomical", source=MOTIVA_SRC,
         notes="Not FDA-approved in the US; international market product."),
    dict(src="motiva", line_pfx="Anatomical TrueFixation", label_pfx="MF ",
         brand="Motiva", line="Anatomical TrueFixation Medium-height", line_slug="truefix-m",
         pclass="high", plabel="MF (Medium height, Full projection)", idcode="mf",
         shape="anatomical", source=MOTIVA_SRC,
         notes="Not FDA-approved in the US; international market product."),
    dict(src="motiva", line_pfx="Anatomical TrueFixation", label_pfx="LF ",
         brand="Motiva", line="Anatomical TrueFixation Low-height", line_slug="truefix-l",
         pclass="high", plabel="LF (Low height, Full projection)", idcode="lf",
         shape="anatomical", source=MOTIVA_SRC,
         notes="Not FDA-approved in the US; international market product."),
    # --- Sientra OPUS Luxe round (smooth) ---
    dict(src="sientra", line_pfx="OPUS Luxe", label_pfx="Low",
         brand="Sientra", line="OPUS Luxe", line_slug="opus",
         pclass="low", plabel="Low", idcode="low",
         shape="round", source=SIENTRA_SRC, surface="smooth"),
    dict(src="sientra", line_pfx="OPUS Luxe", label_pfx="Moderate",
         brand="Sientra", line="OPUS Luxe", line_slug="opus",
         pclass="moderate", plabel="Moderate", idcode="mod",
         shape="round", source=SIENTRA_SRC, surface="smooth"),
    dict(src="sientra", line_pfx="OPUS Luxe", label_pfx="Moderate Plus",
         brand="Sientra", line="OPUS Luxe", line_slug="opus",
         pclass="moderate plus", plabel="Moderate Plus", idcode="modplus",
         shape="round", source=SIENTRA_SRC, surface="smooth"),
    dict(src="sientra", line_pfx="OPUS Luxe", label_pfx="High",
         brand="Sientra", line="OPUS Luxe", line_slug="opus",
         pclass="high", plabel="High", idcode="hp",
         shape="round", source=SIENTRA_SRC, surface="smooth"),
    dict(src="sientra", line_pfx="OPUS Luxe", label_pfx="Xtra High",
         brand="Sientra", line="OPUS Luxe", line_slug="opus",
         pclass="ultra high", plabel="Xtra High", idcode="uhp",
         shape="round", source=SIENTRA_SRC, surface="smooth",
         drop_volumes={245}),  # only in 2022 QRG; 2019 catalog + current site start at 275
    # Sientra Low Plus: EXCLUDED (keeps canonical ladder unambiguous).
    # --- Sientra OPUS Curve anatomical ---
    dict(src="sientra", line_pfx="OPUS Curve", label_pfx="Classic Base Moderate",
         brand="Sientra", line="OPUS Curve Classic Base", line_slug="opuscurve-classic",
         pclass="moderate", plabel="Classic Base Moderate", idcode="cbmod",
         shape="anatomical", source=SIENTRA_CURVE_SRC),
    dict(src="sientra", line_pfx="OPUS Curve", label_pfx="Classic Base High",
         brand="Sientra", line="OPUS Curve Classic Base", line_slug="opuscurve-classic",
         pclass="high", plabel="Classic Base High", idcode="cbhigh",
         shape="anatomical", source=SIENTRA_CURVE_SRC,
         notes="Per-size values single-source (MDC-0400 R4)."),
    dict(src="sientra", line_pfx="OPUS Curve", label_pfx="Round Base Moderate",
         brand="Sientra", line="OPUS Curve Round Base", line_slug="opuscurve-round",
         pclass="moderate", plabel="Round Base Moderate", idcode="rbmod",
         shape="anatomical", source=SIENTRA_CURVE_SRC),
    dict(src="sientra", line_pfx="OPUS Curve", label_pfx="Round Base High",
         brand="Sientra", line="OPUS Curve Round Base", line_slug="opuscurve-round",
         pclass="high", plabel="Round Base High", idcode="rbhigh",
         shape="anatomical", source=SIENTRA_CURVE_SRC),
    # Sientra legacy oval-base shaped: EXCLUDED (2017-only table, likely discontinued).
]

# Per-record notes for specific volumes (resolved conflicts, keyed by idcode prefix)
ROW_NOTES = {
    ("opus", "modplus", 455): (
        "Projection conflict in sources resolved to 4.6 cm (independent audit "
        "2026-07-27): 4.6 in MDC-0343 R3 (2019) and MDC-0270 R11 (2022) — the "
        "two most recent documents; 4.8 appears only in the 2017 catalog."
    ),
}


PROFILE_RANK = {"low": 0, "moderate": 1, "moderate plus": 2, "high": 3, "ultra high": 4}


def load_sources() -> dict[str, dict]:
    return {p.stem.replace("_tables", ""): json.loads(p.read_text())
            for p in SOURCES.glob("*_tables.json")}


def series_rows(doc: dict, spec: dict) -> list[dict]:
    """Find the matching series in a source doc and return its rows."""
    for s in doc["series"]:
        if not s["product_line"].startswith(spec["line_pfx"]):
            continue
        if not s["profile_label"].startswith(spec["label_pfx"]):
            continue
        if spec.get("surface") and s.get("surface", "smooth") != spec["surface"]:
            continue
        return s["rows"]
    raise KeyError(f"series not found: {spec['src']} / {spec['line_pfx']} / {spec['label_pfx']}")


def build_records() -> list[dict]:
    sources = load_sources()
    records: list[dict] = []
    for spec in SERIES:
        rows = series_rows(sources[spec["src"]], spec)
        drop = spec.get("drop_volumes", set())
        for row in rows:
            vol = int(row["volume_cc"])
            if vol in drop:
                continue
            bw = row.get("diameter_cm", row.get("width_cm"))
            height = row.get("height_cm")
            if spec["shape"] == "anatomical" and height is None:
                height = bw  # Sientra round-base: manufacturer states height == width
            rec = {
                "sku_id": f"{spec['brand'].lower()}-{spec['line_slug']}-{vol}-{spec['idcode']}",
                "brand": spec["brand"],
                "product_line": spec["line"],
                "volume_cc": vol,
                "base_width_cm": bw,
                "projection_cm": row["projection_cm"],
                "shape": spec["shape"],
                "profile_class": spec["pclass"],
                "profile_label": spec["plabel"],
                "placement_options": ROUND_PLACEMENTS if spec["shape"] == "round" else ANAT_PLACEMENTS,
                "values_status": "verified",
                "source": spec["source"],
            }
            if height is not None:
                rec["height_cm"] = height
            note = spec.get("notes") or ROW_NOTES.get((spec["line_slug"], spec["idcode"], vol))
            if note:
                rec["notes"] = note
            records.append(rec)
    return records


def check_invariants(records: list[dict]) -> list[str]:
    """Dimensional sanity on the emitted set. Returns a report; raises on violation."""
    errors: list[str] = []

    ids = [r["sku_id"] for r in records]
    if len(ids) != len(set(ids)):
        dupes = {i for i in ids if ids.count(i) > 1}
        errors.append(f"duplicate sku_ids: {sorted(dupes)}")

    # 1. Per (brand, line, shape, profile_class): base width strictly increasing
    #    with volume; projection non-decreasing up to <=0.2 cm print-rounding dips.
    ladders: dict[tuple, list[dict]] = {}
    for r in records:
        ladders.setdefault((r["brand"], r["product_line"], r["shape"], r["profile_class"]), []).append(r)
    for key, recs in ladders.items():
        recs.sort(key=lambda r: r["volume_cc"])
        for small, big in zip(recs, recs[1:]):
            if big["base_width_cm"] <= small["base_width_cm"]:
                errors.append(f"bw not increasing: {key} {small['volume_cc']}->{big['volume_cc']}")
            dip = small["projection_cm"] - big["projection_cm"]
            if dip > 0.2 + 1e-9:  # official tables contain real <=0.2 cm dips (print rounding)
                errors.append(f"proj dip {dip:.2f} > 0.2: {key} {small['volume_cc']}->{big['volume_cc']}")

    # 2. Per (brand, line, shape, volume): higher canonical profile rank =>
    #    base width NOT LARGER (real tables have equal-width steps, e.g.
    #    Sientra 590 cc MP vs High both 14.3) AND strictly larger projection.
    groups: dict[tuple, dict[str, dict]] = {}
    for r in records:
        g = groups.setdefault((r["brand"], r["product_line"], r["shape"], r["volume_cc"]), {})
        if r["profile_class"] in g:
            errors.append(f"two SKUs share {r['brand']}/{r['product_line']}/"
                          f"{r['shape']}/{r['volume_cc']}cc/{r['profile_class']}")
        g[r["profile_class"]] = r
    n_rank_checks = 0
    for key, by_profile in groups.items():
        ordered = [p for _, p in sorted(by_profile.items(), key=lambda kv: PROFILE_RANK[kv[0]])]
        for lower, higher in zip(ordered, ordered[1:]):
            n_rank_checks += 1
            if higher["base_width_cm"] > lower["base_width_cm"] + 1e-9:
                errors.append(f"rank bw: {key} {higher['profile_class']} "
                              f"{higher['base_width_cm']} > {lower['profile_class']} {lower['base_width_cm']}")
            if not higher["projection_cm"] > lower["projection_cm"]:
                errors.append(f"rank proj: {key} {higher['profile_class']} "
                              f"{higher['projection_cm']} !> {lower['profile_class']} {lower['projection_cm']}")

    # 3. Engine compatibility: dome fullness beta = pi*a^2*h/V - 1 >= 0.05 floor.
    min_beta = float("inf")
    for r in records:
        a = r["base_width_cm"] / 2
        beta = math.pi * a * a * r["projection_cm"] / r["volume_cc"] - 1.0
        min_beta = min(min_beta, beta)
        if beta < 0.05:
            errors.append(f"beta floor: {r['sku_id']} beta={beta:.3f}")

    report = [
        f"{len(records)} SKUs, {len(ladders)} ladders, {n_rank_checks} rank comparisons, "
        f"min beta {min_beta:.3f}",
    ]
    if errors:
        raise SystemExit("INVARIANT VIOLATIONS:\n" + "\n".join(errors))
    return report


def main() -> None:
    records = build_records()
    records.sort(key=lambda r: (r["brand"], r["product_line"], PROFILE_RANK[r["profile_class"]],
                                r["volume_cc"]))
    report = check_invariants(records)
    OUT.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n")
    brands = {}
    for r in records:
        brands.setdefault(r["brand"], 0)
        brands[r["brand"]] += 1
    print(f"wrote {OUT} — " + ", ".join(report))
    for b, n in sorted(brands.items()):
        print(f"  {b}: {n} SKUs")


if __name__ == "__main__":
    sys.exit(main())
