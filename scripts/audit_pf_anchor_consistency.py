"""Audit PF exact-consistency anchor cases: why do some not reproduce their source AC-OPF state?

For each exact_consistency record, evaluates the source AC-OPF voltages on the PF network
(bus power mismatch), and compares PF set-points and topology against the source run.
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import numpy as np


def _source_state_mismatch(case: dict, src: dict) -> dict:
    base = float(case["base_mva"])
    buses = case["buses"]
    idx = {b["bus_id"]: i for i, b in enumerate(buses)}
    src_bus = src["bus"]
    va = {k: float(v["va"]) for k, v in src_bus.items()}
    scale = np.pi / 180.0 if max(abs(x) for x in va.values()) > 2 * np.pi else 1.0
    V = np.array([float(src_bus[b["bus_id"]]["vm"]) * np.exp(1j * va[b["bus_id"]] * scale) for b in buses])
    current = np.zeros(len(buses), complex)
    for br in case["branches"]:
        f, t = idx[br["from"]], idx[br["to"]]
        y = 1.0 / complex(br["r"], br["x"])
        bc = 0.5j * float(br["b"])
        current[f] += (y + bc) * V[f] - y * V[t]
        current[t] += (y + bc) * V[t] - y * V[f]
    for b in buses:
        current[idx[b["bus_id"]]] += complex(b["gs"], b["bs"]) / base * V[idx[b["bus_id"]]]
    injection = V * np.conj(current) * base
    for load in case["loads"]:
        injection[idx[load["bus_id"]]] += complex(load["pd"], load["qd"])
    gen_bus = {g["bus_id"] for g in case["generators"]}
    for k, g in enumerate(case["generators"], start=1):
        sg = src["gen"].get(str(k))
        if sg is not None:
            injection[idx[g["bus_id"]]] -= complex(sg["pg"], sg["qg"]) * float(src.get("baseMVA", base))
    load_buses = [i for b, i in idx.items() if b not in gen_bus]
    mis = np.abs(injection[load_buses]) if load_buses else np.zeros(1)
    return {
        "src_state_max_mismatch_load_buses_mva": float(mis.max()),
        "src_state_buses_over_1mva": int((mis > 1.0).sum()),
    }


def _audit_shard(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if '"exact_consistency"' not in line:
                continue
            r = json.loads(line)
            status = r.get("anchor_consistency_status")
            if status not in ("consistent", "inconsistent"):
                continue
            case = r["inputs"]["resolved_case"]
            cand = r["inputs"]["candidate"]
            src = cand.get("source_ac_opf_solution") or {}
            gens = {g["gen_id"]: g for g in case["generators"]}
            src_vm = {k: float(v["vm"]) for k, v in src["bus"].items()}
            vg_err = max(
                (abs(float(c["vg"]) - src_vm[gens[gid]["bus_id"]]) for gid, c in cand["controls"]["generators"].items() if gid in gens),
                default=0.0,
            )
            run_id = cand.get("source_ac_opf_run_id", "")
            ctg_token = run_id.split("-ctg_")[1].split("_")[0] if "-ctg_" in run_id else ""
            anchor = r["result"]["validation"].get("anchor_consistency") or {}
            row = {
                "shard": Path(path).parts[-3],
                "status": status,
                "outcome": r.get("outcome_class"),
                "case_id": r["case_id"],
                "source_ctg": ctg_token,
                "source_has_contingency": bool(ctg_token) and ctg_token not in ("seq0", "none", "base"),
                "pf_contingency": cand.get("contingency"),
                "src_branches": len(src.get("branch") or {}),
                "pf_branches": len(case["branches"]),
                "max_abs_vg_minus_src_vm": vg_err,
                "anchor_max_dv_pu": anchor.get("max_voltage_error_pu"),
                "anchor_max_dva_rad": anchor.get("max_angle_error_rad"),
                "pv_to_pq_count": len(r["result"].get("pv_to_pq_bus_ids") or []),
            }
            try:
                row.update(_source_state_mismatch(case, src))
            except (KeyError, ZeroDivisionError) as exc:
                row["mismatch_error"] = repr(exc)
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()
    paths = sorted(glob.glob(f"{args.round_dir}/shard_*/pf/samples.jsonl"))
    with Pool(args.workers) as pool:
        rows = [row for chunk in pool.imap_unordered(_audit_shard, paths, chunksize=8) for row in chunk]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    def summarize(group: list[dict]) -> dict:
        def med(key):
            vals = [x[key] for x in group if x.get(key) is not None]
            return float(np.median(vals)) if vals else None
        return {
            "n": len(group),
            "source_has_contingency": sum(x["source_has_contingency"] for x in group),
            "branch_count_differs": sum(x["src_branches"] != x["pf_branches"] for x in group),
            "vg_differs_from_src_vm_gt_1e-3": sum(x["max_abs_vg_minus_src_vm"] > 1e-3 for x in group),
            "src_state_satisfies_pf_network(<1MVA)": sum(x.get("src_state_max_mismatch_load_buses_mva", 1e9) < 1.0 for x in group),
            "median_src_state_mismatch_mva": med("src_state_max_mismatch_load_buses_mva"),
            "median_anchor_dv_pu": med("anchor_max_dv_pu"),
            "outcomes": dict(Counter(x["outcome"] for x in group)),
        }

    summary = {s: summarize([x for x in rows if x["status"] == s]) for s in ("consistent", "inconsistent")}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
