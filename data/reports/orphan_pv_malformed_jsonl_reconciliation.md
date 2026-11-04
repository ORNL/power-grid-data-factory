# Orphan-PV Malformed JSONL Reconciliation

Generated: 2026-11-02

## Scope and Method

This read-only reconciliation covers the 13 malformed PF `samples.jsonl` files previously identified in the four `riker_pf_severity25_lane*_v1` campaigns. Each shard's valid primary JSON rows and complete objects recoverable from malformed-line suffixes were compared as a multiset against its parseable `pf/outcomes/<outcome>/samples.jsonl` rows. Thirteen outcome rows exactly match complete top-level objects in malformed-line suffixes; two additional outcome rows match neither valid primary rows nor those suffix objects. SHA-256 is over UTF-8 canonical JSON (`sort_keys=True`, compact separators, Unicode preserved). Orphan-PV counts use `count_orphan_pv_buses` from the corpus audit.

The 13 primary parse errors remain present; every outcome file parsed without error. The 50 inspected source files (primary JSONL, outcome JSONLs, and manifests) had unchanged size, modification time, and inode across the check. No source output was modified.

## Recovered Rows

| Shard | Outcome file row | Candidate ID | Run ID | Success / termination | Orphan PV buses | Canonical SHA-256 |
|---|---|---|---|---|---:|---|
| `lane3/r000/s00486` | `converged_invalid` #1 | `pf::7e0941b8f47b29aa` | `pglib_opf_case10000_goc-topology_000004_single_line_switching::n1_generator-op_000000_baseline-ctg_k1_ggen001864_5023040f-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `302c42d2f21e9e012eebabe47a46a2498d0ac3f36d90faac106b25d6e562eaac` |
| `lane3/r000/s00200` | `nonconvergent` #1 | `pf::329d814dd6e7bfdf` | `pglib_opf_case10000_goc-topology_000001_single_line_switching::n2_structured-op_000000_baseline-ctg_k2_bbranch008652-bbranch008653_e899b83c-powermodels_pf_ipopt` | false / `LOCALLY_INFEASIBLE` | n/a | `5dc1badef35ac7a33d59f478e5f1c75860622ebbc33adba4c34a57d5b81192c5` |
| `lane2/r003/s03453` | `converged_invalid` #3 | `pf::4ce0df5f1a575647` | `epigrids_texas_7336-topology_000003_single_line_upgrade::n1_generator-op_000000_baseline-ctg_k1_ggen000364_6be622bf-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `59e1cd60fb65ec5b5f6b1eb450315c89c62b303d8860621c5f2707ebe52e1197` |
| `lane2/r003/s03453` | `converged_invalid` #4 | `pf::4ce0df5f1a575647` | `epigrids_texas_7336-topology_000003_single_line_upgrade::n1_generator-op_000000_baseline-ctg_k1_ggen000364_6be622bf-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `63c543a8dc5238250786f1f160167dd92fa5e722891f1020e1130e1023b872a8` |
| `lane2/r003/s00644` | `converged_invalid` #3 | `pf::efea61b72fc951ec` | `epigrids_florida_5658-topology_000001_single_line_switching::intact-op_000000_baseline-ctg_base-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `d99a023e50bb43f88e8996ff8b9446799f850c3536263d9d87b2574bef789b81` |
| `lane2/r003/s01104` | `nonconvergent` #3 | `pf::e9187daaa25c5a49` | `epigrids_florida_5658-topology_000002_double_line_switching::n2_structured-op_000000_baseline-ctg_k2_bbranch001037-bbranch001057_c9ac9f5d-powermodels_pf_ipopt` | false / `LOCALLY_INFEASIBLE` | n/a | `c45c18a4bbf42356a0a37d56e79ac82168fd277ca8b36eb3e6eb1ba12e95b30e` |
| `lane2/r000/s00754` | `converged_invalid` #1 | `pf::dc30fd0b0661b9b1` | `pglib_opf_case10000_goc-topology_000002_double_line_switching::intact-op_000000_baseline-ctg_base-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `a3d7c56a0ba52cd01df84705253c22c7b9b18d9e0b4f469c8a3ed9a5bf62c712` |
| `lane2/r000/s00056` | `converged_invalid` #3 | `pf::2e7140e8c54f9c0c` | `epigrids_texas_7336-topology_000001_single_line_switching::intact-op_000000_baseline-ctg_base-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `a6bed62df5b1e002cca9f86c21776aa4ac46bb02bcc0090a4dee37d31f79abbf` |
| `lane2/r000/s00755` | `converged_invalid` #3 | `pf::59707c60e901371c` | `pglib_opf_case10000_goc-topology_000002_double_line_switching::intact-op_000000_baseline-ctg_base-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `3e92400762c8b561425a6f97cfb4ca74ca4a9117b8ebc6739dd63720900c4094` |
| `lane0/r000/s00572` | `converged_invalid` #1 | `pf::8ff365c2fc841099` | `pglib_opf_case10000_goc-topology_000002_double_line_switching::intact-op_000000_baseline-ctg_base-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `27bde89ce03ab13c1c741e25e191702783ed822333d2b04eb79a70803741bd66` |
| `lane1/r000/s00422` | `converged_invalid` #1 | `pf::66b21a437508cfd2` | `epigrids_texas_7336-topology_000000_baseline::n1_branch-op_000000_baseline-ctg_k1_bbranch006390_9e4e8cf1-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `7139298137576796232f84bd457bac807cc0d82e35735e320fbb0ad1c4524127` |
| `lane1/r000/s00422` | `software_model_error` #4 | `pf::d0802b8aaf70c84d` | `epigrids_texas_7336-topology_000000_baseline::intact-op_000000_baseline-ctg_base-powermodels_pf_ipopt` | false / `process_error` | n/a | `99d9b938764a8c1dbe19a778fea72f4dbc6283f7226acb5b66d9313c9ad4c2dc` |
| `lane1/r003/s02254` | `converged_invalid` #3 | `pf::7e9909db8262a41a` | `epigrids_florida_5658-topology_000005_double_line_switching::n1_generator-op_000000_baseline-ctg_k1_ggen000381_3953a384-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `34cf8dfa1465303465a8575840bfc2e20e877390d55bd60a18bdd98e2eec8ffa` |
| `lane1/r003/s00412` | `nonconvergent` #4 | `pf::60f3c5838b891d19` | `pglib_opf_case9241_pegase-topology_000004_single_line_switching::n1_generator-op_000000_baseline-ctg_k1_ggen000362_9b7166ed-powermodels_pf_ipopt` | false / `LOCALLY_INFEASIBLE` | n/a | `139f5eab1d16502d179ab0b80727db8ba19e16423e76f1330eae6e9cfcd70b3f` |
| `lane1/r003/s02739` | `converged_invalid` #5 | `pf::92c2246d48980683` | `pglib_opf_case10000_goc-topology_000005_double_line_switching::n2_structured-op_000000_baseline-ctg_k2_bbranch002148-bbranch003837_7f65a1d8-powermodels_pf_ipopt` | true / `LOCALLY_SOLVED` | 0 | `59f8fb77c615882cbc96139a101d1b315ebab9af60a1f3a6891a450ab0876ec4` |

