# Campaign Diversity Analysis

The campaign diversity audit quantifies whether a large execution count represents
meaningfully distinct solved states. It reads authoritative per-shard diversity
ledgers directly, avoiding the cumulative campaign ledger and its hundreds of
gigabytes of repeated append history.

## Repository placement

- `src/grid_data_factory/diversity/audit.py`: reusable metrics and streaming readers.
- `scripts/analyze_campaign_diversity.py`: reproducible command-line entrypoint.
- `configs/diversity_analysis.yaml`: versioned analysis parameters.
- `tests/test_diversity_audit.py`: synthetic metric and shard-discovery regression tests.
- `data/reports/diversity/<campaign>/<rounds>/`: protected, curated outputs.

Do not place generated reports under `data/outputs/`; that tier is disposable. Do
not put metric implementations in notebooks or `scripts/`; reusable scientific
logic belongs in the importable package and is covered by unit tests.

## Run the ultrascale audit

From the repository root:

```bash
.venv/bin/python scripts/analyze_campaign_diversity.py \
  --campaign-id ultrascale_3b \
  --rounds 0-5
```

The serial command is the reference implementation. For the production corpus,
install the optional MPI dependency using the compiler wrapper for the active
MPI stack, then submit the Andes template:

```bash
bash scripts/setup_andes_venv.sh
sbatch configs/slurm/andes_diversity_analysis_mpi.sbatch
```

Override resources with `sbatch` and analysis parameters through the environment:

```bash
sbatch -N 8 --ntasks-per-node=8 \
  --export=ALL,CAMPAIGN_ID=ultrascale_3b,ROUNDS=0-5,SAMPLE_SIZE=25000,BLOCK_SIZE=128 \
  configs/slurm/andes_diversity_analysis_mpi.sbatch
```

The MPI path deterministically assigns `ledgers[rank::size]`. Each rank streams
exact counters and retains its local lowest-hash sample candidates. Ranks first
exchange compact priority lists, derive the global sample threshold, and only
then gather eligible rows. Rank 0 merges counters, computes global nearest-neighbor
and clustering metrics, and writes the same report schema as the serial command.
For a fixed seed, results do not depend on ledger partitioning or MPI rank count.

The default output is:

```text
data/reports/diversity/ultrascale_3b/rounds_000-005/
  diversity_report.json
  diversity_report.html
  diversity_sample.csv
```

The JSON report records the campaign, rounds, Git commit, analysis schema,
configuration, missing-ledger count, and method limitations. The CSV is the
deterministic descriptor sample used for distance calculations. The HTML report
contains headline metrics, nearest-neighbor ECDF, similarity-cluster Lorenz curve,
exact topology-contingency heatmap, and categorical coverage table.

## What is exact and what is estimated

The audit streams every selected shard ledger. These values are exact:

- descriptor row count by round;
- case, dataset, topology-class, and contingency-order counts;
- topology-class by contingency-order joint counts and corpus shares;
- category entropy and Hill number for those low-cardinality fields.

Pairwise calculations use an order-independent hash sample controlled by
`sample_size` and `seed`:

- robust-IQR-scaled nearest-neighbor distance;
- near-duplicate rate;
- descriptor duplicate rate;
- similarity-cluster effective sample ratio;
- intrinsic dimension;
- active and near-active constraint-signature coverage.

Distances are only computed between samples with the same case, topology class,
and contingency order. This prevents incomparable grids and structural regimes
from creating artificial diversity. Numeric fields are median/IQR scaled within
each grid case before computing root-mean-square Euclidean distance, so large
systems do not compress meaningful variation in smaller systems.

## Threshold calibration

The default near-duplicate threshold is `0.02`. Treat it as a versioned analysis
parameter, not a universal physical constant. Calibrate it by generating known
small perturbations and choosing a threshold that separates operationally
equivalent states from changes the intended model should learn. For sensitivity
analysis, rerun with several thresholds:

```bash
for threshold in 0.01 0.02 0.05; do
  .venv/bin/python scripts/analyze_campaign_diversity.py \
    --campaign-id ultrascale_3b \
    --rounds 0-5 \
    --near-duplicate-threshold "$threshold" \
    --output-dir "data/reports/diversity/ultrascale_3b/threshold_${threshold}"
done
```

## Interpretation

Raw sample count should be reported together with:

- near-duplicate rate;
- effective sample count and ratio;
- median and lower-tail nearest-neighbor distances;
- intrinsic dimension;
- structural and active-constraint coverage entropy.

A high raw count with a low effective-sample ratio indicates repeated coverage of
the same regions. A nearest-neighbor ECDF concentrated near zero indicates local
redundancy. A bowed Lorenz curve indicates that a few similarity clusters dominate
the sampled corpus. The topology-contingency heatmap exposes empty or dominant
structural combinations. Its colors are log-scaled so common and rare cells remain
visible, while labels show exact counts and corpus shares.

This audit measures solved-state diversity. It does not replace canonical full-input
hashing or a grouped train/validation/test leakage audit. Before model evaluation,
keep related operating-point lineages, topology families, contingencies, and stress
trajectories in a single split and measure every validation/test sample's nearest
training neighbor.

## Scaling controls

Memory use is bounded by the deterministic sample and distance block. Increase
`sample_size` for tighter estimates and reduce `block_size` if memory is limited:

```bash
.venv/bin/python scripts/analyze_campaign_diversity.py \
  --campaign-id ultrascale_3b \
  --rounds 0-5 \
  --sample-size 25000 \
  --block-size 128
```

Missing ledgers fail the command by default. `--allow-missing-ledgers` is intended
only for explicitly labeled provisional reports while a campaign is still running.

Start with 16 to 32 ranks and compare elapsed time before increasing concurrency.
The scan is often limited by Lustre bandwidth and metadata service rather than CPU;
excessive ranks can make it slower. The supplied Andes template uses 32 ranks and
sets threaded numerical libraries to one thread per rank.
