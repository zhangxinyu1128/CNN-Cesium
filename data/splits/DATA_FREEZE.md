# Data Scope and Split Freeze

Freeze date: 2026-09-30

This record freezes the current track-only train/validation/test assignment and documents the ERA5 pilot coverage. The exact storm roster remains in `storm_splits.json`; do not regenerate it for model tuning or evaluation.

## Frozen track split

- Source: `data/` annual indexes and best-track JSON files.
- Source fingerprint: `68fa166de12c638807258b67091c1bc996ba8c75a600ffdad013ece731a2cf9e`.
- Split file: `storm_splits.json`.
- Split file SHA-256: `2836e1da45f6de2dc2081cdb8c92eccf2042a483215cb83c9e5831d8736ac00e`.
- The preprocessing manifest records the same source fingerprint and split-file SHA-256.
- Assignment rule: train 1945-2016, validation 2017-2019, test 2020-2025.
- Assigned storm counts: train 1,687; validation 94; test 154.
- After track validation and window construction, usable storms are train 1,568; validation 69; test 114, yielding 33,761 / 926 / 1,670 windows respectively.

The roster, year boundaries, and test membership are frozen. Any future alternative split must be stored as a separate, explicitly named experiment and must not replace this split or be used to retune the reported test results.

## Track data audit

Audit output: `artifacts/acceptance/day1_track_audit_20260930.json`.

- Coverage: 1945-2025; 1,937 track files, 1,935 valid tracks, 73,388 observation points.
- Invalid coordinates, duplicate timestamps, and non-monotonic steps: 0.
- Invalid track files: `196118.json` and `197319.json` (no track object).
- Missing values: time 0; longitude 0; latitude 0; speed 15 (0.020%); power 15 (0.020%); pressure 2,083 (2.838%); movement direction 49,646 (67.649%); movement speed 49,921 (68.023%); radius7 48,040 (65.460%); radius10 52,488 (71.521%).
- The high missingness in movement direction/speed and wind radii is retained as missing data; do not silently impute those fields for the baseline. Pressure is not required by the current baseline feature contract.

## ERA5 scope and matched pilot

- The existing full-year ERA5 file is `ERA5data/data.grib`; provenance and checksum are recorded in `ERA5data/SOURCE.json`.
- Verified variables are U/V wind components on 200, 300, 500, 700, and 850 hPa levels, at 00/06/12/18 UTC, on a 0.25-degree grid over 45N-0N and 100E-180E. These are winds on pressure levels, not a surface-pressure variable.
- Historical ERA5 artifacts/metadata cover discontinuous blocks 1962-1964, 1972-1974, 1982-1984, 1992-1994, 2002-2004, and 2012-2014, plus a full-year 2025 file. Do not infer continuous ERA5 coverage from the 1945-2025 track archive; validation years 2017-2019 and test years 2020-2024 have no corresponding ERA5 coverage in this folder.
- Paired point pilot: `artifacts/era5/track_points_2025.jsonl`, summary `artifacts/era5/sampling_summary_2025.json`.
- Of 877 requested 2025 synoptic track observations, 877 rows were sampled and 849 have complete U/V values at all five levels (96.807%); 28 are incomplete.
- All 32 2025 storm IDs represented in the pilot belong to the frozen test split. This is a held-out coverage pilot, not a training/validation split for a trained fusion model.

## Rules for subsequent experiments

1. Keep `storm_splits.json` and its SHA-256 unchanged for all reported track-only results.
2. Compare track-only and ERA5-fusion models on the exact same ERA5-paired test windows; report the paired sample count and exclude neither model selectively.
3. Build any meteorology-specific train/validation assignment as a separate file after historical ERA5 coverage is fully verified and storm/time matches are computed. Never move storms out of the frozen test set to improve scores.
4. Do not claim ERA5 fusion is trained until aligned features, leakage checks, model training, and an evaluation on the frozen paired test subset are complete.
