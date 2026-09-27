# ACTIVSg2000 and case300 repair

The repair job retries unsuccessful `activsg2000` and
`pglib_opf_case300_ieee` samples from completed `ultrascale_3b` rounds `000`
through `005`. It does not read or modify active round `006`.

Retries are written under
`data/outputs/repairs/activsg2000_case300_r000_r005`. After all retry shards
finish, only retries with `success=true` replace their original
`samples.jsonl` rows. Original files are copied to the repair tree's `backups`
directory before replacement. Retries that remain infeasible, time out, or
error leave the original rows unchanged.

Run a short preflight first:

```bash
PREFLIGHT_ONLY=1 sbatch configs/slurm/andes_repair_activsg2000_case300.sbatch
```

Submit the complete repair from an Andes submit host:

```bash
sbatch configs/slurm/andes_repair_activsg2000_case300.sbatch
```

The job is resumable. Submit the same command again after a timeout; completed
repair shards are skipped. Set `APPLY_MERGE=0` to run all retries and produce a
dry-run merge manifest without changing the original samples.

The final audit record is:

```text
data/outputs/repairs/activsg2000_case300_r000_r005/merge_manifest.json
```