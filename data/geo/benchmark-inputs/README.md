# Frozen map inputs

These losslessly compressed Natural Earth source files make the original
200-site map sweep reproducible without a pre-existing `tools/geo` vector set or
network access. `manifest.json` records the source URLs, uncompressed and packed
sizes, and SHA256 hashes. GeoJSON files are the exact inputs used by the original
sweep.

The shaded-relief raster (Natural Earth SR_HR.tif, 233 MB) is not committed. Its
manifest row records the source URL, size and SHA-256, and `external_path`
names where `python scripts/fetch_relief.py` saves it (tools/geo/relief/, gitignored;
the maps read the same file at runtime). measure.py verifies the fetched file's
size and SHA-256 and refuses a missing or different file with the fetch
instruction; it never runs without it.

Review screenshots belong in the QF-20 evidence directory as JPEG files under
300 KB. No video is included.

Natural Earth data are public domain. The application fetchers document the
original conversion of the vector archives. See the official
[terms](https://www.naturalearthdata.com/about/terms-of-use/) and
[disputed-area description](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-breakaway-disputed-areas/).
