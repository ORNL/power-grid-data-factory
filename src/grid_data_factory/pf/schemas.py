from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeneratorControl(BaseModel):
    pg: float
    vg: float
    qg: float | None = None


class PFControls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generators: dict[str, GeneratorControl]
    transformer_taps: dict[str, float] = Field(default_factory=dict)
    transformer_shifts: dict[str, float] = Field(default_factory=dict)
    shunts: dict[str, float] = Field(default_factory=dict)


class ResponsePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: Literal[
        "fixed_controls_slack_loss",
        "reserve_participation",
        "fixed_participation",
        "governor_droop",
    ]
    participation_factors: dict[str, float] = Field(default_factory=dict)
    droop_coefficients: dict[str, float] = Field(default_factory=dict)
    reactive_limit_mode: Literal["enforce"] = "enforce"
    pv_to_pq: bool = True

    @model_validator(mode="after")
    def require_policy_weights(self) -> "ResponsePolicy":
        if self.policy_id == "fixed_participation" and not self.participation_factors:
            raise ValueError("fixed_participation requires participation_factors")
        if self.policy_id == "governor_droop" and not self.droop_coefficients:
            raise ValueError("governor_droop requires droop_coefficients")
        return self


class PFCandidate(BaseModel):
    model_config = ConfigDict(extra="allow")

    candidate_id: str
    case_id: str
    task: Literal["pf"] = "pf"
    source_ac_opf_run_id: str
    parent_network_id: str
    parent_topology_id: str
    parent_topology_hash: str
    topology_id: str
    topology_hash: str
    dataset_split: Literal["train", "validation", "test", "ood"]
    resolved_case: dict[str, Any]
    controls: PFControls
    control_sampling_method: str
    control_distance: float = Field(ge=0.0)
    control_distance_stratum: str
    response_policy: ResponsePolicy
    contingency: dict[str, Any] | None = None

    @model_validator(mode="after")
    def controls_match_resolved_case(self) -> "PFCandidate":
        generators = [gen for gen in self.resolved_case.get("generators", []) if int(gen.get("status", 1)) > 0]
        generator_ids = {str(gen.get("gen_id")) for gen in generators}
        unknown = sorted(set(self.controls.generators) - generator_ids)
        missing = sorted(generator_ids - set(self.controls.generators))
        if unknown:
            raise ValueError(f"Controls reference generators absent from resolved_case: {unknown}")
        if missing:
            raise ValueError(f"Active generators missing controls: {missing}")
        voltage_by_bus: dict[str, float] = {}
        for generator in generators:
            gid = str(generator["gen_id"])
            bus_id = str(generator["bus_id"])
            voltage = self.controls.generators[gid].vg
            if bus_id in voltage_by_bus and abs(voltage_by_bus[bus_id] - voltage) > 1e-9:
                raise ValueError(f"Conflicting voltage controls at generator bus {bus_id}")
            voltage_by_bus[bus_id] = voltage
        return self


class PFSampleMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_ac_opf_run_id: str
    parent_network_id: str
    parent_topology_id: str
    parent_topology_hash: str
    topology_hash: str
    dataset_split: Literal["train", "validation", "test", "ood"]
    control_sampling_method: str
    control_distance: float = Field(ge=0.0)
    control_distance_stratum: str
    response_policy_id: str
    outcome_class: Literal[
        "converged_valid",
        "converged_invalid",
        "nonconvergent",
        "islanded",
        "software_model_error",
    ]

    @model_validator(mode="after")
    def require_pf_lineage(self) -> "PFSampleMetadata":
        for value in (
            self.source_ac_opf_run_id,
            self.parent_network_id,
            self.parent_topology_id,
            self.topology_hash,
            self.response_policy_id,
        ):
            if not value.strip():
                raise ValueError("PF lineage and response-policy fields must be non-empty")
        return self