#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from grid_data_factory.campaigns import AdaptiveCampaign


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run one adaptive campaign selection round.")
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--config", default="configs/campaign_default.yaml")
    p.add_argument("--candidates-jsonl", required=True)
    p.add_argument("--round-index", type=int, default=0)
    p.add_argument("--budget", type=int, required=True)
    p.add_argument("--seed", type=int, default=0)
    return p.parse_args()


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    campaign = AdaptiveCampaign(
        repo_root=repo_root,
        config_path=(repo_root / args.config).resolve(),
        campaign_id=args.campaign_id,
    )
    campaign.initialize()

    candidates = _read_jsonl((repo_root / args.candidates_jsonl).resolve())
    summary = campaign.run_round(
        round_index=args.round_index,
        candidates=candidates,
        budget=args.budget,
        seed=args.seed,
    )

    print(json.dumps({"ok": True, "campaign_id": args.campaign_id, "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
