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

- `scripts/download_sources.py`
- `scripts/inspect_sources.py`
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

## CLI module entrypoints

Equivalent module-backed entrypoints are exposed via package scripts:

- `pgdf-finalize-attempt`
- `pgdf-verify-attempt`
- `pgdf-audit-preservation`
- `pgdf-validate-layout`
