# First Real-Case Input Run with Registry Append

This walkthrough uses a tiny but concrete case input, executes AC-OPF through the `PowerModelsAdapter`, and runs a full preservation workflow including run-registry append.

It uses:

- `scripts/demo_real_case_run.py`

## What this script does

1. Creates a tiny canonical case JSON at:
   - `data/canonical/pglib_opf_case14_ieee_demo.json`
2. Creates an immutable attempt directory under `data/runs/ac_opf/.../attempts/`.
3. Writes required metadata files (`run.yaml`, `command.txt`, `command.json`, environment snapshot).
4. Writes resolved input into `inputs/resolved_case.json`.
5. Executes `solve_ac_opf` via `PowerModelsAdapter` and stores solver-native output.
6. Generates artifact manifest and checksums.
7. Finalizes attempt with terminal marker `SUCCESS`.
8. Verifies checksums.
9. Appends a run record to:
   - `data/runs/run_registry.jsonl`
   - `data/runs/run_registry.parquet`

## Run command

From repository root:

```bash
PYTHONPATH=src python scripts/demo_real_case_run.py
```

Expected output:

- JSON containing `ok: true`, `run_id`, and finalized `attempt_dir`.

## Validate outputs

```bash
PYTHONPATH=src python scripts/validate_run_layout.py --runs-root data/runs
PYTHONPATH=src python scripts/audit_preservation.py --runs-root data/runs
```

Check registry contents:

```bash
python - <<'PY'
import json
from pathlib import Path

p = Path('data/runs/run_registry.jsonl')
for line in p.read_text(encoding='utf-8').splitlines()[-3:]:
    print(json.loads(line)['run_id'])
PY
```

## Important note

This workflow now runs the adapter path to Julia. It is still a tiny synthetic case and intended for pipeline verification, not benchmark-quality labels.
