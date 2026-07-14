"""Project-local workspace and Git exclusion validation."""

from __future__ import annotations

from pathlib import Path

from .constants import LEGACY_RELATIVE_PATH, MEMORY_RELATIVE_PATH, Policy
from .store import git_exclude_path, run_git


def validate_workspace(
    root: Path,
    policy: Policy,
    errors: list[str],
    warnings: list[str],
) -> None:
    if not (root / MEMORY_RELATIVE_PATH).is_file():
        errors.append(f"missing project memory: {MEMORY_RELATIVE_PATH}")
    if not (root / policy.artifact_root).is_dir():
        errors.append(f"missing artifact workspace: {policy.artifact_root}")
    exclude_path = git_exclude_path(root)
    if exclude_path is None:
        warnings.append(
            "Git worktree not detected; clone-local .research/ exclusion could not be verified"
        )
    else:
        try:
            exclude_lines = (
                exclude_path.read_text(encoding="utf-8").splitlines()
                if exclude_path.exists()
                else []
            )
        except OSError as exc:
            warnings.append(f"could not read Git info/exclude: {exc}")
        else:
            if not any(line.strip() == ".research/" for line in exclude_lines):
                warnings.append(
                    ".research/ is not present in this clone's Git info/exclude"
                )
        tracked_research = run_git(root, "ls-files", "--", ".research")
        if tracked_research:
            warnings.append(
                ".research/ contains tracked files; info/exclude does not untrack them"
            )
    if (root / LEGACY_RELATIVE_PATH).exists():
        warnings.append(
            f"legacy state retained at {LEGACY_RELATIVE_PATH}; state.json is authoritative"
        )
