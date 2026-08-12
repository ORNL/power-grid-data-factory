#!/bin/bash
#SBATCH -A lrn087
#SBATCH -J exago-single
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --gpus=1
#SBATCH -t 01:00:00

set -euo pipefail

ROOT="/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory"
cd "$ROOT"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

CASE="${1:-external/ExaGO/datafiles/case118.m}"
CASE_ABS="$(realpath "$CASE")"
CASE_STEM="$(basename "${CASE_ABS%.*}")"

PYTHONPATH="$ROOT/src" python3.11 - "$ROOT" "$CASE_STEM" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
runs_root = root / "data" / "runs"
case_id = sys.argv[2].strip().lower()
case_id = ''.join(ch if ch.isalnum() or ch in {'_','-'} else '_' for ch in case_id)
if not case_id:
    case_id = "case"

solver_id = "exago_ac_opf_gpu"
topology_id = "topology_000001_frontier_singleton"
operating_point_id = "op_000001_nominal"

from grid_data_factory.storage.layout import get_solver_directory, create_next_attempt_directory

solver_dir = get_solver_directory(
    runs_root=runs_root,
    task="ac_opf",
    case_id=case_id,
    topology_id=topology_id,
    operating_point_id=operating_point_id,
    solver_id=solver_id,
)
solver_dir.mkdir(parents=True, exist_ok=True)
in_progress_dir, attempt_id = create_next_attempt_directory(solver_dir)
print(in_progress_dir)
print(attempt_id)
PY

ATTEMPT_DIR_IN_PROGRESS="$(PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3.11 - "$ROOT" "$CASE_STEM" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
runs_root = root / 'data' / 'runs'
case_id = sys.argv[2].strip().lower()
case_id = ''.join(ch if ch.isalnum() or ch in {'_','-'} else '_' for ch in case_id)
if not case_id:
    case_id = 'case'

from grid_data_factory.storage.layout import get_solver_directory, create_next_attempt_directory
solver_dir = get_solver_directory(
    runs_root=runs_root,
    task='ac_opf',
    case_id=case_id,
    topology_id='topology_000001_frontier_singleton',
    operating_point_id='op_000001_nominal',
    solver_id='exago_ac_opf_gpu',
)
solver_dir.mkdir(parents=True, exist_ok=True)
in_progress_dir, _ = create_next_attempt_directory(solver_dir)
print(in_progress_dir)
PY
)"

mkdir -p "$ATTEMPT_DIR_IN_PROGRESS/logs"
mkdir -p "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs"

printf '%s\n' "Case: $CASE_ABS" > "$ATTEMPT_DIR_IN_PROGRESS/logs/solver_case.txt"
printf '%s\n' "Attempt dir: $ATTEMPT_DIR_IN_PROGRESS" >> "$ATTEMPT_DIR_IN_PROGRESS/logs/solver_case.txt"

export SRCDIR="$ROOT/external/ExaGO"
# frontierVariables.sh sources the build-time base.sh, which references
# EXTRA_CMAKE_ARGS assuming it already exists (a build-only convention). Under
# `set -u` that reference aborts the run, so seed the var and relax nounset
# just around the source; we only need the runtime module/env setup here.
export EXTRA_CMAKE_ARGS="${EXTRA_CMAKE_ARGS:-}"
set +u
source "$ROOT/external/ExaGO/buildsystem/clang-hip/frontierVariables.sh"
set -u
export LD_LIBRARY_PATH="/opt/rocm-6.3.1/lib:${LD_LIBRARY_PATH:-}"

EXAGO_CMD=(
  ./external/ExaGO/install/bin/opflow
  -netfile "$CASE_ABS"
  -opflow_model PBPOLRAJAHIOPSPARSE
  -opflow_solver HIOPSPARSEGPU
  -hiop_compute_mode gpu
  -hiop_verbosity_level 12
  -log_view
  -options_left no
  -print_output 1
  -save_output "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_solution"
)
printf '%q ' "${EXAGO_CMD[@]}" > "$ATTEMPT_DIR_IN_PROGRESS/logs/solver_command.txt"
printf '\n' >> "$ATTEMPT_DIR_IN_PROGRESS/logs/solver_command.txt"
env | grep -E '^(LD_LIBRARY_PATH|PATH|SLURM|ROCM|HIP|PETSC|OMP|MKL|CUDA)=' | sort > "$ATTEMPT_DIR_IN_PROGRESS/logs/solver_environment.txt" || true

set +e
srun --exclusive -N1 -n1 --gpus=1 \
  "${EXAGO_CMD[@]}" \
  >"$ATTEMPT_DIR_IN_PROGRESS/logs/solver_stdout.log" \
  2>"$ATTEMPT_DIR_IN_PROGRESS/logs/solver_stderr.log"
SOLVER_RC=$?
set -e

