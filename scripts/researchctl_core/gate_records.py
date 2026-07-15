"""Target-aware Gate record access for the single v2 state shape."""

from __future__ import annotations

from typing import Any, Iterable

from .constants import Policy


def iter_present_gate_records(state: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield every structurally present record without requiring a valid policy."""

    gates = state.get("gates")
    if not isinstance(gates, dict):
        return
    for container in gates.values():
        if not isinstance(container, dict):
            continue
        targets = container.get("targets")
        if isinstance(targets, dict):
            for record in targets.values():
                if isinstance(record, dict):
                    yield record
        else:
            yield container


def approval_targets_for_gate(policy: Policy, gate: str) -> tuple[str, ...]:
    """Return policy-ordered targets for a targeted Gate."""

    return tuple(
        target
        for candidate, target in policy.gate_sequence
        if candidate == gate and target is not None
    )


def gate_record(
    state: dict[str, Any], policy: Policy, gate: str, target: str | None = None
) -> dict[str, Any] | None:
    """Resolve one exact Gate record from the target-aware state shape."""

    gates = state.get("gates")
    container = gates.get(gate) if isinstance(gates, dict) else None
    targets = approval_targets_for_gate(policy, gate)
    if targets:
        records = container.get("targets") if isinstance(container, dict) else None
        return records.get(target) if isinstance(records, dict) else None
    if target is not None:
        return None
    return container if isinstance(container, dict) else None


def iter_gate_records(
    state: dict[str, Any], policy: Policy
) -> Iterable[tuple[str, str | None, dict[str, Any] | None]]:
    """Yield Gate records in canonical stage-exit approval order."""

    for gate, target in policy.gate_sequence:
        yield gate, target, gate_record(state, policy, gate, target)
