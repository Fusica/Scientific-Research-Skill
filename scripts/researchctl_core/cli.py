"""Public command-line parser and error boundary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .commands import (
    cmd_artifact,
    cmd_checkpoint,
    cmd_doctor,
    cmd_gate,
    cmd_init,
    cmd_status,
    cmd_toggle,
)
from .constants import (
    GATE_ACTIONS,
    GATE_IDS,
    Policy,
    ResearchCtlError,
)
from .policy import load_policy
from .store import find_project_root, state_mutation_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="researchctl",
        description="Manage project-local Scientific Research Skill state.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="initialize and enable the current project")
    status = subparsers.add_parser("status", help="show current research state")
    status.add_argument("--json", action="store_true", help="emit raw state JSON")
    subparsers.add_parser("enable", help="enable hooks for this project")
    subparsers.add_parser("disable", help="disable hooks for this project")

    artifact = subparsers.add_parser(
        "artifact", help="register a versioned, hash-verified canonical artifact"
    )
    artifact_actions = artifact.add_subparsers(dest="artifact_action", required=True)
    register = artifact_actions.add_parser(
        "register", help="register or replace the current version of an artifact"
    )
    register.add_argument("role", help="lower_snake_case role within the stage")
    register.add_argument("--path", required=True, help="existing regular file path")
    register.add_argument("--artifact-id", required=True, help="stable artifact ID")
    register.add_argument("--version", required=True, help="artifact version")
    register.add_argument(
        "--status",
        default="current",
        help="descriptive lifecycle status, not Gate approval (default: current)",
    )
    register.add_argument(
        "--stage", help="producer stage; defaults to the current stage"
    )

    gate = subparsers.add_parser("gate", help="record an explicit Gate decision")
    gate.add_argument("action", choices=sorted(GATE_ACTIONS))
    gate.add_argument("gate", choices=GATE_IDS)
    gate.add_argument("--reason", required=True, help="non-empty decision rationale")

    checkpoint = subparsers.add_parser(
        "checkpoint", help="record a bounded resumption checkpoint"
    )
    checkpoint.add_argument("--summary", required=True, help="checkpoint summary")
    checkpoint.add_argument(
        "--stage",
        help="optionally move to a policy-allowed stage while recording the checkpoint",
    )
    subparsers.add_parser("doctor", help="validate project state and pointers")
    return parser

def dispatch_command(
    root: Path, policy: Policy, args: argparse.Namespace
) -> int:
    if args.command == "init":
        return cmd_init(root, policy, args)
    if args.command == "status":
        return cmd_status(root, policy, args)
    if args.command == "enable":
        return cmd_toggle(root, policy, args, enabled=True)
    if args.command == "disable":
        return cmd_toggle(root, policy, args, enabled=False)
    if args.command == "artifact":
        return cmd_artifact(root, policy, args)
    if args.command == "gate":
        return cmd_gate(root, policy, args)
    if args.command == "checkpoint":
        return cmd_checkpoint(root, policy, args)
    if args.command == "doctor":
        return cmd_doctor(root, policy, args)
    raise ResearchCtlError(f"unsupported command: {args.command}")

def configure_standard_streams() -> None:
    """Keep Chinese project output reliable when Windows pipes use a legacy code page."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            # Embedded hosts may expose immutable streams. Command execution can
            # still proceed, and the normal error boundary remains available.
            pass

def main(argv: list[str] | None = None) -> int:
    configure_standard_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        policy = load_policy()
        root = find_project_root()
        mutating_commands = {
            "init",
            "enable",
            "disable",
            "artifact",
            "gate",
            "checkpoint",
        }
        if args.command in mutating_commands:
            with state_mutation_lock(root, create=args.command == "init"):
                return dispatch_command(root, policy, args)
        return dispatch_command(root, policy, args)
    except ResearchCtlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, OverflowError, RecursionError) as exc:
        print(f"error: unexpected local I/O or data failure: {exc}", file=sys.stderr)
        return 2
