# Script Reference

This page summarizes script entrypoints currently included in the scaffold.

## Preservation and integrity

- `scripts/finalize_attempt.py`
  - Generates manifest/checksums, writes terminal marker, finalizes attempt directory.
- `scripts/verify_attempt_integrity.py`
  - Verifies file checksums for an attempt.
- `scripts/archive_attempt.py`
  - Creates a compressed archive for an attempt.
- `scripts/verify_archive.py`
  - Validates archive readability and checksum metadata.
- `scripts/audit_preservation.py`
  - Audits attempt completeness and marker consistency across `data/runs`.
- `scripts/validate_run_layout.py`
  - Confirms expected top-level run directories exist.

## Workflow command stubs

The following are scaffolded placeholders and should be implemented for production execution:

- `scripts/canonicalize_cases.py`
- `scripts/register_case.py`
- `scripts/create_topology.py`
- `scripts/create_operating_point.py`
- `scripts/enumerate_contingencies.py`
- `scripts/screen_contingencies.py`
- `scripts/run_pf.py`
- `scripts/run_dc_opf.py`
- `scripts/run_ac_opf.py`
- `scripts/run_scopf.py`
- `scripts/compare_solver_consistency.py`
- `scripts/phase1_gate.py`

## Source onboarding helpers

- `scripts/inspect_sources.py`
  - Reports configured source presence, git head (for git repos), direct-download file presence, and manual-case raw file status.
- `scripts/download_sources.py`
  - Clones missing git-based sources from `configs/sources.yaml`.
  - Supports pinned git checkout and recursive clone configuration.
  - Downloads direct URL artifacts with retries/resume (`curl --continue-at -`) when `--download-files` is enabled.
  - Skips already-verified archives and records source URLs, timestamps, SHA-256, and sizes.
  - Optionally extracts archives and writes per-source `source_manifest.yaml`.
  - Writes provenance report JSON (default: `data/imported/source_provenance.json`).
- `scripts/prepare_manual_downloads.py`
  - Creates TAMU case-folder structure and emits `external/tamu/MANUAL_DOWNLOADS.md`.
  - Writes `external/tamu/manual_download_manifest.yaml` with per-case acquisition status.
- `scripts/register_manual_download.py`
  - Registers a manually downloaded archive for a specific case.
  - Preserves the original archive under `external/tamu/<case_id>/raw/`, computes SHA-256, and writes inventory/checksum files.
  - Optionally extracts archive contents and writes case-level `source_manifest.yaml`.
- `scripts/validate_sources.py`
  - Validates configured sources and emits policy statuses.
  - Supports statuses: `MISSING`, `DOWNLOADED_UNREGISTERED`, `REGISTERED`, `EXTRACTED`, `VALIDATED`, `CHECKSUM_MISMATCH`, `UNSUPPORTED_FORMAT`, `INCOMPLETE_FOR_PF`, `INCOMPLETE_FOR_OPF`.
  - Marks validation non-OK when required manual cases are not validated.

## Build and machine-profile helpers

- `scripts/configure_exago_build.py`
  - Configures machine-scoped ExaGO build/install directories under `external/ExaGO/builds/<profile>/`.
  - Supports optional configure/build/install execution and emits resolved paths in JSON.

## CLI module entrypoints

Equivalent module-backed entrypoints are exposed via package scripts:

- `pgdf-finalize-attempt`
- `pgdf-verify-attempt`
- `pgdf-audit-preservation`
- `pgdf-validate-layout`