## Per-Shard Completeness

`sample_count` matches the number of nonempty physical lines in every primary file. Each file has one malformed line. A `JSONDecoder.raw_decode` scan recovered one complete object from the suffix of each malformed line; all 13 hashes match outcome rows. “Outcome-only” below means an outcome row with no exact hash match in either a valid primary row or a recovered suffix object. “Primary-only” means a valid primary row with no exact outcome match.

| Shard | Manifest / physical lines | Valid primary | Malformed lines | Suffix objects matched | Outcome rows | Outcome-only | Primary-only |
|---|---:|---:|---:|---:|---:|---:|---:|
| `lane3/r000/s00486` | 6 / 6 | 5 | 1 | 1 | 6 | 0 | 0 |
| `lane3/r000/s00200` | 7 / 7 | 6 | 1 | 1 | 7 | 0 | 0 |
| `lane2/r003/s03453` | 6 / 6 | 5 | 1 | 1 | 7 | 1 | 0 |
| `lane2/r003/s00644` | 7 / 7 | 6 | 1 | 1 | 7 | 0 | 0 |
| `lane2/r003/s01104` | 7 / 7 | 6 | 1 | 1 | 7 | 0 | 0 |
| `lane2/r000/s00754` | 7 / 7 | 6 | 1 | 1 | 7 | 0 | 0 |
| `lane2/r000/s00056` | 7 / 7 | 6 | 1 | 1 | 7 | 0 | 0 |
| `lane2/r000/s00755` | 7 / 7 | 6 | 1 | 1 | 7 | 0 | 0 |
| `lane0/r000/s00572` | 7 / 7 | 6 | 1 | 1 | 7 | 0 | 0 |
| `lane1/r000/s00422` | 6 / 6 | 5 | 1 | 1 | 6 | 1 | 1 |
| `lane1/r003/s02254` | 6 / 6 | 5 | 1 | 1 | 6 | 0 | 0 |
| `lane1/r003/s00412` | 6 / 6 | 5 | 1 | 1 | 6 | 0 | 0 |
| `lane1/r003/s02739` | 6 / 6 | 5 | 1 | 1 | 6 | 0 | 0 |
| **Total** | **85 / 85** | **72** | **13** | **13** | **86** | **2** | **1** |

