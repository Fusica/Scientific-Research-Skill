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
    cmd_lifecycle,
    cmd_status,
    cmd_toggle,
)
from .dashboard import cmd_dashboard
from .constants import (
    CLEAN_BREAK_REINIT_GUIDANCE,
    LEGACY_RELATIVE_PATH,
    Policy,
    ResearchCtlError,
    STATE_RELATIVE_PATH,
)
from .policy import load_policy, retrospective_gate_contract
from .store import (
    find_project_root,
    load_state,
    require_compatible_state,
    state_mutation_lock,
)


def build_parser(policy: Policy) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="researchctl",
        description="Manage project-local Scientific Research Skill state.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="initialize and enable the current project")
    status = subparsers.add_parser("status", help="show current research state")
    status.add_argument("--json", action="store_true", help="emit raw state JSON")
    for action in ("enable", "disable"):
        toggle = subparsers.add_parser(
            action, help=f"{action} hooks for this project with an audit reason"
        )
        toggle.add_argument("--reason", required=True, help="non-empty audit reason")

    artifact = subparsers.add_parser(
        "artifact", help="register a revision with an immutable verified snapshot"
    )
    artifact_actions = artifact.add_subparsers(dest="artifact_action", required=True)
    register = artifact_actions.add_parser(
        "register", help="register the current source as the next automatic revision"
    )
    register.add_argument("role", help="lower_snake_case role within the stage")
    register.add_argument("--path", required=True, help="existing regular file path")
    register.add_argument("--artifact-id", required=True, help="stable artifact ID")
    register.add_argument(
        "--stage", help="producer stage; defaults to the current stage"
    )

    def add_decision_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--reason", required=True, help="non-empty decision rationale")
        command.add_argument(
            "--supporting-evidence-id",
            action="append",
            required=True,
            help="supporting evidence ID; repeat for multiple IDs",
        )
        command.add_argument(
            "--opposing-evidence-id",
            action="append",
            default=[],
            help="opposing evidence ID; repeat for multiple IDs",
        )
        command.add_argument(
            "--unresolved-risk",
            action="append",
            default=[],
            help="unresolved risk; repeat for multiple risks",
        )
        command.add_argument(
            "--decision-condition",
            action="append",
            required=True,
            help="stop or reopen condition; repeat for multiple conditions",
        )

    gate = subparsers.add_parser("gate", help="record an explicit Gate decision")
    gate.add_argument("action", help="policy-supported Gate action")
    gate.add_argument("gate", help="Gate ID from the active policy")
    add_decision_arguments(gate)
    gate.add_argument(
        "--target",
        help="required exact target for a Gate that defines approval_targets",
    )
    gate.add_argument(
        "--selected-id",
        help=(
            "candidate ID selected inside the Gate portfolio artifact; required only "
            "when policy defines selection_artifact_role"
        ),
    )
    gate.add_argument(
        "--approval-mode",
        help=(
            "policy-defined approval mode key; valid only for Gate approve when the "
            "named Gate defines approval_modes"
        ),
    )
    retrospective = retrospective_gate_contract(policy)
    if retrospective is not None:
        retrospective_gate, retrospective_mode, retrospective_spec = retrospective
        gate.add_argument(
            retrospective_spec["cli_flag"],
            dest="retrospective_mode_requested",
            action="store_const",
            const=retrospective_mode,
            help=(
                "use the policy-defined retrospective evidence exception; valid only "
                f"with `gate approve {retrospective_gate}`"
            ),
        )

    lifecycle = subparsers.add_parser(
        "lifecycle", help="record an explicit project lifecycle decision"
    )
    lifecycle.add_argument("action", choices=policy.runtime.lifecycle_actions)
    add_decision_arguments(lifecycle)
    lifecycle.add_argument(
        "--gate", help="earliest affected approved Gate to reopen with the project"
    )
    lifecycle.add_argument(
        "--target", help="exact target when --gate names a targeted Gate"
    )

    checkpoint = subparsers.add_parser(
        "checkpoint", help="record a bounded resumption checkpoint"
    )
    checkpoint.add_argument("--summary", required=True, help="checkpoint summary")
    checkpoint.add_argument(
        "--stage",
        help="optionally move to a policy-allowed stage while recording the checkpoint",
    )
    dashboard = subparsers.add_parser(
        "dashboard", help="generate a read-only project research dashboard"
    )
    dashboard.add_argument(
        "--verify",
        action="store_true",
        help="verify registered artifact hashes while generating the dashboard",
    )
    dashboard.add_argument(
        "--open",
        action="store_true",
        help="best-effort open the generated dashboard in the default browser",
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
    if args.command == "lifecycle":
        return cmd_lifecycle(root, policy, args)
    if args.command == "checkpoint":
        return cmd_checkpoint(root, policy, args)
    if args.command == "dashboard":
        return cmd_dashboard(root, policy, args)
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
    try:
        policy = load_policy()
        parser = build_parser(policy)
        args = parser.parse_args(argv)
        root = find_project_root()
        mutating_commands = {
            "init",
            "enable",
            "disable",
            "artifact",
            "gate",
            "lifecycle",
            "checkpoint",
        }
        if args.command in mutating_commands:
            if (
                args.command == "init"
                and not (root / STATE_RELATIVE_PATH).exists()
                and (root / LEGACY_RELATIVE_PATH).exists()
            ):
                raise ResearchCtlError(
                    f"unsupported legacy state found at {LEGACY_RELATIVE_PATH}; "
                    f"{CLEAN_BREAK_REINIT_GUIDANCE}"
                )
            if args.command != "init" or (root / STATE_RELATIVE_PATH).is_file():
                # Fail an incompatible clean-break state before opening or creating
                # the transaction lock. Dispatch reloads under the lock to close the
                # normal read-modify-write race.
                require_compatible_state(load_state(root), policy)
            with state_mutation_lock(root, create=args.command == "init"):
                return dispatch_command(root, policy, args)
        return dispatch_command(root, policy, args)
    except ResearchCtlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, OverflowError, RecursionError) as exc:
        print(f"error: unexpected local I/O or data failure: {exc}", file=sys.stderr)
        return 2
