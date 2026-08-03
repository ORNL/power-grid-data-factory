# Manual Source Reproducibility Bundle

This directory is a git-tracked snapshot of manual TAMU source ingestion state.

## What is included

- `audit_case_sources.json`: full case-source audit snapshot.
- `repro_bundle.json`: machine-readable summary of manual cases, archive hashes, and copied metadata.
- `MANUAL_DOWNLOADS.md`: manual acquisition checklist snapshot.
- `manual_download_manifest.yaml`: case acquisition-status snapshot.
- `cases/<case_id>/source_manifest.yaml`: per-case registration metadata.
- `cases/<case_id>/checksums.sha256`: per-case archive checksum record.
- `cases/<case_id>/inventory.txt`: per-case file inventory snapshot.

## What is intentionally not included

Large manual archives and extracted payloads under `external/tamu/` are not tracked in git because `external/` is repository-local workspace state.

Use the hashes and manifests in this bundle to verify that local manual archives match the recorded ingestion state.

## Refresh procedure

From repository root:

```bash
python3.11 scripts/export_manual_source_bundle.py --config configs/sources.yaml
python3.11 scripts/audit_case_sources.py --config configs/sources.yaml --format json > data/imported/manual_sources/audit_case_sources.json
```
