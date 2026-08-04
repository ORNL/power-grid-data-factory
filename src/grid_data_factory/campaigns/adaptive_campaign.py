from __future__ import annotations

from pathlib import Path
from typing import Any
import re

from grid_data_factory.acquisition.portfolio_constraints import PortfolioConstraints

from .campaign_round import run_campaign_round
from .ledgers import create_campaign_layout


class AdaptiveCampaign:
    def __init__(self, repo_root: Path, config_path: Path, campaign_id: str):
        self.repo_root = Path(repo_root)
        self.config_path = Path(config_path)
        self.campaign_id = campaign_id

        self.config = self._load_yaml(self.config_path)
        self.campaign_root = self.repo_root / "data" / "campaigns" / self.campaign_id

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            return AdaptiveCampaign._fallback_parse(path)

        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @staticmethod
    def _fallback_parse(path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        out: dict[str, Any] = {
            "acquisition_budget": {},
            "portfolio_constraints": {},
        }

        section = ""
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue

            m_section = re.match(r"^([A-Za-z0-9_]+):\s*$", line)
            if m_section:
                section = m_section.group(1)
                continue

            if section in {"acquisition_budget", "portfolio_constraints"}:
                m_item = re.match(r"^\s+([A-Za-z0-9_]+):\s*([0-9.eE+\-]+)\s*$", line)
                if not m_item:
                    continue
                key = m_item.group(1)
                token = m_item.group(2)
                if section == "acquisition_budget":
                    out[section][key] = float(token)
                else:
                    out[section][key] = int(float(token))

        if not out["acquisition_budget"]:
            out["acquisition_budget"] = {
                "broad_coverage": 0.25,
                "active_constraint_novelty": 0.20,
                "security_boundary": 0.20,
                "credible_contingencies": 0.15,
                "high_severity_and_uncertainty": 0.10,
                "unscreened_audit": 0.10,
            }
        if not out["portfolio_constraints"]:
            out["portfolio_constraints"] = {
                "max_per_grid": 0,
                "max_per_regime": 0,
                "max_per_contingency_class": 0,
            }

        return out

    def initialize(self) -> None:
        create_campaign_layout(self.campaign_root, self.config)

    def run_round(self, round_index: int, candidates: list[dict[str, Any]], budget: int, seed: int) -> dict[str, Any]:
        acq = self.config["acquisition_budget"]
        queue_fractions = {
            "coverage": float(acq["broad_coverage"]),
            "active_set": float(acq["active_constraint_novelty"]),
            "boundary": float(acq["security_boundary"]),
            "credible_contingency": float(acq["credible_contingencies"]),
            "severity_uncertainty": float(acq["high_severity_and_uncertainty"]),
            "audit": float(acq["unscreened_audit"]),
        }

        pcfg = self.config.get("portfolio_constraints", {})
        constraints = PortfolioConstraints(
            max_per_grid=int(pcfg.get("max_per_grid", 0)),
            max_per_regime=int(pcfg.get("max_per_regime", 0)),
            max_per_contingency_class=int(pcfg.get("max_per_contingency_class", 0)),
        )

        return run_campaign_round(
            campaign_root=self.campaign_root,
            round_index=round_index,
            candidates=candidates,
            budget=budget,
            queue_fractions=queue_fractions,
            constraints=constraints,
            audit_seed=seed,
        )