The outcome-only records are `lane2/r003/s03453` `converged_invalid` #3 (hash prefix `59e1cd60fb65`) and `lane1/r000/s00422` `converged_invalid` #1 (hash prefix `713929813757`). The primary-only row in `lane1/r000/s00422` was already included among the 72 valid primary rows. Each of the two shards with an outcome-only row has seven distinct complete record hashes evidenced against six physical lines; therefore the line-based manifest count is not a record-object count for those shards.

## Totals

The fresh 2026-11-03 audit covered 49,745 canonical PF files across 15 campaigns. It counted 303,297 records: 179,476 successful/audited and 123,821 unsuccessful, with 13 file-level JSON parse exceptions. These totals reproduce the prior expanded-corpus baseline. Because the audit drops every row in a file when any line fails parsing, the correction includes 72 valid primary-file rows plus the 15 distinct rows represented by the derived overlay, for 87 additions total. The 72 primary-file rows comprise 44 successful and 28 unsuccessful records; the overlay contributes 11 successful and 4 unsuccessful records. All additions were hash-checked as distinct from one another.

| Measure | Before | Added | Reconciled lower bound |
|---|---:|---:|---:|
| Records | 303,297 | 87 | 303,384 |
| Successful/audited | 179,476 | 55 | 179,531 |
| Unsuccessful | 123,821 | 32 | 123,853 |
| Orphan-positive records | 43,365 | 2 | 43,367 |
| Orphan-PV demotions | 1,154,762 | 4 | 1,154,766 |

All 11 successful overlay rows have zero orphan-PV buses; the 44 successful primary-file rows contribute 2 orphan-positive records and 4 demotions. The 4 unsuccessful overlay rows and 28 unsuccessful primary-file rows are not counted as audited successes. In `lane2/r003/s03453`, two distinct JSON records have the same candidate and run IDs: one matches the malformed-line suffix and the other is outcome-only. In `lane1/r000/s00422`, one suffix object and one additional outcome-only row are recovered; its valid primary-only record (`pf::1d5d6e5a2526256a`) is among the 72 primary-file rows already included in the correction.

The separate historical 41,553-file audit remains unchanged at 253,297 records: 147,312 successful/audited and 105,985 unsuccessful. It is a different audit scope and must not be combined with the expanded-corpus totals above.

## Derived Recovery Overlay

An additive JSONL overlay is available at `data/derived/pf_malformed_jsonl_recovery/samples.jsonl`; its per-row provenance and canonical hashes are in `data/derived/pf_malformed_jsonl_recovery/recovery_manifest.json`. The overlay contains the 15 rows in the table above, copied without added fields from the cited outcome files. It is 139,464,182 bytes with SHA-256 `39ad0998d178706ea3517c0de96c12dba0521a227e2c30ba8439c121e751e6fd`. Rows are in reconciliation-report order, not asserted to match original execution order. This is a recovery overlay, not a complete replacement for any campaign shard; original `samples.jsonl` files remain untouched.

## Repeatable Audit Run

The corrected read-only audit can be rerun on Riker with `sbatch configs/slurm/riker_orphan_pv_audit.sbatch`. Slurm job `199446` completed with exit code 0 on 2026-11-03 using 32 workers. Its log is `data/scratch/logs/slurm-pgdf_orphan_pv_audit-199446.out`. The script parses valid rows from files containing malformed lines, recovers complete JSON objects from malformed-line suffixes, verifies the overlay against its manifest, and deduplicates by canonical record hash. This run covered 49,745 files across 15 campaigns, retained all 13 parse exceptions, deduplicated the 13 suffix-recovered overlay rows, and added the 2 outcome-only rows. The direct corrected totals match the reconciliation table above: 303,384 records, 179,531 successful/audited, 123,853 unsuccessful, 43,367 orphan-positive records, and 1,154,766 orphan-PV demotions.

## Interpretation

The local worker logs show resumed runs for all 13 affected shard IDs. Each malformed physical line has a damaged prefix followed by a complete top-level JSON object; the object is also present in its outcome copy. Two shards have an additional outcome record without an exact match in the valid primary rows or recovered suffix. This supports partial writes followed by resumed appends as the likely mechanism, but the available logs do not establish why the earlier writes stopped. No rerun or repair of the source JSONL files was performed.
