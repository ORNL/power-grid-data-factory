from __future__ import annotations

from collections import defaultdict
from typing import Any


def build_component_dependency_graph(components: list[dict[str, Any]], links: list[dict[str, Any]]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    valid = {str(c["component_id"]) for c in components}
    for link in links:
        a = str(link.get("a"))
        b = str(link.get("b"))
        if a in valid and b in valid:
            graph[a].add(b)
            graph[b].add(a)
    return graph