printf '%s\n' "solver_exit_code=$SOLVER_RC" > "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_summary.txt"
printf '%s\n' "solver_case=$CASE_ABS" >> "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_summary.txt"
printf '%s\n' "attempt_dir=$ATTEMPT_DIR_IN_PROGRESS" >> "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_summary.txt"
printf '%s\n' "captured_files:" >> "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_summary.txt"
find "$ATTEMPT_DIR_IN_PROGRESS" -maxdepth 3 -print | sort >> "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_summary.txt"

{
  echo "solver_exit_code=$SOLVER_RC"
  echo "case=$CASE_ABS"
  echo "attempt_dir=$ATTEMPT_DIR_IN_PROGRESS"
  echo "command=$(cat "$ATTEMPT_DIR_IN_PROGRESS/logs/solver_command.txt")"
  echo "stdout_log=$ATTEMPT_DIR_IN_PROGRESS/logs/solver_stdout.log"
  echo "stderr_log=$ATTEMPT_DIR_IN_PROGRESS/logs/solver_stderr.log"
  echo "saved_solution_dir=$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_solution"
  echo "artifacts:"
  find "$ATTEMPT_DIR_IN_PROGRESS" -maxdepth 3 -print | sort
} > "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_dump_manifest.txt"

find "$ATTEMPT_DIR_IN_PROGRESS" -maxdepth 3 -type f -printf '%p\t%s\n' | sort > "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_artifact_inventory.txt"

PYTHONPATH="$ROOT/src" python3.11 - "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/solver_solution.m" "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/dual_bus_multipliers.csv" "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/dual_multipliers.txt" <<'PY'
import csv
import re
import sys
from pathlib import Path

sol_path = Path(sys.argv[1])
csv_path = Path(sys.argv[2])
summary_path = Path(sys.argv[3])

summary_lines = [
    "=== extracted ExaGO bus multipliers from solver_solution.m ===",
    f"solution_file={sol_path}",
]

if not sol_path.exists():
    summary_lines.append("status=no solver_solution.m found")
    summary_path.write_text("\n".join(summary_lines) + "\n")
    raise SystemExit(0)

text = sol_path.read_text(errors='ignore')
match = re.search(r"mpc\.bus\s*=\s*\[(.*?)\];", text, re.S)
rows = []
if match:
    block = match.group(1)
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('%') or line.startswith(']'):
            continue
        line = line.rstrip(';').strip()
        if not line:
            continue
        vals = line.split()
        if len(vals) < 4:
            continue
        bus_id = vals[0]
        mult_p = vals[-4]
        mult_q = vals[-3]
        rows.append((bus_id, mult_p, mult_q))

with csv_path.open('w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['bus', 'mult_Pmis', 'mult_Qmis'])
    writer.writerows(rows)

if rows:
    summary_lines.append('status=success')
    summary_lines.append('bus,mult_Pmis,mult_Qmis')
    for bus_id, mult_p, mult_q in rows:
        summary_lines.append(f'{bus_id},{mult_p},{mult_q}')
else:
    summary_lines.append('status=no_bus_multipliers_found')
    summary_lines.append('note=solver_solution.m did not contain a parseable mpc.bus multiplier block')

summary_path.write_text("\n".join(summary_lines) + "\n")
PY

if [ ! -s "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/dual_bus_multipliers.csv" ]; then
  echo "ERROR: missing or empty dual multiplier CSV at $ATTEMPT_DIR_IN_PROGRESS/raw_outputs/dual_bus_multipliers.csv" >&2
  exit 1
fi

python3.11 - "$ATTEMPT_DIR_IN_PROGRESS/raw_outputs/dual_bus_multipliers.csv" <<'PY'
import csv
import sys
from pathlib import Path

csv_path = Path(sys.argv[1])
with csv_path.open(newline='') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

if not rows:
    raise SystemExit(f"ERROR: CSV is empty or missing header: {csv_path}")

bad = []
for idx, row in enumerate(rows, start=1):
    for key in ('bus', 'mult_Pmis', 'mult_Qmis'):
        if key not in row:
            bad.append((idx, f'missing field {key}'))
            break
        value = (row.get(key) or '').strip()
        if value == '':
            bad.append((idx, f'empty value for {key}'))
            break
        try:
            float(value)
        except ValueError:
            bad.append((idx, f'non-numeric value for {key}: {value!r}'))
            break

if bad:
    raise SystemExit(f"ERROR: invalid multiplier CSV rows: {bad[:5]} (file={csv_path})")

print(f"validated_multiplier_rows={len(rows)}")
PY

if [ "$SOLVER_RC" -eq 0 ]; then
  MARKER="SUCCESS"
else
  MARKER="FAILED"
fi

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3.11 - "$ATTEMPT_DIR_IN_PROGRESS" "$MARKER" <<'PY'
from pathlib import Path
import sys

attempt_dir = Path(sys.argv[1])
marker = sys.argv[2]
from grid_data_factory.storage.layout import finalize_attempt_directory
final_dir = finalize_attempt_directory(attempt_dir)
(final_dir / marker).touch()
print(final_dir)
PY

exit "$SOLVER_RC"
