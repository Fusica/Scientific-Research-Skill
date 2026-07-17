#!/usr/bin/env python3
"""Validate one retained maintainer semantic-acceptance report."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from capability_acceptance import (
    AcceptanceInputError,
    input_error_result,
    validate_report,
)


def _emit(result: dict[str, object]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def main(arguments: list[str] | None = None) -> int:
    """Run the report validator with stable 0/1/2 exit semantics."""

    args = sys.argv[1:] if arguments is None else arguments
    if len(args) != 1:
        _emit(
            input_error_result(
                "usage: python3 scripts/validate_acceptance.py REPORT.json"
            )
        )
        return 2
    try:
        result = validate_report(Path(args[0]))
    except AcceptanceInputError as exc:
        _emit(input_error_result(str(exc)))
        return 2
    _emit(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
