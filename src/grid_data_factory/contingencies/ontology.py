from __future__ import annotations

ONTOLOGY_CLASSES = {
    "independent_random",
    "electrically_interacting",
    "parallel_circuit",
    "common_corridor",
    "common_tower",
    "common_substation",
    "common_bus",
    "common_protection_zone",
    "common_generation_plant",
    "generator_export_path",
    "weather_correlated",
    "sequential_n1n1",
    "cut_set",
    "cascade_induced",
    "adversarial_but_credible",
}


def validate_ontology_classes(labels: list[str]) -> None:
    unknown = [x for x in labels if x not in ONTOLOGY_CLASSES]
    if unknown:
        raise ValueError(f"Unknown contingency ontology labels: {unknown}")
