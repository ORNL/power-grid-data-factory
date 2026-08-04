# Slurm Configs

Place cluster-specific submission templates and resource presets here.

## Campaign-Driven Scheduler Template

`configs/slurm/andes_powermodels_acopf_small_10n_36h.sbatch` now runs the
adaptive campaign round workflow instead of sweeping all `.m` files.

Execution flow:

1. Optional bootstrap (candidate generation, contingency expansion, screening, queue-based selection).
2. AC-OPF execution for selected candidates only.
3. Ledger/report updates under `data/campaigns/<campaign_id>/round_summaries/`.

### Submit Example

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
sbatch \
	--export=ALL,CAMPAIGN_ID=pilot_pglib_14_57_118,ROUND_INDEX=0,BUDGET=600,SEED=7 \
	configs/slurm/andes_powermodels_acopf_small_10n_36h.sbatch
```

### Key Environment Overrides

- `CAMPAIGN_ID` (default: `pilot_pglib_14_57_118`)
- `ROUND_INDEX` (default: `0`)
- `CONFIG` (default: `configs/campaign_default.yaml`)
- `CASES` (space-separated case ids used only when `RUN_BOOTSTRAP=1`)
- `PER_CASE` (default: `500`)
- `SAMPLER` (default: `latin_hypercube`)
- `BUDGET` (default: `600`)
- `SEED` (default: `7`)
- `SOLVER_ID` (default: `powermodels_ac_opf_ipopt_campaign`)
- `TIMEOUT_S` (default: `1200`)
- `RUNS_ROOT` (default: `data/runs`)
- `RUN_BOOTSTRAP` (`1` to generate/select candidates in-job, `0` to reuse existing selected JSONL)
- `MAX_CANDIDATES` (default: `0`, meaning no cap)
- `CONTINUE_ON_ERROR` (`1` to continue solving candidates after failures)

### Reusing Existing Round Selection

If `data/campaigns/<campaign_id>/round_summaries/round_<idx>_selected_candidates.jsonl`
already exists, run with `RUN_BOOTSTRAP=0` to skip regeneration.

## Map/Reduce Slurm Template

`configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch` runs a
parallel map/reduce variant:

1. Optional bootstrap and round selection.
2. Deterministic sharding of selected candidates, with optional coverage enforcement and pool-based backfill.
3. Parallel shard execution with dynamic shard pickup (workers claim next unfinished shard when they complete).
4. Deterministic reducer merge into the target campaign ledgers.

### Submit Example (Map/Reduce)

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
sbatch \
	--export=ALL,CAMPAIGN_ID=pilot_pglib_14_57_118,ROUND_INDEX=0,BUDGET=600,SEED=7,SHARD_COUNT=160 \
	configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch
```

Additional map/reduce overrides:

- `SHARD_COUNT` (default: `SLURM_NTASKS`)
- `COVERAGE_KEYS` (default: `dataset,topology_id`)
- `MIN_PER_BUCKET` (default: `1`)
- `ENFORCE_COVERAGE` (default: `1`)
- `BACKFILL_FROM_POOL` (default: `1`)
- `POOL_JSONL` (default: `data/campaigns/<campaign_id>/round<idx>_screened_candidates.jsonl`)
- `MAX_BACKFILL_ADDITIONS` (default: `0`, unlimited)
- `SCORE_KEY` (default: `novelty_score`)
- `RESUME` (default: `0`; set `1` to resume an interrupted round)

Reducer output report:

- `data/campaigns/<campaign_id>/round_summaries/round_<idx>_mapreduce_reduce_report.json`

### Resuming an Interrupted Round

Set `RESUME=1` to resume a round whose shards already exist (for example after a
job hit its wall clock). When resuming, the job:

1. Skips bootstrap and re-sharding and reuses the existing shard files.
2. Skips any shard with a `queue/done/<shard>` completion marker.
3. Passes `--resume` to the map script, so partially-completed shards skip
   configurations that already have a finalized attempt directory.
4. Marks each shard done only after its map call succeeds.

The reduce stage stays deterministic, so a resumed round produces the same
merged ledgers and round marker.

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
sbatch \
	--export=ALL,CAMPAIGN_ID=ultrascale_60m,ROUND_INDEX=2,RESUME=1,BUDGET=6000000 \
	configs/slurm/andes_powermodels_acopf_mapreduce_10n_36h.sbatch
```

Prefer the top-level driver (below), which detects the correct round and sets
`RESUME=1` automatically.

### Top-Level Driver (Auto-Resume and Chaining)

`scripts/drive_campaign.py` finds the furthest incomplete round (lowest round
whose reduce marker is missing or not `ok`) and resubmits it with `RESUME=1`.
With `--chain` it also queues every remaining round with `afterok` dependencies
for unattended completion. See
[docs/resumable_campaigns.md](../../docs/resumable_campaigns.md).

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
# resume just the current round
PYTHONPATH=src python3.11 scripts/drive_campaign.py \
	--campaign-id ultrascale_60m --rounds 10 --total-budget 60000000 --set MAX_K=10 \
	--nodes 64 --ntasks-per-node 16 --cpus-per-task 1 --time 36:00:00

# resume and chain all remaining rounds
PYTHONPATH=src python3.11 scripts/drive_campaign.py \
	--campaign-id ultrascale_60m --rounds 10 --total-budget 60000000 --set MAX_K=10 \
	--nodes 64 --ntasks-per-node 16 --cpus-per-task 1 --time 36:00:00 --chain
```

## Ultra-Scale Setup (Hundreds of Millions)

Provided artifacts:

- `configs/campaign_ultrascale.yaml` (high-throughput adaptive policy profile)
- `configs/slurm/ultrascale_cases.txt` (large default case set)
- `scripts/launch_ultrascale_campaign.py` (round planner/submission orchestrator)

### Dry-run Planner (no submit)

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYTHONPATH=src python3.11 scripts/launch_ultrascale_campaign.py \
	--campaign-id ultrascale_v1 \
	--config configs/campaign_ultrascale.yaml \
	--cases-file configs/slurm/ultrascale_cases.txt \
	--rounds 20 \
	--start-round 0 \
	--seed-start 700 \
	--budget 120000 \
	--budget-step 5000 \
	--per-case 25000 \
	--sampler sobol \
	--shard-count 160 \
	--nodes 10 \
	--ntasks-per-node 16 \
	--cpus-per-task 1 \
	--time 36:00:00
```

### Submit Chained Rounds

```bash
cd /lustre/orion/lrn070/proj-shared/mlupopa/OPF/power_grid_data_factory
PYTHONPATH=src python3.11 scripts/launch_ultrascale_campaign.py \
	--campaign-id ultrascale_v1 \
	--config configs/campaign_ultrascale.yaml \
	--cases-file configs/slurm/ultrascale_cases.txt \
	--rounds 20 \
	--start-round 0 \
	--seed-start 700 \
	--budget 120000 \
	--budget-step 5000 \
	--per-case 25000 \
	--sampler sobol \
	--shard-count 160 \
	--nodes 10 \
	--ntasks-per-node 16 \
	--cpus-per-task 1 \
	--time 36:00:00 \
	--submit \
	--chain
```
