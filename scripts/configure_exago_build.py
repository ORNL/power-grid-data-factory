#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _default_profile() -> str:
    raw = os.environ.get("PGDF_EXAGO_BUILD_PROFILE") or os.environ.get("HOSTNAME") or "local"
    profile = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw.strip().lower()).strip("-.")
    return profile or "local"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Configure machine-scoped ExaGO build/install directories.",
    )
    p.add_argument("--exago-root", default="external/ExaGO", help="Path to ExaGO checkout (relative to repo root or absolute).")
    p.add_argument("--profile", default=_default_profile(), help="Machine/profile name used under external/ExaGO/builds/<profile>.")
    p.add_argument("--cache", default="", help="Optional CMake cache file path. Relative paths are resolved from --exago-root.")
    p.add_argument("--build-type", default="Release", help="CMAKE_BUILD_TYPE value.")
    p.add_argument("--generator", default="", help="Optional CMake generator, e.g. Ninja.")
    p.add_argument("--define", action="append", default=[], help="Additional -D entries for CMake, e.g. CMAKE_C_COMPILER=cc.")
    p.add_argument("--build", action="store_true", help="Run cmake --build . after configure.")
    p.add_argument("--install", action="store_true", help="Run cmake --install . after configure/build.")
    p.add_argument("--parallel", type=int, default=0, help="Parallel jobs for cmake --build.")
    p.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    exago_root = Path(args.exago_root)
    exago_root = exago_root if exago_root.is_absolute() else (repo_root / exago_root).resolve()
    if not exago_root.exists():
        print(json.dumps({"ok": False, "message": "ExaGO root does not exist", "exago_root": str(exago_root)}, indent=2))
        raise SystemExit(2)

    profile = args.profile.strip()
    if not profile:
        print(json.dumps({"ok": False, "message": "Profile must be non-empty"}, indent=2))
        raise SystemExit(2)

    build_dir = exago_root / "builds" / profile / "build"
    install_dir = exago_root / "builds" / profile / "install"
    build_dir.mkdir(parents=True, exist_ok=True)
    install_dir.mkdir(parents=True, exist_ok=True)

    cache_path = None
    if args.cache:
        cache_candidate = Path(args.cache)
        cache_path = cache_candidate if cache_candidate.is_absolute() else (exago_root / cache_candidate).resolve()
        if not cache_path.exists():
            print(
                json.dumps(
                    {
                        "ok": False,
                        "message": "CMake cache file not found",
                        "cache": str(cache_path),
                    },
                    indent=2,
                )
            )
            raise SystemExit(2)

    configure_cmd = ["cmake"]
    if args.generator:
        configure_cmd.extend(["-G", args.generator])
    if cache_path is not None:
        configure_cmd.extend(["-C", str(cache_path)])
    configure_cmd.extend(
        [
            str(exago_root),
            f"-DCMAKE_BUILD_TYPE={args.build_type}",
            f"-DCMAKE_INSTALL_PREFIX={install_dir}",
        ]
    )
    for entry in args.define:
        configure_cmd.append(f"-D{entry}")

    build_cmd = ["cmake", "--build", "."]
    if args.parallel and args.parallel > 0:
        build_cmd.extend(["--parallel", str(args.parallel)])

    install_cmd = ["cmake", "--install", "."]

    summary = {
        "ok": True,
        "profile": profile,
        "exago_root": str(exago_root),
        "build_dir": str(build_dir),
        "install_dir": str(install_dir),
        "commands": {
            "configure": configure_cmd,
            "build": build_cmd if args.build else None,
            "install": install_cmd if args.install else None,
        },
    }

    if args.dry_run:
        summary["dry_run"] = True
        print(json.dumps(summary, indent=2))
        return

    cfg = _run(configure_cmd, build_dir)
    if cfg.returncode != 0:
        print(
            json.dumps(
                {
                    **summary,
                    "ok": False,
                    "stage": "configure",
                    "returncode": cfg.returncode,
                    "stdout": cfg.stdout,
                    "stderr": cfg.stderr,
                },
                indent=2,
            )
        )
        raise SystemExit(2)

    if args.build:
        bld = _run(build_cmd, build_dir)
        if bld.returncode != 0:
            print(
                json.dumps(
                    {
                        **summary,
                        "ok": False,
                        "stage": "build",
                        "returncode": bld.returncode,
                        "stdout": bld.stdout,
                        "stderr": bld.stderr,
                    },
                    indent=2,
                )
            )
            raise SystemExit(2)

    if args.install:
        inst = _run(install_cmd, build_dir)
        if inst.returncode != 0:
            print(
                json.dumps(
                    {
                        **summary,
                        "ok": False,
                        "stage": "install",
                        "returncode": inst.returncode,
                        "stdout": inst.stdout,
                        "stderr": inst.stderr,
                    },
                    indent=2,
                )
            )
            raise SystemExit(2)

    summary["opflow_bin"] = str(install_dir / "bin" / "opflow")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()