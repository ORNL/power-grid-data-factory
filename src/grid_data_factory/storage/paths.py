"""Single source of truth for the on-disk ``data/`` layout.

Lifecycle tiers make it unambiguous what may be deleted:

- ``data/inputs/``   source-of-truth provenance and manual source bundles (never auto-deleted).
- ``data/derived/``  reproducible caches (canonical cases, registries) — safe to rebuild.
- ``data/outputs/``  disposable run products (solver runs, campaign rounds).
- ``data/scratch/``  ephemeral logs and temp exports.
- ``data/reports/``  curated analysis artifacts meant to be kept.

Every directory location is defined here so a future move is a one-line change.
"""
from __future__ import annotations

from pathlib import Path

# Tier roots (relative to the repository root).
INPUTS = "data/inputs"
DERIVED = "data/derived"
OUTPUTS = "data/outputs"
SCRATCH = "data/scratch"
REPORTS = "data/reports"

# Leaf locations (relative strings, suitable as argparse defaults).
RUNS = "data/outputs/runs"
CAMPAIGNS = "data/outputs/campaigns"
CANONICAL = "data/derived/canonical"
TOPOLOGY_REGISTRY = "data/derived/registries/topology"
OPERATING_POINT_REGISTRY = "data/derived/registries/operating_point"
CONTINGENCY_SET_REGISTRY = "data/derived/registries/contingency_set"
LOGS = "data/scratch/logs"
TMP = "data/scratch/tmp"
MANUAL_SOURCES = "data/inputs/manual_sources"

# Tiers clean_workspace.py may remove (outputs + scratch are always safe;
# derived is rebuildable but only cleared on explicit request).
CLEANABLE = (OUTPUTS, SCRATCH)
REBUILDABLE = (DERIVED,)
# Tiers that must never be auto-deleted.
PROTECTED = (INPUTS, REPORTS)


def data_root(repo_root: Path) -> Path:
    return Path(repo_root) / "data"


def runs_root(repo_root: Path) -> Path:
    return Path(repo_root) / RUNS


def campaigns_root(repo_root: Path) -> Path:
    return Path(repo_root) / CAMPAIGNS


def campaign_root(repo_root: Path, campaign_id: str) -> Path:
    return campaigns_root(repo_root) / campaign_id


def canonical_dir(repo_root: Path) -> Path:
    return Path(repo_root) / CANONICAL


def topology_registry_dir(repo_root: Path) -> Path:
    return Path(repo_root) / TOPOLOGY_REGISTRY


def operating_point_registry_dir(repo_root: Path) -> Path:
    return Path(repo_root) / OPERATING_POINT_REGISTRY


def contingency_set_registry_dir(repo_root: Path) -> Path:
    return Path(repo_root) / CONTINGENCY_SET_REGISTRY


def logs_dir(repo_root: Path) -> Path:
    return Path(repo_root) / LOGS


def tmp_dir(repo_root: Path) -> Path:
    return Path(repo_root) / TMP


def reports_dir(repo_root: Path) -> Path:
    return Path(repo_root) / REPORTS


def manual_sources_dir(repo_root: Path) -> Path:
    return Path(repo_root) / MANUAL_SOURCES
