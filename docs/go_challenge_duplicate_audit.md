# GO Challenge MATPOWER Duplicate Audit

This page documents duplicate analysis for converted GO Challenge MATPOWER case files.

## Scope

- Dataset root: `external/go_challenge1/extracted/matpower`
- Audit artifact: `data/analysis/go_challenge1_matpower_duplicate_audit.json`
- Audit time (UTC): `2026-08-04` (see exact timestamp in the JSON artifact)

## Summary Results

- Total MATPOWER files audited: `7715`
- Exact duplicate groups (byte-for-byte identical files): `0`
- Exact duplicate files in groups: `0`
- Normalized duplicate groups: `690`
- Normalized duplicate files in groups: `3099`

Interpretation:

- There are no strict file-level duplicates.
- There are many normalized duplicates, meaning multiple files represent the same effective electrical case after removing non-semantic differences.

## Duplicate Definitions

### Exact duplicates

Exact duplicates are detected using SHA-256 over full file bytes.

### Normalized duplicates

Normalized duplicates are detected by:

1. Removing the MATLAB function declaration line (`function mpc = ...`)
2. Removing generated timestamp comment lines (`% generated_at: ...`)
3. Removing comments and blank lines
4. Normalizing whitespace
5. Computing SHA-256 over the normalized text

This captures semantically equivalent cases that differ only by function name, generation timestamp, or formatting.

## Why Normalized Duplicates Exist

GO Challenge archives contain many scenario packages and re-bundled event sets. Different archive paths can reference scenarios that normalize to the same MATPOWER model content. This is expected in aggregated challenge distributions.

## Reproducible Audit Method

Use the duplicate audit script/command that writes:

- `data/analysis/go_challenge1_matpower_duplicate_audit.json`

The JSON includes:

- Global counts
- Grouped duplicate clusters
- Full member paths for each group
- Method notes used to generate hashes

## Recommended Usage in Campaigns

- Keep the full converted corpus for provenance completeness.
- For training or benchmark fairness, build a deduplicated manifest using normalized hash groups and select one canonical representative per group.
- Preserve dropped-members mapping so dedup decisions are reproducible.
