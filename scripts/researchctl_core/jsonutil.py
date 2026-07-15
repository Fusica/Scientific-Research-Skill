"""Strict JSON decoding shared by policy and project state loaders."""

from __future__ import annotations

import json
from typing import Any


class DuplicateJsonKeyError(ValueError):
    """A JSON object repeated a key and therefore has ambiguous authority."""

    def __init__(self, key: str) -> None:
        super().__init__(f"duplicate object key {key!r}")
        self.key = key


class NonStandardJsonConstantError(ValueError):
    """JSON used a non-standard NaN or infinity token."""

    def __init__(self, value: str) -> None:
        super().__init__(f"non-standard numeric constant {value!r}")
        self.value = value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKeyError(key)
        value[key] = item
    return value


def _reject_nonstandard_constant(value: str) -> Any:
    raise NonStandardJsonConstantError(value)


def strict_json_loads(text: str) -> Any:
    """Decode JSON while rejecting duplicate keys at every object depth."""

    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonstandard_constant,
    )
