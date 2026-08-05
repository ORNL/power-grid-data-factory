from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from grid_data_factory.acquisition.portfolio_constraints import PortfolioConstraints
from grid_data_factory.storage import paths

from .campaign_round import run_campaign_round, run_campaign_round_streaming
from .ledgers import create_campaign_layout


class AdaptiveCampaign:
    def __init__(self, repo_root: Path, config_path: Path, campaign_id: str):
        self.repo_root = Path(repo_root)
        self.config_path = Path(config_path)
        self.campaign_id = campaign_id

        self.config = self._load_yaml(self.config_path)
        self.campaign_root = paths.campaign_root(self.repo_root, self.campaign_id)

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def initialize(self) -> None:
        create_campaign_layout(self.campaign_root, self.config)

    def _queue_fractions(self) -> dict[str, float]:
        acq = self.config["acquisition_budget"]
        return {
            "coverage": float(acq["broad_coverage"]),
            "active_set": float(acq["active_constraint_novelty"]),
            "boundary": float(acq["security_boundary"]),
            "credible_contingency": float(acq["credible_contingencies"]),
            "severity_uncertainty": float(acq["high_severity_and_uncertainty"]),
            "audit": float(acq["unscreened_audit"]),
        }

    def _constraints(self) -> PortfolioConstraints:
        pcfg = self.config.get("portfolio_constraints", {})
        return PortfolioConstraints(
            max_per_grid=int(pcfg.get("max_per_grid", 0)),
            max_per_regime=int(pcfg.get("max_per_regime", 0)),
            max_per_contingency_class=int(pcfg.get("max_per_contingency_class", 0)),
        )

    def run_round(self, round_index: int, candidates: list[dict[str, Any]], budget: int, seed: int) -> dict[str, Any]:
        return run_campaign_round(
            campaign_root=self.campaign_root,
            round_index=round_index,
            candidates=candidates,
            budget=budget,
            queue_fractions=self._queue_fractions(),
            constraints=self._constraints(),
            audit_seed=seed,
        )

    def run_round_streaming(
        self, round_index: int, candidates_path: Path, budget: int, seed: int, candidate_count: int
    ) -> dict[str, Any]:
        return run_campaign_round_streaming(
            campaign_root=self.campaign_root,
            round_index=round_index,
            candidates_path=Path(candidates_path),
            budget=budget,
            queue_fractions=self._queue_fractions(),
            constraints=self._constraints(),
            audit_seed=seed,
            candidate_count=candidate_count,
        )

