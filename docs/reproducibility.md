# Reproducibility Workflow

This repository treats every simulation as a reproducible scientific record.

## Reproducibility checklist per attempt

1. Immutable resolved inputs are stored under `inputs/`.
2. Full stdout and stderr are preserved under `logs/`.
3. Solver-native outputs are preserved under `raw_outputs/`.
4. Intermediate artifacts are preserved under `intermediate/`.
5. Normalized outputs are written separately under `normalized/`.
6. Validation artifacts are written under `validation/`.
7. Artifact manifest is generated under `manifests/artifacts_manifest.json`.
8. Checksums are generated under `manifests/checksums.sha256`.
9. Exactly one terminal marker is written.
10. Attempt finalization is atomic.

## Finalization command

```bash
PYTHONPATH=src python scripts/finalize_attempt.py --attempt-dir <attempt_dir> --terminal-status SUCCESS
```

## Integrity command

```bash
PYTHONPATH=src python scripts/verify_attempt_integrity.py --attempt-dir <attempt_dir>
```

## Archive command

```bash
PYTHONPATH=src python scripts/archive_attempt.py --attempt-dir <attempt_dir> --format tar.gz
```

## Archive verification

```bash
PYTHONPATH=src python scripts/verify_archive.py --archive <archive_path>
```

## Global preservation audit

```bash
PYTHONPATH=src python scripts/audit_preservation.py --runs-root data/runs
```

## Registry expectations

Each finalized attempt should append a machine-readable registry record containing at least:

- task, case, topology, operating-point, solver, attempt IDs
- numerical status
- preservation status
- runtime and objective (when available)
- validation status and max violations
- artifact count and total size
- creation timestamp

## Non-destructive policy

- Raw outputs must never be overwritten by normalized or derived outputs.
- Any parser/normalizer correction must produce versioned derived artifacts.
- Failed and nonconvergent runs remain preserved for analysis.
