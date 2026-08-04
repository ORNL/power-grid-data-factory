# First Reproducible Run Walkthrough

This walkthrough demonstrates one full preservation-first attempt lifecycle in the scaffold, from creating an attempt directory to finalization, integrity verification, and optional archival.

## Goal

Produce one immutable attempt record under:

- `data/runs/ac_opf/<case_id>/<topology_id>/<operating_point_id>/<solver_id>/attempts/<attempt_id>/`

Layout note:

- Runs use the compact path above.

and verify that preservation artifacts are generated.

## 0. Prerequisites

- Python 3.10+ environment with project installed (`pip install -e .`).
- Repository root:
  - `/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory`

## 1. Create an in-progress attempt directory

Run this Python snippet from repository root:

```bash
PYTHONPATH=src python - <<'PY'
from pathlib import Path
from grid_data_factory.storage.layout import get_solver_directory, create_attempt_directory
from grid_data_factory.storage.naming import format_attempt_id

runs_root = Path("data/runs")
solver_dir = get_solver_directory(
    runs_root=runs_root,
    task="ac_opf",
    case_id="pglib_opf_case14_ieee",
    topology_id="topology_000000_baseline",
    operating_point_id="op_000000_baseline",
    solver_id="powermodels_ac_opf_ipopt",
)
attempt = create_attempt_directory(solver_dir, format_attempt_id(1))
print(attempt)
PY
```

Expected output path ends with:

- `.attempt_000001.in_progress`

## 2. Populate minimal required files

Create basic metadata and logs for this scaffold run:

```bash
ATTEMPT=$(find data/runs/ac_opf -type d -name '.attempt_000001.in_progress' | head -1)

cat > "$ATTEMPT/run.yaml" <<'YAML'
run_id: demo-run-0001
task: ac_opf
case_id: pglib_opf_case14_ieee
topology_id: topology_000000_baseline
operating_point_id: op_000000_baseline
contingency_set_id: null
solver_id: powermodels_ac_opf_ipopt
attempt_id: attempt_000001
numerical_status: not_run
preservation_status: in_progress
YAML

echo "python scripts/run_ac_opf.py ..." > "$ATTEMPT/command.txt"
cat > "$ATTEMPT/command.json" <<'JSON'
{"executable":"python","args":["scripts/run_ac_opf.py"],"note":"scaffold placeholder"}
JSON

cat > "$ATTEMPT/environment/environment.json" <<'JSON'
{"python":"3.x","note":"demo environment snapshot"}
JSON

touch "$ATTEMPT/logs/stdout.log"
touch "$ATTEMPT/logs/stderr.log"

echo '{"success": false, "termination_status": "not_implemented"}' > "$ATTEMPT/raw_outputs/solver_result/result.json"
```

## 3. Finalize the attempt (manifest + checksums + terminal marker)

```bash
PYTHONPATH=src python scripts/finalize_attempt.py --attempt-dir "$ATTEMPT" --terminal-status SUCCESS
```

This performs:

- artifact manifest generation
- checksum file generation
- terminal marker write
- atomic rename from `.attempt_000001.in_progress` to `attempt_000001`

## 4. Verify attempt integrity

```bash
FINAL_ATTEMPT=$(dirname "$ATTEMPT")/attempt_000001
PYTHONPATH=src python scripts/verify_attempt_integrity.py --attempt-dir "$FINAL_ATTEMPT"
```

Expected result:

- `ok: true`

## 5. Optional: archive and verify archive

```bash
PYTHONPATH=src python scripts/archive_attempt.py --attempt-dir "$FINAL_ATTEMPT" --format tar.gz

ARCHIVE=$(dirname "$FINAL_ATTEMPT")/attempt_000001.tar.gz
PYTHONPATH=src python scripts/verify_archive.py --archive "$ARCHIVE"
```

## 6. Run global preservation audit

```bash
PYTHONPATH=src python scripts/audit_preservation.py --runs-root data/runs
```

Use this regularly after batch execution to detect missing manifests, missing checksums, or marker inconsistencies.

## What this demonstrates

- Deterministic hierarchy construction.
- Immutable attempt finalization semantics.
- Preservation-first artifact capture.
- Integrity checks as a required gate.

## Next step after this walkthrough

Replace placeholder solver output with real AC-OPF execution through the PowerModels adapter, then append a run-registry record using the storage registry API.
