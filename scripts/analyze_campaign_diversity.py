#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from grid_data_factory.diversity.audit import AuditConfig, audit_ledgers, discover_shard_ledgers
    from grid_data_factory.storage import paths
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.diversity.audit import AuditConfig, audit_ledgers, discover_shard_ledgers
    from grid_data_factory.storage import paths


def _rounds(value: str) -> list[int]:
    result: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, stop = (int(item) for item in part.split("-", 1))
            result.update(range(start, stop + 1))
        else:
            result.add(int(part))
    if not result:
        raise argparse.ArgumentTypeError("at least one round is required")
    return sorted(result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit solved-state diversity across sharded campaign rounds.")
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--rounds", type=_rounds, required=True, help="Comma/range syntax, for example 0-5 or 0,2,4.")
    parser.add_argument("--config", default="configs/diversity_analysis.yaml")
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--near-duplicate-threshold", type=float, default=None)
    parser.add_argument("--block-size", type=int, default=None)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--allow-missing-ledgers", action="store_true")
    return parser.parse_args()


def _load_config(path: Path) -> dict[str, Any]:
    import yaml

    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _git_head(repo_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, capture_output=True, check=False
    )
    return result.stdout.strip() or None


def analysis_config(args: argparse.Namespace, repo_root: Path) -> AuditConfig:
    file_config = _load_config((repo_root / args.config).resolve()).get("diversity_analysis", {})
    return AuditConfig(
        sample_size=args.sample_size or int(file_config.get("sample_size", 10_000)),
        seed=args.seed if args.seed is not None else int(file_config.get("seed", 20260924)),
        near_duplicate_threshold=(
            args.near_duplicate_threshold
            if args.near_duplicate_threshold is not None
            else float(file_config.get("near_duplicate_threshold", 0.02))
        ),
        block_size=args.block_size or int(file_config.get("block_size", 256)),
    )


def _ecdf_polyline(values: list[float], width: int = 720, height: int = 240) -> str:
    if not values:
        return ""
    ordered = sorted(values)
    maximum = max(ordered) or 1.0
    points = []
    for index, value in enumerate(ordered):
        x = 20 + (width - 40) * value / maximum
        y = height - 20 - (height - 40) * index / max(len(ordered) - 1, 1)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _lorenz_polyline(cluster_sizes: list[int], width: int = 720, height: int = 240) -> str:
    if not cluster_sizes:
        return ""
    ordered = sorted(cluster_sizes)
    total = sum(ordered)
    cumulative = 0
    points = [f"20,{height - 20}"]
    for index, size in enumerate(ordered, start=1):
        cumulative += size
        x = 20 + (width - 40) * index / len(ordered)
        y = height - 20 - (height - 40) * cumulative / total
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _topology_contingency_heatmap(metrics: dict[str, Any]) -> str:
    orders = metrics["contingency_orders"]
    topologies = metrics["topology_classes"]
    cells = {(cell["topology_class"], cell["contingency_order"]): cell for cell in metrics["cells"]}
    maximum = max((cell["count"] for cell in metrics["cells"]), default=0)
    header = "<tr><th>Topology class</th>" + "".join(f"<th>N-{html.escape(order)}</th>" for order in orders) + "</tr>"
    rows = []
    for topology in topologies:
        row_cells = [f"<th>{html.escape(topology)}</th>"]
        for order in orders:
            cell = cells[(topology, order)]
            intensity = math.log1p(cell["count"]) / math.log1p(maximum) if maximum else 0.0
            lightness = 97.0 - 55.0 * intensity
            text_color = "#ffffff" if lightness < 58 else "#17211b"
            row_cells.append(
                f'<td style="background:hsl(158 58% {lightness:.1f}%);color:{text_color}" '
                f'title="{cell["count"]:,} samples ({cell["corpus_fraction"]:.4%})">'
                f'<b>{cell["count"]:,}</b><br><small>{cell["corpus_fraction"]:.2%}</small></td>'
            )
        rows.append("<tr>" + "".join(row_cells) + "</tr>")
    return '<table class="heatmap">' + header + "".join(rows) + "</table>"


def _write_html(path: Path, report: dict[str, Any]) -> None:
    sample = report["sample_metrics"]
    distances = [value for value in sample.pop("nearest_neighbor_distances", []) if value is not None]
    cluster_sizes = sample.pop("cluster_sizes", [])
    categories = report["categorical_metrics"]
    topology_contingency = report["topology_contingency_metrics"]
    rows = "".join(
        f"<tr><td>{html.escape(field)}</td><td>{html.escape(metric['basis'])}</td><td>{metric['category_count']:,}</td>"
        f"<td>{metric['normalized_entropy']:.4f}</td><td>{metric['hill_number_2']:.1f}</td></tr>"
        for field, metric in categories.items()
    )
    q = sample["nearest_neighbor_distance"]
    body = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Campaign diversity audit</title>
<style>
body{{font:15px Georgia,serif;max-width:980px;margin:40px auto;padding:0 24px;color:#17211b;background:#f4f6f1}}
h1,h2{{font-family:Arial,sans-serif;letter-spacing:0}} .metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.metric{{background:white;border:1px solid #c9d1c8;padding:14px;border-radius:6px}} .value{{font:700 25px Arial,sans-serif}}
table{{width:100%;border-collapse:collapse;background:white}} td,th{{padding:8px;border-bottom:1px solid #d8ddd7;text-align:left}}
.heatmap td,.heatmap th{{text-align:center;border:2px solid #f4f6f1}} .heatmap th:first-child{{text-align:left}}
svg{{background:white;border:1px solid #c9d1c8;width:100%;height:auto}} code{{font-size:12px}}
</style><body>
<h1>Campaign diversity audit</h1>
<p><b>{html.escape(report['campaign_id'])}</b>, rounds {html.escape(str(report['rounds']))}; {report['row_count']:,} solved-state descriptors.</p>
<div class="metrics">
<div class="metric">Near-duplicate rate<div class="value">{sample['near_duplicate_rate']:.2%}</div></div>
<div class="metric">Effective sample ratio<div class="value">{sample['effective_sample_ratio']:.2%}</div></div>
<div class="metric">Intrinsic dimension<div class="value">{sample['intrinsic_dimension_participation_ratio']:.2f}</div></div>
<div class="metric">Exact descriptor duplicates<div class="value">{sample['exact_descriptor_duplicate_rate']:.2%}</div></div>
<div class="metric">Median NN distance<div class="value">{q['p50'] if q['p50'] is not None else 'n/a'}</div></div>
<div class="metric">Largest sampled cluster<div class="value">{sample['largest_similarity_cluster']:,}</div></div>
</div>
<h2>Nearest-neighbor distance ECDF</h2>
<svg viewBox="0 0 720 240" role="img"><polyline fill="none" stroke="#087f5b" stroke-width="2" points="{_ecdf_polyline(distances)}"/></svg>
<h2>Similarity-cluster Lorenz curve</h2>
<svg viewBox="0 0 720 240" role="img"><line x1="20" y1="220" x2="700" y2="20" stroke="#adb5bd"/><polyline fill="none" stroke="#b54708" stroke-width="2" points="{_lorenz_polyline(cluster_sizes)}"/></svg>
<h2>Topology-contingency coverage</h2>
<p>{topology_contingency['occupied_cell_count']} of {topology_contingency['possible_cell_count']} combinations are occupied; joint normalized entropy is {topology_contingency['normalized_entropy']:.4f}.</p>
{_topology_contingency_heatmap(topology_contingency)}
<h2>Coverage</h2><table><tr><th>Field</th><th>Basis</th><th>Categories</th><th>Normalized entropy</th><th>Effective categories</th></tr>{rows}</table>
<h2>Method</h2><p>{html.escape(report['method'])}. Distances use robust IQR scaling and only compare matching case, topology class, and contingency order.</p>
<p>Sample cluster sizes: <code>{html.escape(json.dumps(cluster_sizes[:50]))}</code></p>
</body></html>"""
    path.write_text(body, encoding="utf-8")
    sample["nearest_neighbor_distances"] = distances
    sample["cluster_sizes"] = cluster_sizes


def write_outputs(output_dir: Path, report: dict[str, Any], sample_rows: list[dict[str, Any]]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "diversity_report.json"
    sample_path = output_dir / "diversity_sample.csv"
    html_path = output_dir / "diversity_report.html"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    with sample_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = sorted({key for row in sample_rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in sample_rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    _write_html(html_path, report)
    return {"report": str(report_path), "html": str(html_path), "sample": str(sample_path)}


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    config = analysis_config(args, repo_root)
    ledgers, missing = discover_shard_ledgers(paths.campaigns_root(repo_root), args.campaign_id, args.rounds)
    if missing and not args.allow_missing_ledgers:
        raise SystemExit(f"Missing {len(missing)} required ledgers; first missing path: {missing[0]}")
    if not ledgers:
        raise SystemExit("No diversity ledgers found")

    report, sample_rows = audit_ledgers(ledgers, config)
    report.update(
        {
            "campaign_id": args.campaign_id,
            "rounds": args.rounds,
            "git_commit": _git_head(repo_root),
            "missing_ledger_count": len(missing),
            "missing_ledgers": missing,
        }
    )
    round_slug = f"rounds_{args.rounds[0]:03d}-{args.rounds[-1]:03d}"
    output_dir = Path(args.output_dir).resolve() if args.output_dir else paths.reports_dir(repo_root) / "diversity" / args.campaign_id / round_slug
    outputs = write_outputs(output_dir, report, sample_rows)
    print(json.dumps({"ok": True, **outputs}, indent=2))


if __name__ == "__main__":
    main()
