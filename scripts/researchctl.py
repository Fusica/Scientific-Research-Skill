#!/usr/bin/env python3
"""Public CLI facade for deterministic project-local research state."""

from __future__ import annotations

if __package__:
    from .researchctl_core import ResearchCtlError, main, sha256_file
else:
    from researchctl_core import ResearchCtlError, main, sha256_file

__all__ = ["ResearchCtlError", "main", "sha256_file"]


if __name__ == "__main__":
    raise SystemExit(main())
