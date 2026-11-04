#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare an isolated replay queue for PF shards with software errors.")
    parser.add_argument("--source-campaign-id", required=True)
    parser.add_argument("--staged-campaign-id", required=True)
    parser.add_argument("--round-index", type=int, default=3)
    parser.add_argument("--shard-index", action="append", default=[], help="Explicit shard index to replay, including shards with incomplete reports.")
    parser.add_argument("--base-merged-ids-file", type=Path, help="Existing merged shard ID list to extend with these replacements.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_root = args.repo_root / "data" / "outputs" / "campaigns" / args.source_campaign_id
    source_round = source_root / "round_summaries" / f"round_{args.round_index:03d}_pf"
    shard_ids_path = source_round / "shards" / "shard_campaign_ids.txt"
    source_shard_ids = [line.strip() for line in shard_ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    requested_indices = set(args.shard_index)
    source_index_by_shard = {
        shard_id.rsplit("__s", 1)[1]: position for position, shard_id in enumerate(source_shard_ids)
    }
    unknown_indices = requested_indices - source_index_by_shard.keys()
    if unknown_indices:
        raise ValueError(f"Requested shard indices are not in the source shard list: {sorted(unknown_indices)}")
    affected = []
    for shard_id in source_shard_ids:
        report_path = (
            args.repo_root
            / "data"
            / "outputs"
            / "campaigns"
            / shard_id
            / "round_summaries"
            / f"round_{args.round_index:03d}_pf_execution_report.json"
        )
        if not report_path.is_file():
            raise FileNotFoundError(f"Missing source shard report: {report_path}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        software_errors = int((report.get("outcome_counts") or {}).get("software_model_error", 0))
        shard_index = shard_id.rsplit("__s", 1)[1]
        should_replay = shard_index in args.shard_index if args.shard_index else bool(software_errors)
        if should_replay:
            candidate_path = source_round / "shards" / f"selected_{shard_index}.jsonl"
            if not candidate_path.is_file() or candidate_path.stat().st_size == 0:
                raise FileNotFoundError(f"Missing selected candidates for affected shard: {candidate_path}")
            affected.append((shard_index, shard_id, f"{args.staged_campaign_id}__r{args.round_index:03d}__s{shard_index}"))

    staged_root = args.repo_root / "data" / "outputs" / "campaigns" / args.staged_campaign_id
    staged_round = staged_root / "round_summaries" / f"round_{args.round_index:03d}_pf"
    queue_dir = staged_round / "shards" / "queue"
    if staged_round.exists() and any(staged_round.iterdir()):
        raise FileExistsError(f"Staged replay directory is not empty: {staged_round}")
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "done").mkdir(exist_ok=True)
    shard_indices = [item[0] for item in affected]
    (queue_dir / "shards.txt").write_text("".join(f"{index}\n" for index in shard_indices), encoding="utf-8")
    (queue_dir / "ready").touch()
    (staged_round / "shards" / "shard_campaign_ids.txt").write_text(
        "".join(f"{staged_id}\n" for _, _, staged_id in affected), encoding="utf-8"
    )

    replacements = {source_id: staged_id for _, source_id, staged_id in affected}
    if args.base_merged_ids_file:
        base_path = args.base_merged_ids_file
        base_path = base_path if base_path.is_absolute() else args.repo_root / base_path
        merged_ids = [line.strip() for line in base_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(merged_ids) != len(source_shard_ids):
            raise ValueError("Base merged shard ID list has a different length from the source shard list")
        for index, source_id, staged_id in affected:
            source_position = source_index_by_shard[index]
            if merged_ids[source_position] not in {source_id, staged_id}:
                raise ValueError(f"Base merged list has unexpected campaign ID at shard index {index}")
            merged_ids[source_position] = staged_id
    else:
        merged_ids = [replacements.get(shard_id, shard_id) for shard_id in source_shard_ids]
    merged_path = staged_round / "merged_shard_campaign_ids.txt"
    merged_path.write_text("".join(f"{shard_id}\n" for shard_id in merged_ids), encoding="utf-8")
    manifest = {
        "source_campaign_id": args.source_campaign_id,
        "staged_campaign_id": args.staged_campaign_id,
        "round_index": args.round_index,
        "source_shard_count": len(source_shard_ids),
        "replay_shard_count": len(affected),
        "replay_candidate_count": sum(
            int(json.loads((args.repo_root / "data/outputs/campaigns" / source_id / "round_summaries" / f"round_{args.round_index:03d}_pf_execution_report.json").read_text(encoding="utf-8")).get("candidate_count", 0))
            for _, source_id, _ in affected
        ),
        "merged_shard_campaign_ids_file": str(merged_path.relative_to(args.repo_root)),
        "replay_shards": [
            {"shard_index": index, "source_campaign_id": source_id, "staged_campaign_id": staged_id}
            for index, source_id, staged_id in affected
        ],
    }
    (staged_round / "replay_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "replay_shards"}, indent=2))


if __name__ == "__main__":
    main()