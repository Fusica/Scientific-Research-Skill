#!/usr/bin/env python3
"""Install the local composition skills into a Codex skills directory."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "skills"


def default_destination() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    return (Path(codex_home).expanduser() if codex_home else Path.home() / ".codex") / "skills"


def available_skills() -> dict[str, Path]:
    return {
        path.name: path
        for path in sorted(SOURCE_ROOT.iterdir())
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


def install_one(
    source: Path,
    destination_root: Path,
    mode: str,
    force: bool,
    dry_run: bool,
    timestamp: str,
) -> str:
    target = destination_root / source.name

    if target.is_symlink():
        try:
            if target.resolve() == source.resolve():
                return f"already linked: {source.name}"
        except OSError:
            pass

    if target.exists() or target.is_symlink():
        if not force:
            raise FileExistsError(
                f"{target} already exists; rerun with --force to back it up and replace it"
            )
        backup = (
            destination_root.parent
            / ".scientific-research-skill-backups"
            / timestamp
            / source.name
        )
        if dry_run:
            print(f"would back up: {target} -> {backup}")
        else:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(backup))

    if dry_run:
        return f"would {mode}: {source.name} -> {target}"

    destination_root.mkdir(parents=True, exist_ok=True)
    if mode == "link":
        target.symlink_to(source.resolve(), target_is_directory=True)
    else:
        shutil.copytree(source, target)
    action = "linked" if mode == "link" else "copied"
    return f"{action}: {source.name} -> {target}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the maintained skills/ composition layer; vendor/ is never installed."
    )
    parser.add_argument(
        "--mode",
        choices=("link", "copy"),
        default="link",
        help="Symlink skills for live development or copy independent snapshots.",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=default_destination(),
        help="Skills directory (default: $CODEX_HOME/skills or ~/.codex/skills).",
    )
    parser.add_argument(
        "--skill",
        action="append",
        dest="skills",
        help="Install only this skill; repeat for several. Default: all.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Back up and replace existing same-name entries.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    discovered = available_skills()
    selected = args.skills or list(discovered)

    unknown = sorted(set(selected) - set(discovered))
    if unknown:
        print(f"Unknown skill(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Available: {', '.join(discovered)}", file=sys.stderr)
        return 2

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        for name in selected:
            print(
                install_one(
                    discovered[name],
                    args.destination.expanduser(),
                    args.mode,
                    args.force,
                    args.dry_run,
                    timestamp,
                )
            )
    except (FileExistsError, OSError, shutil.Error) as exc:
        print(f"Installation stopped: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
