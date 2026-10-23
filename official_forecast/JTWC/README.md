# JTWC official forecast data status

## Archived data

`best_track/bwp1945/` contains 26 ATCF-format records for the 1945 western North Pacific basin.

- Basin: `WP`
- Record type: `BEST`
- Forecast lead: `tau=0`
- Content: best-track observations, not operational forecast positions
- Wind field: ATCF convention is normally knots; retain the original value and unit metadata before comparison

## Important limitation

These files cannot be used as a JTWC forecast benchmark because they do not contain `tau>0` forecasts or an operational technique such as `OFCL`/`JTWC`. They may be used for parser validation and cross-source best-track checking only.

Do not report model-versus-JTWC forecast scores from this directory.
