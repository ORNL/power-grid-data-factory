#!/bin/bash
#SBATCH -J pgdf_repair_all
#SBATCH -A lrn070
#SBATCH -p batch
#SBATCH --qos=normal
#SBATCH -N 16
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=1
#SBATCH -t 36:00:00
#SBATCH -o /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.out
#SBATCH -e /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory/data/scratch/logs/slurm-%x-%j.err

set -euo pipefail
ulimit -c 0  # disable core dumps: Julia/Ipopt crashes were filling Lustre

ROOT=/lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
export REPAIR_ID=${REPAIR_ID:-riker_all_failed_r000_r005_ma27_ma57}
export ROUNDS=0-5
export ALL_CASES=1
export LINEAR_SOLVERS=ma27,ma57
export CAMPAIGN_PREFIX=repair_all_failed_r000_r005

exec bash "$ROOT/configs/slurm/riker_repair_activsg2000_case300.sbatch"