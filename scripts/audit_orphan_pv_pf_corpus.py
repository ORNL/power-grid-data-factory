#!/usr/bin/env python3
"""Read-only audit of orphan PV buses in canonical saved PF samples."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from concurrent.futures import FIRST_COMPLETED, wait

JQ_FILTER = r'''
. as $record
| if $record.success != true then {success: false}
  else try (
     $record.inputs.resolved_case as $case
     | ($record.inputs.candidate.contingency // {}) as $contingency
     | (if $contingency.event_type == "sequential_n1n1" then [$contingency.first_outage, $contingency.second_outage]
         elif $contingency.event_type == "sequential_cascade" then ($contingency.stages // [])
         else ($contingency.components // []) end
         | map(select(.type == "generator") | (.id | tostring))) as $outages
     | ([$case.generators[]
         | (.gen_id | tostring) as $generator_id
         | select((.status // 1 | tonumber) != 0
              and (($outages | index($generator_id)) == null)
              and ([.pmin, .pmax, .qmin, .qmax] | all(. == 0) | not))
         | (.bus_id | tonumber)]) as $online_buses
     | {success: true,
         buses: ($case.buses | length),
         null_status: ([$case.generators[] | select(has("status") and .status == null)] | length),
         orphan_pv: ([$case.buses[] | select(.type == 2) | (.bus_id | tonumber) as $bus_id
                          | select(($online_buses | index($bus_id)) == null)] | length)}
  ) catch {success: true, error: "invalid_or_incomplete_input"}
end
'''


def canonical_sample_files(campaign_root: Path):
    for round_entry in os.scandir(campaign_root):
        if not round_entry.name.startswith("mapreduce_round_") or not round_entry.is_dir():
            continue
        for shard_entry in os.scandir(round_entry.path):
            if not shard_entry.name.startswith("shard_") or not shard_entry.is_dir():
                continue
            sample_path = Path(shard_entry.path) / "pf" / "samples.jsonl"
            if sample_path.is_file():
                yield sample_path


def generator_outages(contingency: Any) -> set[str]:
    if not isinstance(contingency, dict):
        return set()
    event_type = str(contingency.get("event_type", "simultaneous"))
    if event_type == "sequential_n1n1":
        components = (contingency.get("first_outage"), contingency.get("second_outage"))
    elif event_type == "sequential_cascade":
        components = contingency.get("stages", ())
    else:
        components = contingency.get("components", ())
    return {
        str(component.get("id", ""))
        for component in components or ()
        if isinstance(component, dict) and str(component.get("type", "")) == "generator"
    }


def count_orphan_pv_buses(record: dict[str, Any]) -> tuple[int, int]:
    inputs = record["inputs"]
    resolved_case = inputs["resolved_case"]
    candidate = inputs.get("candidate") or {}
    outages = generator_outages(candidate.get("contingency"))
    online_buses: set[int] = set()

    for generator in resolved_case["generators"]:
        status = generator.get("status", 1)
        if status is None:
            raise ValueError("null_generator_status")
        if int(status) == 0 or str(generator["gen_id"]) in outages:
            continue
        limits = tuple(float(generator[key]) for key in ("pmin", "pmax", "qmin", "qmax"))
        if all(value == 0.0 for value in limits):
            continue
        online_buses.add(int(generator["bus_id"]))

    orphan_count = sum(
        int(bus["type"]) == 2 and int(bus["bus_id"]) not in online_buses
        for bus in resolved_case["buses"]
    )
    return len(resolved_case["buses"]), orphan_count


def canonical_record_hash(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_complete_pf_record(record: dict[str, Any]) -> bool:
    return (
        record.get("task") == "pf"
        and isinstance(record.get("success"), bool)
        and isinstance(record.get("candidate_id"), str)
        and bool(record["candidate_id"].strip())
        and isinstance(record.get("run_id"), str)
        and bool(record["run_id"].strip())
    )


def recover_json_suffix(line: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for offset in range(len(line) - 1, -1, -1):
        if line[offset] != "{":
            continue
        try:
            record, end = decoder.raw_decode(line, offset)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and is_complete_pf_record(record) and not line[end:].strip():
            return record
    return None


def parse_sample_lines(lines: list[str]) -> tuple[list[dict[str, Any]], set[str], int]:
    records: list[dict[str, Any]] = []
    source_hashes: set[str] = set()
    malformed_lines = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed_lines += 1
            record = recover_json_suffix(line)
            if record is None:
                continue
        if isinstance(record, dict) and is_complete_pf_record(record):
            records.append(record)
            source_hashes.add(canonical_record_hash(record))
    return records, source_hashes, malformed_lines


def source_hashes_for_overlay_file(sample_path: Path, overlay_source_paths: set[Path]) -> set[str]:
    if sample_path.resolve() not in overlay_source_paths:
        return set()
    records, hashes, malformed_lines = parse_sample_lines(sample_path.read_text(encoding="utf-8").splitlines())
    if malformed_lines:
        raise ValueError(f"overlay source parsed by jq contains malformed lines: {sample_path}")
    return hashes


def summarize_jq_output(stdout: str) -> tuple[Counter[str], dict[int, Counter[str]], Counter[str]]:
    file_counts: Counter[str] = Counter()
    bus_totals: dict[int, Counter[str]] = defaultdict(Counter)
    file_exceptions: Counter[str] = Counter()
    try:
        for line in stdout.splitlines():
            if not line:
                continue
            row = json.loads(line)
            file_counts["records"] += 1
            if row.get("success") is not True:
                file_counts["unsuccessful"] += 1
                continue
            file_counts["successful"] += 1
            bus_count = row.get("buses")
            if row.get("null_status", 0):
                file_exceptions["null_generator_status"] += 1
                if isinstance(bus_count, int):
                    bus_totals[bus_count]["successful"] += 1
                continue
            if row.get("error") or not isinstance(bus_count, int) or not isinstance(row.get("orphan_pv"), int):
                file_exceptions["invalid_or_incomplete_input"] += 1
                continue
            file_counts["audited"] += 1
            stats = bus_totals[bus_count]
            stats["successful"] += 1
            stats["audited"] += 1
            stats["records_with_orphan_pv"] += row["orphan_pv"] > 0
            stats["orphan_pv_demotions"] += row["orphan_pv"]
    except json.JSONDecodeError:
        file_exceptions["jq_output_parse_error"] += 1
    return file_counts, bus_totals, file_exceptions


def audit_records(
    records: list[dict[str, Any]], jq: str
) -> tuple[Counter[str], dict[int, Counter[str]], Counter[str]]:
    if not records:
        return Counter(), {}, Counter()
    payload = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)
    try:
        result = subprocess.run(
            [jq, "-c", JQ_FILTER],
            input=payload,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return Counter(), {}, Counter({"sample_file_io_error": 1})
    if result.returncode != 0:
        return Counter(), {}, Counter({"jq_input_error": 1})
    return summarize_jq_output(result.stdout)


def load_recovery_overlay(
    overlay_path: Path, manifest_path: Path, runs_root: Path | None = None
) -> list[tuple[str, Path, str, dict[str, Any]]]:
    overlay_bytes = overlay_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_digest = hashlib.sha256(overlay_bytes).hexdigest()
    if actual_digest != manifest.get("records_sha256"):
        raise ValueError("recovery overlay digest does not match manifest")
    if manifest.get("canonical_record_hash") != "SHA-256 of UTF-8 JSON with sorted keys and compact separators":
        raise ValueError("unsupported recovery manifest hash format")

    lines = overlay_bytes.decode("utf-8").splitlines()
    metadata = manifest.get("records", [])
    if len(lines) != manifest.get("record_count") or len(lines) != len(metadata):
        raise ValueError("recovery overlay row count does not match manifest")

    entries: list[tuple[str, Path, str, dict[str, Any]]] = []
    for line_number, (line, row_metadata) in enumerate(zip(lines, metadata), start=1):
        record = json.loads(line)
        if not isinstance(record, dict) or canonical_record_hash(record) != row_metadata.get("sha256"):
            raise ValueError(f"recovery overlay hash mismatch at line {line_number}")
        source_parts = Path(row_metadata["source_shard"]).parts
        try:
            campaign_name = source_parts[source_parts.index("runs") + 1]
        except (ValueError, IndexError) as error:
            raise ValueError(f"invalid source_shard in recovery manifest at line {line_number}") from error
        source_path = Path(row_metadata["source_shard"]) / "samples.jsonl"
        if runs_root is not None:
            source_path = runs_root / Path(*source_parts[source_parts.index("runs") + 1 :]) / "samples.jsonl"
        entries.append((campaign_name, source_path, row_metadata["sha256"], record))
    return entries


def select_overlay_records(
    entries: list[tuple[str, Path, str, dict[str, Any]]],
    existing_hashes: set[str],
    selected_campaigns: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    overlay_by_campaign: dict[str, list[dict[str, Any]]] = defaultdict(list)
    overlay_seen: set[str] = set()
    deduplicated = 0
    for campaign_name, _, record_hash, record in entries:
        if campaign_name not in selected_campaigns:
            continue
        if record_hash in existing_hashes or record_hash in overlay_seen:
            deduplicated += 1
            continue
        overlay_by_campaign[campaign_name].append(record)
        overlay_seen.add(record_hash)
    return overlay_by_campaign, deduplicated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=Path("data/outputs/runs"))
    parser.add_argument("--campaign", action="append", help="Campaign name; repeat to select several.")
    parser.add_argument("--max-files", type=int, help="Stop after this many sample files (partial audit).")
    parser.add_argument("--recovery-overlay", type=Path, help="Verified additive JSONL recovery overlay.")
    parser.add_argument("--recovery-manifest", type=Path, help="Manifest for --recovery-overlay (defaults beside it).")
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()

    if args.max_files is not None and args.max_files < 1:
        parser.error("--max-files must be positive")
    if args.progress_every < 1:
        parser.error("--progress-every must be positive")
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.recovery_manifest is not None and args.recovery_overlay is None:
        parser.error("--recovery-manifest requires --recovery-overlay")
    if args.recovery_overlay is not None and args.max_files is not None:
        parser.error("--recovery-overlay cannot be used with a partial --max-files audit")
    jq = shutil.which("jq")
    if jq is None:
        parser.error("jq is required for fast corpus parsing")

    selected = set(args.campaign or ())
    campaigns = sorted(
        entry for entry in args.runs_root.iterdir()
        if entry.is_dir() and entry.name.startswith("riker_pf") and (not selected or entry.name in selected)
    )
    missing = selected - {campaign.name for campaign in campaigns}
    if missing:
        parser.error("campaign not found: " + ", ".join(sorted(missing)))

    overlay_entries: list[tuple[str, Path, str, dict[str, Any]]] = []
    overlay_source_paths: set[Path] = set()
    if args.recovery_overlay is not None:
        manifest_path = args.recovery_manifest or args.recovery_overlay.parent / "recovery_manifest.json"
        try:
            overlay_entries = load_recovery_overlay(args.recovery_overlay, manifest_path, args.runs_root)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, ValueError) as error:
            parser.error(f"invalid recovery overlay: {error}")
        overlay_source_paths = {source_path.resolve() for _, source_path, _, _ in overlay_entries}

    totals: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    exceptions: Counter[tuple[str, str]] = Counter()
    files_seen = records_seen = successful = unsuccessful = audited = exception_count = 0

    def audit_file(
        item: tuple[str, Path],
    ) -> tuple[str, Counter[str], dict[int, Counter[str]], Counter[str], set[str]]:
        campaign_name, sample_path = item
        try:
            result = subprocess.run(
                [jq, "-c", JQ_FILTER, str(sample_path)],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return campaign_name, Counter(), {}, Counter({"sample_file_io_error": 1}), set()
        if result.returncode == 0:
            counts, buses, file_exceptions = summarize_jq_output(result.stdout)
            try:
                source_hashes = source_hashes_for_overlay_file(sample_path, overlay_source_paths)
            except (OSError, UnicodeDecodeError, ValueError) as error:
                raise RuntimeError(f"failed to index overlay source hashes: {error}") from error
            return campaign_name, counts, buses, file_exceptions, source_hashes

        try:
            lines = sample_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return campaign_name, Counter(), {}, Counter({"sample_file_io_error": 1}), set()
        records, source_hashes, malformed_lines = parse_sample_lines(lines)
        counts, buses, file_exceptions = audit_records(records, jq)
        if malformed_lines:
            file_exceptions["json_parse_error"] += malformed_lines
        return campaign_name, counts, buses, file_exceptions, source_hashes

    recovered_source_hashes: set[str] = set()

    def accumulate(
        campaign_name: str,
        counts: Counter[str],
        bus_totals: dict[int, Counter[str]],
        file_exceptions: Counter[str],
        source_hashes: set[str] | None = None,
    ) -> None:
        nonlocal records_seen, successful, unsuccessful, audited, exception_count
        records_seen += counts["records"]
        successful += counts["successful"]
        unsuccessful += counts["unsuccessful"]
        audited += counts["audited"]
        for bus_count, stats in bus_totals.items():
            totals[(campaign_name, bus_count)].update(stats)
        for reason, count in file_exceptions.items():
            exceptions[(campaign_name, reason)] += count
            exception_count += count
        if source_hashes:
            recovered_source_hashes.update(source_hashes)

    print(
        f"audit_start campaigns={len(campaigns)} "
        f"workers={args.workers} parser=jq",
        flush=True,
    )
    started = time.monotonic()
    campaign_file_counts: Counter[str] = Counter()
    max_in_flight = max(1, args.workers * 2)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        for campaign in campaigns:
            pending = set()
            campaign_files = 0
            print(f"campaign_start name={campaign.name}", flush=True)
            for sample_path in canonical_sample_files(campaign):
                if args.max_files is not None and files_seen + len(pending) >= args.max_files:
                    break
                pending.add(executor.submit(audit_file, (campaign.name, sample_path)))
                campaign_files += 1
                if len(pending) >= max_in_flight:
                    completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in completed:
                        campaign_name, counts, bus_totals, file_exceptions, source_hashes = future.result()
                        campaign_file_counts[campaign_name] += 1
                        files_seen += 1
                        accumulate(campaign_name, counts, bus_totals, file_exceptions, source_hashes)

                        if files_seen % args.progress_every == 0:
                            elapsed = max(time.monotonic() - started, 0.001)
                            print(
                                f"progress campaign={campaign_name} files={files_seen} "
                                f"campaign_files={campaign_file_counts[campaign_name]} records={records_seen} "
                                f"successful={successful} audited={audited} exceptions={exception_count} "
                                f"files_per_second={files_seen / elapsed:.2f}",
                                flush=True,
                            )

            while pending:
                completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    campaign_name, counts, bus_totals, file_exceptions, source_hashes = future.result()
                    campaign_file_counts[campaign_name] += 1
                    files_seen += 1
                    accumulate(campaign_name, counts, bus_totals, file_exceptions, source_hashes)

                    if files_seen % args.progress_every == 0:
                        elapsed = max(time.monotonic() - started, 0.001)
                        print(
                            f"progress campaign={campaign_name} files={files_seen} "
                            f"campaign_files={campaign_file_counts[campaign_name]} records={records_seen} "
                            f"successful={successful} audited={audited} exceptions={exception_count} "
                            f"files_per_second={files_seen / elapsed:.2f}",
                            flush=True,
                        )
            print(f"campaign_complete name={campaign.name} files={campaign_files}", flush=True)
            if args.max_files is not None and files_seen >= args.max_files:
                break

    overlay_added = overlay_deduplicated = 0
    if args.recovery_overlay is not None:
        overlay_by_campaign, overlay_deduplicated = select_overlay_records(
            overlay_entries,
            recovered_source_hashes,
            {campaign.name for campaign in campaigns},
        )
        for campaign_name, records in overlay_by_campaign.items():
            counts, bus_totals, file_exceptions = audit_records(records, jq)
            accumulate(campaign_name, counts, bus_totals, file_exceptions)
            overlay_added += counts["records"]

    complete = args.max_files is None and not selected
    print(
        f"scope={'complete' if complete else 'partial'} campaigns={len(campaigns)} canonical_pf_files={files_seen} "
        f"nonempty_records={records_seen} successful_records={successful} unsuccessful_records={unsuccessful} "
        f"audited_successful_records={audited} exceptions={exception_count}"
    )
    if args.recovery_overlay is not None:
        print(
            f"recovery_overlay_records_added={overlay_added} "
            f"recovery_overlay_records_deduplicated={overlay_deduplicated}"
        )
    print("campaign_scope=" + ",".join(campaign.name for campaign in campaigns))
    print("campaign,buses,successful,audited,records_with_orphan_pv,orphan_pv_demotions")
    for (campaign, bus_count), stats in sorted(totals.items()):
        print(
            f"{campaign},{bus_count},{stats['successful']},{stats['audited']},"
            f"{stats['records_with_orphan_pv']},"
            f"{stats['orphan_pv_demotions']}"
        )
    print("exceptions_by_campaign:")
    for (campaign, reason), count in sorted(exceptions.items()):
        print(f"campaign={campaign} count={count} reason={reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())