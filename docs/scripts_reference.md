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
- `scripts/create_operating_point.py`
- `scripts/enumerate_contingencies.py`
- `scripts/screen_contingencies.py`
- `scripts/run_pf.py`
- `scripts/run_dc_opf.py`
- `scripts/run_scopf.py`
- `scripts/compare_solver_consistency.py`
- `scripts/phase1_gate.py`

Implemented workflow command:

- `scripts/run_ac_opf.py`
  - Runs AC-OPF through `PowerModelsAdapter` for selected MATPOWER cases.
  - Creates full preservation-first attempt directories under `data/runs/ac_opf/...`.
  - Writes normalized outputs, validation placeholders, manifests/checksums, terminal marker, and appends run registry records.
- `scripts/run_exago_ac_opf.py`
  - Runs AC-OPF through ExaGO OPFLOW for selected MATPOWER cases.
  - Parses OPFLOW text output into structured `raw_result.solution` fields (`bus`, `branch`, `gen`) for downstream HydraGNN-style OPF training conversion.
  - Creates full preservation-first attempt directories and appends run registry records with runtime metadata.
- `scripts/run_pandapower_ac_opf.py`
  - Runs AC-OPF through pandapower for selected MATPOWER cases.
  - Creates full preservation-first attempt directories and appends run registry records with runtime metadata.
- `scripts/shard_selected_candidates.py`
  - Deterministically shards selected-candidate JSONL files by `candidate_id` ordering.
  - Produces fixed shard files and a manifest for map-stage parallel execution.
  - Supports coverage gates (for example by dataset/topology) and deterministic pool-based backfill before sharding.
- `scripts/reduce_campaign_shards.py`
  - Deterministically merges shard campaign ledgers and shard AC execution reports back into the target campaign.
  - Aggregates active-constraint ledger rows by `(constraint_family, component_id)` and writes a reduce report marker per round.
- `scripts/launch_ultrascale_campaign.py`
  - Plans or submits chained map/reduce Slurm rounds with full parameter exports (budget, sharding, coverage gate, backfill, solver runtime settings).
  - Supports dependency-chained submission (`afterok`) for continuous multi-round campaigns.

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
- `scripts/audit_case_sources.py`
  - Audits per-case source identity metadata and local availability from `configs/sources.yaml`.
  - Reports source lineage, acquisition mode, expected source file, checksum, TAMU correspondence, and PF/DC-OPF/AC-OPF readiness.
  - Enforces explicit non-equivalence metadata for similar-size but distinct grid families.
- `scripts/export_manual_source_bundle.py`
  - Exports a git-tracked reproducibility bundle from manual TAMU case ingestion state.
  - Copies per-case manifests, checksums, and inventories into `data/imported/manual_sources/cases/`.
  - Writes `data/imported/manual_sources/repro_bundle.json` with archive checksums and metadata.
- `scripts/convert_go_challenge_to_matpower.py`
  - Converts GO Challenge PSS/E scenario bundles (`.raw` with optional `.rop`) into MATPOWER `.m` files.
  - Input archives are expected to be preserved original downloads from the official DOE catalog entry: https://catalog.data.gov/dataset/arpa-e-grid-optimization-go-competition-challenge-1?utm_source=chatgpt.com
  - Supports batch conversion directly from `external/go_challenge1/raw/Challenge_1*.zip` archives.
  - Writes conversion summary/report JSON (default: `data/analysis/go_challenge1_conversion_report.json`).

## Topology creation helper

- `scripts/create_topology.py`
  - Parses MATPOWER-format case files (currently default-mapped for `pglib_opf`).
  - Emits a topology JSON artifact under `data/topology_registry/<case_id>/topology_<index>_<description>.json`.
  - Appends a registry record to `data/topology_registry/topology_registry.jsonl` with source file and element counts.

## Build and machine-profile helpers

- `scripts/configure_exago_build.py`
  - Configures machine-scoped ExaGO build/install directories under `external/ExaGO/builds/<profile>/`.
  - Supports optional configure/build/install execution and emits resolved paths in JSON.
- `scripts/run_exago_andes_opflow.sh`
  - Loads the known-good Andes module stack and runs isolated ExaGO `opflow`.
  - Defaults to case9 IPOPT smoke test when no CLI arguments are provided.
  - Accepts custom `opflow` arguments for alternate netfiles and solver options.

## CLI module entrypoints

Equivalent module-backed entrypoints are exposed via package scripts:

- `pgdf-finalize-attempt`
- `pgdf-verify-attempt`
- `pgdf-audit-preservation`
- `pgdf-validate-layout`
