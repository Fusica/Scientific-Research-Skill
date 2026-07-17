"""Query the deterministic, read-only scientific-record projection."""

from __future__ import annotations

import copy
from collections import Counter, deque
from typing import Any, Literal

from .constants import ResearchCtlError
from .records import RecordInspection


TraceDirection = Literal["both", "upstream", "downstream"]
_DIRECTIONS = ("both", "upstream", "downstream")


def build_trace_summary(inspection: RecordInspection) -> dict[str, Any]:
    """Return deterministic JSON-ready counts and graph diagnostics."""

    records_by_kind = Counter(
        node["record_kind"]
        for node in inspection.nodes
        if isinstance(node.get("record_kind"), str)
    )
    relations_by_kind = Counter(
        edge["relation"]
        for edge in inspection.edges
        if isinstance(edge.get("relation"), str)
    )
    diagnostics = {
        key: copy.deepcopy(list(inspection.diagnostics.get(key, ())))
        for key in sorted(inspection.diagnostics)
    }
    return {
        "record_count": inspection.record_count,
        "node_count": len(inspection.nodes),
        "edge_count": len(inspection.edges),
        "records_by_kind": {
            key: records_by_kind[key] for key in sorted(records_by_kind)
        },
        "relations_by_kind": {
            key: relations_by_kind[key] for key in sorted(relations_by_kind)
        },
        "error_count": len(inspection.errors),
        "warning_count": len(inspection.warnings),
        "diagnostics": diagnostics,
        "errors": list(inspection.errors),
        "warnings": list(inspection.warnings),
    }


def query_trace(
    inspection: RecordInspection,
    record_id: str,
    *,
    direction: TraceDirection = "both",
    depth: int = 1,
) -> dict[str, Any]:
    """Return the bounded record subgraph around ``record_id``.

    ``upstream`` follows incoming declared edges, ``downstream`` follows outgoing
    declared edges, and ``both`` follows both. ``depth`` is the maximum number of
    edges from the queried record. The function never reads or writes project state.
    """

    if direction not in _DIRECTIONS:
        raise ResearchCtlError(
            "trace direction must be one of: " + ", ".join(_DIRECTIONS)
        )
    if type(depth) is not int or depth < 0:
        raise ResearchCtlError("trace depth must be a non-negative integer")

    nodes_by_id = {
        node.get("record_id"): node
        for node in inspection.nodes
        if isinstance(node.get("record_id"), str)
    }
    if record_id not in nodes_by_id:
        raise ResearchCtlError(f"unknown scientific record {record_id!r}")

    incoming: dict[str, list[tuple[str, dict[str, str]]]] = {}
    outgoing: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for edge in inspection.edges:
        source = edge["source_id"]
        target = edge["target_id"]
        outgoing.setdefault(source, []).append((target, edge))
        incoming.setdefault(target, []).append((source, edge))
    for adjacency in (incoming, outgoing):
        for entries in adjacency.values():
            entries.sort(
                key=lambda item: (
                    item[0],
                    item[1]["relation"],
                    item[1]["source_id"],
                    item[1]["target_id"],
                )
            )

    distances = {record_id: 0}
    queue: deque[str] = deque([record_id])
    while queue:
        current = queue.popleft()
        current_depth = distances[current]
        if current_depth >= depth:
            continue
        neighbors: list[tuple[str, dict[str, str]]] = []
        if direction in {"both", "upstream"}:
            neighbors.extend(incoming.get(current, ()))
        if direction in {"both", "downstream"}:
            neighbors.extend(outgoing.get(current, ()))
        neighbors.sort(
            key=lambda item: (
                item[0],
                item[1]["relation"],
                item[1]["source_id"],
                item[1]["target_id"],
            )
        )
        for neighbor, _edge in neighbors:
            if neighbor not in distances:
                distances[neighbor] = current_depth + 1
                queue.append(neighbor)

    selected = set(distances)
    nodes = []
    for identifier in sorted(selected):
        node = copy.deepcopy(nodes_by_id[identifier])
        node["distance"] = distances[identifier]
        nodes.append(node)
    edges = [
        copy.deepcopy(edge)
        for edge in inspection.edges
        if edge["source_id"] in selected and edge["target_id"] in selected
    ]
    return {
        "record_id": record_id,
        "direction": direction,
        "depth": depth,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


__all__ = ["TraceDirection", "build_trace_summary", "query_trace"]
