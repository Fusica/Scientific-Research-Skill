"""Render a static, read-only projection of project research state."""

from __future__ import annotations

import hashlib
import html
import os
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Any, Iterable

from .constants import DASHBOARD_RELATIVE_PATH, Policy, ResearchCtlError
from .doctor import validate_state
from .gate_records import gate_record, iter_gate_records
from .gates import required_artifact_roles_for_gate
from .store import load_state, require_compatible_state
from .timeutils import utc_now


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _resolve(root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else root / candidate
    except (OSError, RuntimeError, ValueError):
        return None


def _file_state(root: Path, value: Any, content_hash: Any, size_bytes: Any) -> str:
    path = _resolve(root, value)
    if path is None or not path.exists():
        return "missing"
    if not path.is_file():
        return "dirty"
    try:
        if type(size_bytes) is not int or path.stat().st_size != size_bytes:
            return "dirty"
        return "clean" if _sha256(path) == content_hash else "dirty"
    except OSError:
        return "dirty"


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        if os.name != "nt":
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
    except OSError as exc:
        raise ResearchCtlError(f"cannot atomically write dashboard {path}: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def _collect_artifacts(root: Path, state: dict[str, Any], policy: Policy) -> list[dict[str, Any]]:
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    entries: list[dict[str, Any]] = []
    for stage in policy.stage_order:
        stage_bucket = artifacts.get(stage)
        if not isinstance(stage_bucket, dict):
            continue
        for role, role_bucket in stage_bucket.items():
            if not isinstance(role_bucket, dict):
                continue
            for artifact_id, entry in role_bucket.items():
                revisions = entry.get("revisions") if isinstance(entry, dict) else None
                current_revision = entry.get("current_revision") if isinstance(entry, dict) else None
                current = None
                if isinstance(revisions, list):
                    current = next(
                        (
                            revision
                            for revision in revisions
                            if isinstance(revision, dict)
                            and revision.get("revision") == current_revision
                        ),
                        None,
                    )
                source_state = (
                    _file_state(
                        root,
                        current.get("source_path"),
                        current.get("content_hash"),
                        current.get("size_bytes"),
                    )
                    if isinstance(current, dict)
                    else "missing"
                )
                snapshot_state = (
                    _file_state(
                        root,
                        current.get("snapshot_path"),
                        current.get("content_hash"),
                        current.get("size_bytes"),
                    )
                    if isinstance(current, dict)
                    else "missing"
                )
                entries.append(
                    {
                        "stage": stage,
                        "role": role,
                        "artifact_id": artifact_id,
                        "entry": entry,
                        "revisions": revisions if isinstance(revisions, list) else [],
                        "current_revision": current_revision,
                        "current": current if isinstance(current, dict) else {},
                        "source_state": source_state,
                        "snapshot_state": snapshot_state,
                    }
                )
    return entries


def _decision_refs(decision: Any) -> list[dict[str, Any]]:
    refs = decision.get("artifact_refs") if isinstance(decision, dict) else None
    return [ref for ref in refs if isinstance(ref, dict)] if isinstance(refs, list) else []


def _latest_approval(record: Any) -> dict[str, Any] | None:
    history = record.get("history") if isinstance(record, dict) else None
    if not isinstance(history, list):
        return None
    return next(
        (
            decision
            for decision in reversed(history)
            if isinstance(decision, dict) and decision.get("action") == "approve"
        ),
        None,
    )


def _gate_bindings(
    state: dict[str, Any], policy: Policy
) -> dict[tuple[str, int], list[str]]:
    bindings: dict[tuple[str, int], list[str]] = {}
    for gate, target, record in iter_gate_records(state, policy):
        history = record.get("history") if isinstance(record, dict) else None
        if not isinstance(history, list):
            continue
        for decision in history:
            if not isinstance(decision, dict) or decision.get("action") != "approve":
                continue
            for ref in _decision_refs(decision):
                label = ref.get("label")
                revision = ref.get("revision")
                if isinstance(label, str) and type(revision) is int:
                    bindings.setdefault((label, revision), []).append(
                        f"{gate}{f'/{target}' if target is not None else ''}:"
                        f"{decision.get('decision_id', '<missing>')}"
                    )
    return bindings


def _required_roles(
    policy: Policy, gate: str, target: str | None, record: Any
) -> list[str]:
    approval = _latest_approval(record)
    approval_mode = (
        approval.get("approval_mode")
        if isinstance(record, dict)
        and record.get("status") == "approved"
        and isinstance(approval, dict)
        and isinstance(approval.get("approval_mode"), str)
        else None
    )
    try:
        return list(
            required_artifact_roles_for_gate(
                policy, gate, target, approval_mode=approval_mode
            )
        )
    except ResearchCtlError:
        return []


def _render_diagnostics(errors: list[str], warnings: list[str], verified: bool) -> str:
    mode = "完整哈希验证" if verified else "结构检查"
    if not errors and not warnings:
        return f'<section class="panel"><h2>机械检查</h2><p><span class="badge clean">OK</span> {_escape(mode)}通过。</p></section>'
    items = "".join(
        f'<li class="{kind}"><strong>{kind.upper()}</strong> {_escape(message)}</li>'
        for kind, messages in (("error", errors), ("warning", warnings))
        for message in messages
    )
    return (
        '<section class="panel"><h2>机械检查</h2>'
        f'<p>{_escape(mode)}：{len(errors)} 个错误，{len(warnings)} 个警告。</p>'
        f'<ul>{items}</ul></section>'
    )


def _render_stages(state: dict[str, Any], policy: Policy) -> str:
    current = state.get("current_stage")
    items = "".join(
        '<li class="{css}"><strong>{stage}</strong><small>{label}</small>{marker}</li>'.format(
            css="current" if stage == current else "",
            stage=_escape(stage),
            label=_escape(policy.raw.get("stages", {}).get(stage, {}).get("label", stage)),
            marker='<span class="badge neutral">当前</span>' if stage == current else "",
        )
        for stage in policy.stage_order
    )
    candidates = policy.stage_transitions.get(current, [])
    transition_items = "".join(
        f'<li><code>{_escape(current)} → {_escape(candidate.get("to"))}</code></li>'
        for candidate in candidates
        if isinstance(candidate, dict)
    )
    return (
        '<section class="panel"><p class="eyebrow">STAGE MAP</p><h2>科研阶段主线</h2>'
        f'<ol class="stage-flow">{items}</ol><h3>合法迁移</h3><ul>{transition_items}</ul></section>'
    )


def _render_artifacts(
    artifacts: list[dict[str, Any]], bindings: dict[tuple[str, int], list[str]]
) -> str:
    cards: list[str] = []
    for artifact in artifacts:
        current = artifact["current"]
        artifact_label = (
            f"artifacts.{artifact['stage']}.{artifact['role']}.{artifact['artifact_id']}"
        )
        timeline = "".join(
            '<li><strong>r{revision}</strong><small>{time}</small>'
            '<code>{snapshot}</code>{bound}</li>'.format(
                revision=_escape(revision.get("revision", "?")),
                time=_escape(revision.get("registered_at", "<missing>")),
                snapshot=_escape(revision.get("snapshot_path", "<missing>")),
                bound=(
                    '<span class="badge bound">Gate-bound: {}</span>'.format(
                        _escape(", ".join(bindings.get((artifact_label, revision.get("revision")), [])))
                    )
                    if type(revision.get("revision")) is int
                    and bindings.get((artifact_label, revision["revision"]))
                    else ""
                ),
            )
            for revision in artifact["revisions"]
            if isinstance(revision, dict)
        )
        cards.append(
            '<article class="artifact-card"><div class="artifact-title">'
            '<div><code>{stage}.{role}</code><h3>{artifact_id}</h3></div>'
            '<span class="badge neutral">current r{current_revision}</span></div>'
            '<p>历史版本：<strong>{count}</strong></p>'
            '<p>source <span class="badge {source}">{source}</span> · '
            'snapshot <span class="badge {snapshot}">{snapshot}</span></p>'
            '<p class="path">{source_path}</p><details><summary>revision timeline ({count})</summary>'
            '<ol class="timeline">{timeline}</ol></details></article>'.format(
                stage=_escape(artifact["stage"]),
                role=_escape(artifact["role"]),
                artifact_id=_escape(artifact["artifact_id"]),
                current_revision=_escape(artifact["current_revision"]),
                count=len(artifact["revisions"]),
                source=_escape(artifact["source_state"]),
                snapshot=_escape(artifact["snapshot_state"]),
                source_path=_escape(current.get("source_path", "<missing>")),
                timeline=timeline,
            )
        )
    empty_state = "" if cards else '<p class="empty">尚无 v2 artifact。</p>'
    return (
        '<section class="panel"><p class="eyebrow">ARTIFACT REGISTRY V2</p>'
        '<h2>当前 revision、live source 与不可变历史</h2>'
        '<p>source 状态比较当前工作文件与已登记 revision；Gate-bound 指向审批时固定的 snapshot。</p>'
        f'<div class="artifact-grid">{"".join(cards)}</div>'
        f"{empty_state}</section>"
    )


def _render_gates(state: dict[str, Any], policy: Policy, artifacts: list[dict[str, Any]]) -> str:
    role_counts: dict[str, int] = {}
    for artifact in artifacts:
        role_counts[f"{artifact['stage']}.{artifact['role']}"] = role_counts.get(
            f"{artifact['stage']}.{artifact['role']}", 0
        ) + 1
    cards: list[str] = []
    for index, (gate, target) in enumerate(policy.gate_sequence):
        record = gate_record(state, policy, gate, target)
        status = record.get("status", "<missing>") if isinstance(record, dict) else "<missing>"
        history = record.get("history") if isinstance(record, dict) else None
        history = history if isinstance(history, list) else []
        approval = _latest_approval(record)
        selection = approval.get("selection") if isinstance(approval, dict) else None
        selected_id = selection.get("selected_id") if isinstance(selection, dict) else None
        selected_ref = selection.get("artifact_ref") if isinstance(selection, dict) else None
        selected_revision = selected_ref.get("revision") if isinstance(selected_ref, dict) else None
        active_approval = (
            approval
            if isinstance(record, dict)
            and record.get("status") == "approved"
            and isinstance(approval, dict)
            else None
        )
        approval_mode = (
            active_approval.get("approval_mode")
            if isinstance(active_approval, dict)
            else None
        )
        waived_roles = (
            active_approval.get("waived_artifact_roles")
            if isinstance(active_approval, dict)
            else None
        )
        bound_refs = _decision_refs(approval)
        bound_items = "".join(
            '<li><code>{role}</code> <strong>{artifact_id}</strong> r{revision}</li>'.format(
                role=_escape(".".join(str(ref.get("label", "")).split(".")[1:3])),
                artifact_id=_escape(ref.get("artifact_id", "<missing>")),
                revision=_escape(ref.get("revision", "?")),
            )
            for ref in bound_refs
        )
        role_items = "".join(
            f'<li><code>{_escape(role)}</code> <span class="badge neutral">{role_counts.get(role, 0)} IDs</span></li>'
            for role in _required_roles(policy, gate, target, record)
        )
        decision_items = "".join(
            '<li><strong>{action}</strong> {time}<small>{decision}</small><p>{reason}</p>{cascade}</li>'.format(
                action=_escape(decision.get("action", "<missing>")),
                time=_escape(decision.get("decided_at", "<missing>")),
                decision=_escape(decision.get("decision_id", "<missing>")),
                reason=_escape(decision.get("reason", "<missing>")),
                cascade=(
                    '<p>cascade from <code>{gate}</code> decision <code>{identifier}</code></p>'.format(
                        gate=_escape(
                            (
                                decision["cascade"].get("upstream_gate_ref", {}).get(
                                    "gate", "<missing>"
                                )
                                + (
                                    "/"
                                    + str(
                                        decision["cascade"].get(
                                            "upstream_gate_ref", {}
                                        ).get("target")
                                    )
                                    if decision["cascade"].get(
                                        "upstream_gate_ref", {}
                                    ).get("target") is not None
                                    else ""
                                )
                            )
                            if isinstance(
                                decision["cascade"].get("upstream_gate_ref"), dict
                            )
                            else "<missing>"
                        ),
                        identifier=_escape(
                            decision["cascade"].get("upstream_decision_id", "<missing>")
                        ),
                    )
                    if isinstance(decision.get("cascade"), dict)
                    else ""
                ),
            )
            for decision in history
            if isinstance(decision, dict)
        )
        downstream = []
        for downstream_gate, downstream_target in policy.gate_sequence[index + 1 :]:
            downstream_record = gate_record(
                state, policy, downstream_gate, downstream_target
            )
            downstream.append(
                f"{downstream_gate}"
                f"{f'/{downstream_target}' if downstream_target is not None else ''}="
                f"{downstream_record.get('status', '<missing>') if isinstance(downstream_record, dict) else '<missing>'}"
            )
        selection_html = (
            '<p><span class="badge selected">selected</span> '
            f'<strong>{_escape(selected_id)}</strong> · Gate-bound r{_escape(selected_revision)}</p>'
            if selected_id is not None
            else '<p class="empty">本次批准无 selected ID。</p>'
        )
        mode_html = ""
        if isinstance(approval_mode, str):
            mode_html = f'<p>approval mode <code>{_escape(approval_mode)}</code></p>'
            if isinstance(waived_roles, list):
                mode_html += (
                    '<p>waived historical roles: '
                    f'{_escape(", ".join(str(role) for role in waived_roles) or "none")}</p>'
                )
        gate_name = gate + (f"/{target}" if target is not None else "")
        cards.append(
            '<article class="gate-card"><div class="gate-title"><div><p class="eyebrow">GATE {number}</p>'
            '<h3>{gate}</h3></div><span class="badge {status}">{status}</span></div>'
            '{targets}{selection}{mode}<h4>Required roles</h4><ul>{roles}</ul>'
            '<h4>Latest Gate-bound revisions</h4><ul>{bound}</ul><p>下游状态：{downstream}</p>'
            '<details><summary>decision history ({count})</summary><ol class="timeline">{history}</ol></details>'
            '</article>'.format(
                number=index + 1,
                gate=_escape(gate_name),
                status=_escape(status),
                targets="",
                selection=selection_html,
                mode=mode_html,
                roles=role_items,
                bound=bound_items or '<li class="empty">尚无批准绑定。</li>',
                downstream=_escape(", ".join(downstream) or "无"),
                count=len(history),
                history=decision_items,
            )
        )
    return (
        '<section class="panel"><p class="eyebrow">HUMAN GATES</p>'
        '<h2>选择、绑定 revision 与下游失效状态</h2>'
        f'<div class="gate-grid">{"".join(cards)}</div></section>'
    )


def _render_timeline(
    state: dict[str, Any], policy: Policy, artifacts: list[dict[str, Any]]
) -> str:
    events: list[tuple[str, str]] = []
    for artifact in artifacts:
        for revision in artifact["revisions"]:
            if isinstance(revision, dict):
                events.append(
                    (
                        str(revision.get("registered_at", "")),
                        f"artifact {artifact['artifact_id']} r{revision.get('revision', '?')} registered",
                    )
                )
    for gate, target, record in iter_gate_records(state, policy):
        history = record.get("history") if isinstance(record, dict) else None
        for decision in history if isinstance(history, list) else []:
            if isinstance(decision, dict):
                events.append(
                    (
                        str(decision.get("decided_at", "")),
                        f"Gate {gate}{f'/{target}' if target is not None else ''} "
                        f"{decision.get('action', '?')}",
                    )
                )
    lifecycle = state.get("lifecycle")
    lifecycle_history = (
        lifecycle.get("history") if isinstance(lifecycle, dict) else None
    )
    for decision in lifecycle_history if isinstance(lifecycle_history, list) else []:
        if isinstance(decision, dict):
            events.append(
                (
                    str(decision.get("decided_at", "")),
                    "lifecycle "
                    f"{decision.get('previous_status', '?')} → "
                    f"{decision.get('new_status', '?')} · "
                    f"{decision.get('decision_id', '<missing>')} · "
                    f"{decision.get('reason', '<missing>')}",
                )
            )
    activation_history = state.get("activation_history")
    for event in activation_history if isinstance(activation_history, list) else []:
        if isinstance(event, dict):
            events.append(
                (
                    str(event.get("decided_at", "")),
                    f"supervision {event.get('action', '?')} · "
                    f"{event.get('reason', '<missing>')}",
                )
            )
    for transition in state.get("stage_history", []):
        if isinstance(transition, dict):
            events.append(
                (
                    str(transition.get("timestamp", "")),
                    f"stage {transition.get('from_stage', '?')} → {transition.get('to_stage', '?')}",
                )
            )
    items = "".join(
        f'<li><time>{_escape(timestamp or "<missing>")}</time><strong>{_escape(label)}</strong></li>'
        for timestamp, label in sorted(events, key=lambda item: item[0])
    )
    empty_state = "" if items else '<p class="empty">尚无事件。</p>'
    return (
        '<section class="panel"><p class="eyebrow">RECOVERY TIMELINE</p><h2>可恢复事件</h2>'
        f'<ol class="timeline">{items}</ol>'
        f"{empty_state}</section>"
    )


def _document(
    root: Path,
    state: dict[str, Any],
    policy: Policy,
    errors: list[str],
    warnings: list[str],
    verified: bool,
) -> str:
    artifacts = _collect_artifacts(root, state, policy)
    bindings = _gate_bindings(state, policy)
    generated = utc_now()
    css = """
:root{color-scheme:dark;--bg:#08111f;--panel:#111d2e;--line:#2b3d55;--text:#edf4ff;--muted:#9bb0c9;--accent:#69d2b0;--warn:#ffc66d;--bad:#ff7f87}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}main{max-width:1240px;margin:auto;padding:28px}.hero,.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:18px}.hero{display:flex;justify-content:space-between;gap:20px}.eyebrow{color:var(--accent);font-size:12px;letter-spacing:.12em}.badge{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 8px;font-size:12px}.clean,.approved,.selected,.bound{color:var(--accent)}.dirty,.reopened,.warning{color:var(--warn)}.missing,.error{color:var(--bad)}.stage-flow,.gate-grid,.artifact-grid{display:grid;gap:12px}.stage-flow{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));padding:0}.stage-flow li,.artifact-card,.gate-card{list-style:none;border:1px solid var(--line);border-radius:12px;padding:14px}.stage-flow .current{outline:2px solid var(--accent)}.stage-flow small,.gate-title small,.timeline small{display:block;color:var(--muted)}.artifact-grid,.gate-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.artifact-title,.gate-title{display:flex;justify-content:space-between;gap:10px}.path,code{overflow-wrap:anywhere;color:#b9d5ff}.timeline{padding-left:20px}.timeline li{margin:8px 0}.timeline time{display:block;color:var(--muted);font-size:12px}.empty{color:var(--muted)}@media(max-width:800px){main{padding:12px}.hero{display:block}.stage-flow,.artifact-grid,.gate-grid{grid-template-columns:1fr}}
"""
    return "".join(
        [
            "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            f"<title>{_escape(state.get('project_name', root.name))} · Research Dashboard</title>",
            f"<style>{css}</style></head><body><main>",
            '<header class="hero"><div><p class="eyebrow">SCIENTIFIC RESEARCH WORKSPACE</p>',
            f"<h1>{_escape(state.get('project_name', root.name))}</h1>",
            f"<p>Project ID: <code>{_escape(state.get('project_id', '<missing>'))}</code></p></div>",
            f'<div><p>current_stage <strong>{_escape(state.get("current_stage", "<missing>"))}</strong></p>',
            f'<p>lifecycle <strong>{_escape(state.get("lifecycle", {}).get("status", "<missing>") if isinstance(state.get("lifecycle"), dict) else "<missing>")}</strong></p>',
            f'<p>workflow <strong>{"enabled" if state.get("enabled") is True else "disabled"}</strong></p>',
            f"<p>generated {_escape(generated)}</p></div></header>",
            _render_diagnostics(errors, warnings, verified),
            _render_stages(state, policy),
            _render_artifacts(artifacts, bindings),
            _render_gates(state, policy, artifacts),
            _render_timeline(state, policy, artifacts),
            "</main></body></html>",
        ]
    )


def cmd_dashboard(root: Path, policy: Policy, args: Any) -> int:
    state = load_state(root)
    require_compatible_state(state, policy)
    verify = bool(getattr(args, "verify", False))
    errors, warnings = validate_state(
        root,
        state,
        policy,
        verify_artifact_integrity=verify,
    )
    destination = root / DASHBOARD_RELATIVE_PATH
    _atomic_write_text(destination, _document(root, state, policy, errors, warnings, verify))
    print(f"generated read-only dashboard: {destination}")
    if bool(getattr(args, "open", False)):
        try:
            opened = webbrowser.open(destination.resolve().as_uri(), new=2)
        except (OSError, webbrowser.Error) as exc:
            opened = False
            print(f"warning: could not open dashboard in browser: {exc}", file=sys.stderr)
        if not opened:
            print(
                "warning: dashboard was generated but no browser accepted the open request",
                file=sys.stderr,
            )
    return 1 if errors else 0
