# Production Readiness Checklist

Use this checklist as the go/no-go policy before starting real data collection campaigns.

Mark each item `PASS` or `FAIL`.

## 1) Environment and machine profiles

- [ ] `PASS` Python runtime selected and project dependencies installed.
- [ ] `PASS` Julia profile selected for the current machine (`andes`, `frontier`, or `local`).
- [ ] `PASS` ExaGO machine-scoped build profile selected (if ExaGO workflow is used).

Commands:

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
python3.11 --version
PYTHONPATH=src python3.11 -c "import pydantic,yaml,pandas,pyarrow; print('python_deps_ok')"

module load julia/1.8.2
export JULIA_DEPOT_PATH=$PWD/.julia_depot_andes_profile
julia --project=julia/lockfiles/andes -e 'using Pkg; Pkg.instantiate()'

PYTHONPATH=src python3.11 scripts/configure_exago_build.py --exago-root external/ExaGO --preset frontier-gpu --dry-run
```

## 2) Run layout and preservation baseline

- [ ] `PASS` run layout validation succeeds.
- [ ] `PASS` preservation audit succeeds.

Commands:

```bash
PYTHONPATH=src python3.11 scripts/validate_run_layout.py --runs-root data/runs
PYTHONPATH=src python3.11 scripts/audit_preservation.py --runs-root data/runs
```

Required outcome:

- `validate_run_layout` returns `{"ok": true, "missing": []}`.
- `audit_preservation` returns `"ok": true`.

## 3) Source availability and provenance

- [ ] `PASS` required external datasets/repos are present locally.
- [ ] `PASS` source provenance records are available (URL, commit/checksum, timestamp, license note).

Reference:

- `docs/external_sources.md`
- `configs/sources.yaml`

## 4) Workflow implementation status

- [ ] `PASS` production path does not depend on stub workflow scripts.
- [ ] `PASS` source ingestion, case preparation, and run launch scripts are implemented for selected task(s).

Current scaffold warning: several workflow scripts are placeholders and must be implemented before production.

Reference:

- `docs/scripts_reference.md`

## 5) Solver calibration gate (Phase 1)

- [ ] `PASS` solver consistency report generated.
- [ ] `PASS` all required checks in phase1 gate pass.

Command example:

```bash
PYTHONPATH=src python3.11 scripts/phase1_gate.py \
  --config configs/phase1_calibration.yaml \
  --exago-root external/ExaGO \
  --build-profile frontier \
  --runs-root data/runs
```

Required outcome:

- Exit code `0`.
- Report indicates `"ok": true`.

## 6) Attempt lifecycle and integrity guarantees

- [ ] `PASS` every attempt is finalized via `finalize_attempt.py` with one terminal marker.
- [ ] `PASS` every attempt passes `verify_attempt_integrity.py`.
- [ ] `PASS` archive + archive verification policy applied if enabled.
- [ ] `PASS` run registry append is functioning.

Commands:

```bash
PYTHONPATH=src python3.11 scripts/finalize_attempt.py --attempt-dir <attempt_dir> --terminal-status SUCCESS
PYTHONPATH=src python3.11 scripts/verify_attempt_integrity.py --attempt-dir <attempt_dir>
PYTHONPATH=src python3.11 scripts/archive_attempt.py --attempt-dir <attempt_dir> --format tar.gz
PYTHONPATH=src python3.11 scripts/verify_archive.py --archive <archive_path>
```

Reference:

- `docs/reproducibility.md`
- `configs/preservation.yaml`

## 7) Production campaign controls

- [ ] `PASS` retry/timeout/failure-handling policy is defined.
- [ ] `PASS` job orchestration profile exists for target machine (SLURM or equivalent).
- [ ] `PASS` monitoring plan exists for active campaign health.
- [ ] `PASS` stop conditions and rollback criteria are documented.

Reference:

- `configs/slurm/`
- `docs/three_solver_runbook.md`

## Final go/no-go decision

- [ ] `GO` All sections above are `PASS`.
- [ ] `NO-GO` Any section above is `FAIL`.

Production collection starts only when `GO` is checked.