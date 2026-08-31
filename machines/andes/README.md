# Andes — machine-specific setup

Machine: `andes.olcf.ornl.gov` (CPU-only, x86 Zen2, RHEL 8)
Account: LRN070  
Partition: `batch`

---

## Spack setup (one-time, per user)

The system spack is `/sw/andes/spack-configs/spack-v0.23/bin/spack`.

**Problem**: spack v0.23 cannot parse `spec: gcc@X languages:='...'` in
`packages.yaml` (raises "a single spec was requested, but parsed more than one").
The fix is to strip the `languages:=` variant from the external gcc entries.

**Problem**: The default spack install root is `/tmp/mlupopa/spack-install`, which
is ephemeral and wiped between node reuses. All spack-installed libraries must
live on a persistent filesystem.

Apply the corrected configs from this directory:

```bash
cp machines/andes/spack/config.yaml   ~/.spack/config.yaml
cp machines/andes/spack/packages.yaml ~/.spack/packages.yaml
cp machines/andes/spack/packages.yaml ~/.spack/bootstrap/config/packages.yaml
mkdir -p ~/spack-cache/{source,misc}
```

---

## IPOPT install (one-time, per user)

IPOPT is not available as a system module on Andes. Install it persistently.

**Important**: do NOT use `spack install ipopt@3.14.14` directly — spack's parallel
scheduler hangs on the NFS install tree lock when building the openmpi→ipopt chain.
Install the chain package-by-package instead:

```bash
SPACK=/sw/andes/spack-configs/spack-v0.23/bin/spack
$SPACK install openmpi@5.0.5
$SPACK install netlib-scalapack
$SPACK install mumps@5.7.3
$SPACK install ipopt@3.14.14
$SPACK find -p ipopt   # note the install prefix/lib path
```

Installed 2026-08-31:
- `openmpi-5.0.5-j2hwgzcjuz5gzvrh7ihucnx66livguyn`
- `mumps-5.7.3-ih4sk7scwz3yvn3xxv5xnrgawvvxzrvs`
- `ipopt-3.14.14-ovpy35gemn7o5az725b5cn5i6663pv5l`

`IPOPT_LIB_DIR` = `~/spack-packages/linux-rhel8-zen2/gcc-9.3.0/ipopt-3.14.14-ovpy35gemn7o5az725b5cn5i6663pv5l/lib`

---

## Runtime environment for opflow

```bash
module load openmpi   # OpenMPI 5.0.5 from /sw/andes/spack-envs/sw-25.04
module load openblas  # libopenblas.so.0 needed transitively by libipopt.so.3
export IPOPT_LIB_DIR=~/spack-packages/linux-rhel8-zen2/gcc-9.3.0/ipopt-3.14.14-<hash>/lib
export LD_LIBRARY_PATH="${IPOPT_LIB_DIR}:${LD_LIBRARY_PATH:-}"
```

Verify with:
```bash
ldd external/ExaGO-andes-cpu-latest/builds/andes-cpu-latest/install/bin/opflow \
  | grep 'not found'
# Should produce no output (UCX transport libs are OK to be absent on login nodes)
```

---

## Campaign submission

See [configs/slurm/andes_exago_acopf_mapreduce_4n_36h.sbatch](../../configs/slurm/andes_exago_acopf_mapreduce_4n_36h.sbatch)
and [scripts/submit_exago_frontier_campaign.sh](../../scripts/submit_exago_frontier_campaign.sh).

The sbatch sets `IPOPT_LIB_DIR` automatically from the persistent spack path.
Override at submit time if the hash changes after a reinstall:

```bash
IPOPT_LIB_DIR=~/spack-packages/.../ipopt-3.14.14-<newhash>/lib \
CAMPAIGN_ID=exago_andes_activsg_g00 \
RUNS_ROOT=data/outputs/runs/exago_andes_activsg/g00 \
ACCOUNT=LRN070 \
ROUNDS=200 \
ROUND_SBATCH=configs/slurm/andes_exago_acopf_mapreduce_4n_36h.sbatch \
  scripts/submit_exago_frontier_campaign.sh
```
