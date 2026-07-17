from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.researchctl_core.dashboard import cmd_dashboard
from scripts.researchctl_core.policy import load_policy

try:
    from .research_test_support import ResearchProjectTestCase
except ImportError:  # unittest discover -s tests
    from research_test_support import ResearchProjectTestCase


class DashboardV2Test(ResearchProjectTestCase):
    @property
    def dashboard_path(self) -> Path:
        return self.project / ".research/dashboard.html"

    def test_dashboard_is_offline_escaped_read_only_and_fixed_to_one_file(self) -> None:
        disabled = self.run_ctl("disable", "--reason", "Dashboard disabled test.")
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        state = self.load_state()
        state["project_name"] = '<script>alert("project")</script>'
        self.write_state(state)
        before = self.state_path.read_bytes()

        result = self.run_ctl("dashboard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state_path.read_bytes(), before)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy", page)
        self.assertNotIn("<script>", page.casefold())
        self.assertNotIn("https://", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("fetch(", page)
        self.assertIn("&lt;script&gt;alert(&quot;project&quot;)&lt;/script&gt;", page)
        self.assertIn("disabled", page)
        self.assertEqual(list((self.project / ".research").glob("dashboard*.html")), [self.dashboard_path])

    def test_dashboard_shows_current_revision_history_and_live_file_states(self) -> None:
        artifact_id, source, first = self.register(
            "idea.idea_card", "IDEA-PORTFOLIO", content="revision one\n"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        source.write_text("revision two\n", encoding="utf-8")
        _artifact_id, _source, second = self.register(
            "idea.idea_card", artifact_id, path=source
        )
        self.assertEqual(second.returncode, 0, second.stderr)

        clean = self.run_ctl("dashboard")
        self.assertEqual(clean.returncode, 0, clean.stderr)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn("current r2", page)
        self.assertIn("历史版本：<strong>2</strong>", page)
        self.assertIn("revision timeline (2)", page)
        self.assertIn("<strong>r1</strong>", page)
        self.assertIn("<strong>r2</strong>", page)
        self.assertIn('badge clean">clean', page)

        source.write_text("unregistered edit\n", encoding="utf-8")
        dirty = self.run_ctl("dashboard")
        self.assertEqual(dirty.returncode, 0, dirty.stderr)
        self.assertIn('badge dirty">dirty', self.dashboard_path.read_text(encoding="utf-8"))

        source.unlink()
        missing = self.run_ctl("dashboard")
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertIn('badge missing">missing', self.dashboard_path.read_text(encoding="utf-8"))

    def test_dashboard_rebuilds_bidirectional_record_trace(self) -> None:
        source_id, _source, registered = self.register(
            "idea.idea_card", "IDEA-DASHBOARD-TRACE"
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        revision = self.artifact_entry("idea.idea_card", source_id)[
            "current_revision"
        ]
        manifest = {
            "schema_version": "1.0",
            "stage": "idea",
            "records": [
                {
                    "record_id": "IDEA-DASH-A",
                    "record_kind": "candidate",
                    "source": {
                        "artifact_role": "idea_card",
                        "artifact_id": source_id,
                        "revision": revision,
                        "locator": "#idea-dash-a",
                    },
                    "supersedes": None,
                    "relations": [],
                },
                {
                    "record_id": "IDEA-DASH-B",
                    "record_kind": "candidate",
                    "source": {
                        "artifact_role": "idea_card",
                        "artifact_id": source_id,
                        "revision": revision,
                        "locator": "#idea-dash-b",
                    },
                    "supersedes": None,
                    "relations": [
                        {
                            "relation": "derived_from",
                            "target_id": "IDEA-DASH-A",
                        }
                    ],
                },
            ],
        }
        path = self.project / "work/idea/dashboard-records.json"
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _identifier, _path, records = self.register(
            "idea.record_manifest", "IDEA-DASHBOARD-RECORDS", path=path
        )
        self.assertEqual(records.returncode, 0, records.stderr)

        result = self.run_ctl("dashboard", "--verify")

        self.assertEqual(result.returncode, 0, result.stderr)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn("PROJECT-LOCAL TRACE GRAPH", page)
        self.assertIn('id="record-IDEA-DASH-A"', page)
        self.assertIn('href="#record-IDEA-DASH-A"', page)
        self.assertIn("derived_from", page)
        self.assertIn("2 nodes", page)
        self.assertIn("1 edges", page)

    def test_dashboard_shows_candidate_selection_and_exact_gate_bound_revision(self) -> None:
        approved = self.approve_gate("idea_freeze", selected_id="IDEA-003")
        self.assertEqual(approved.returncode, 0, approved.stderr)

        result = self.run_ctl("dashboard", "--verify")

        self.assertEqual(result.returncode, 0, result.stderr)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn("IDEA-003", page)
        self.assertIn("Gate-bound r1", page)
        self.assertIn("Latest Gate-bound revisions", page)
        self.assertIn("idea.idea_card", page)
        self.assertIn("idea_freeze:", page)
        self.assertIn("method_experiment_approval=pending", page)

    def test_dashboard_makes_reopen_and_downstream_invalidation_visible(self) -> None:
        idea = self.approve_gate("idea_freeze")
        self.assertEqual(idea.returncode, 0, idea.stderr)
        method = self.approve_gate("method_experiment_approval")
        self.assertEqual(method.returncode, 0, method.stderr)
        reopened = self.gate("reopen", "method_experiment_approval")
        self.assertEqual(reopened.returncode, 0, reopened.stderr)

        result = self.run_ctl("dashboard")

        self.assertEqual(result.returncode, 0, result.stderr)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn('badge reopened">reopened', page)
        self.assertIn("claim_freeze=pending", page)
        self.assertIn("release/initial_submission=pending", page)
        self.assertIn("release/revision_rebuttal=pending", page)
        self.assertIn("decision history (2)", page)

    def test_dashboard_shows_lifecycle_and_activation_history(self) -> None:
        self.register("idea.idea_card", "DASHBOARD-LIFECYCLE")
        terminated = self.run_ctl(
            "lifecycle",
            "terminate",
            "--reason",
            "Stop <unsafe> direction.",
            "--supporting-evidence-id",
            "EVID-DASHBOARD-STOP",
            "--decision-condition",
            "Reopen only for the same mainline.",
        )
        self.assertEqual(terminated.returncode, 0, terminated.stderr)
        reopened = self.lifecycle("reopen")
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        disabled = self.run_ctl(
            "disable", "--reason", "External work <without supervision>."
        )
        self.assertEqual(disabled.returncode, 0, disabled.stderr)

        result = self.run_ctl("dashboard")

        self.assertEqual(result.returncode, 0, result.stderr)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn("lifecycle <strong>active</strong>", page)
        self.assertIn("lifecycle active → terminated", page)
        self.assertIn("lifecycle terminated → active", page)
        self.assertIn("Stop &lt;unsafe&gt; direction.", page)
        self.assertIn("supervision disable", page)
        self.assertIn("External work &lt;without supervision&gt;.", page)

    def test_dashboard_shows_independent_release_target_states_after_cascade(self) -> None:
        self.advance_through_claim_freeze()
        initial = self.approve_gate("release", release_target="initial_submission")
        self.assertEqual(initial.returncode, 0, initial.stderr)
        cascaded = self.gate("reopen", "idea_freeze")
        self.assertEqual(cascaded.returncode, 0, cascaded.stderr)
        for gate in ("idea_freeze", "method_experiment_approval", "claim_freeze"):
            approved = self.approve_gate(gate)
            self.assertEqual(approved.returncode, 0, approved.stderr)

        result = self.run_ctl("dashboard")

        self.assertEqual(result.returncode, 0, result.stderr)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn("release/initial_submission", page)
        self.assertIn('badge reopened">reopened', page)
        self.assertIn("release/revision_rebuttal=pending", page)
        self.assertIn("paper.manuscript", page)
        self.assertIn("cascade from <code>idea_freeze</code>", page)

    def test_dashboard_shows_only_active_retrospective_mode_contract(self) -> None:
        self.approve_gate("idea_freeze")
        self.approve_gate("method_experiment_approval")
        for role in (
            "paper.manuscript",
            "experiment_results.claim_ledger",
            "experiment_results.provenance_gap_record",
        ):
            _artifact_id, _source, registered = self.register(role)
            self.assertEqual(registered.returncode, 0, registered.stderr)
        approved = self.gate("approve", "claim_freeze", retrospective=True)
        self.assertEqual(approved.returncode, 0, approved.stderr)

        active = self.run_ctl("dashboard")
        self.assertEqual(active.returncode, 0, active.stderr)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn("approval mode <code>retrospective_revision_import</code>", page)
        self.assertIn("paper.manuscript", page)
        self.assertIn("experiment_results.provenance_gap_record", page)

        reopened = self.gate("reopen", "claim_freeze")
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        inactive = self.run_ctl("dashboard")
        self.assertEqual(inactive.returncode, 0, inactive.stderr)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertNotIn("approval mode <code>retrospective_revision_import</code>", page)
        self.assertIn("experiment_results.experiment_matrix", page)

    def test_verify_reports_snapshot_tampering_but_still_renders_diagnostics(self) -> None:
        artifact_id, _source, registered = self.register("idea.idea_card", "VERIFY")
        self.assertEqual(registered.returncode, 0, registered.stderr)
        entry = self.artifact_entry("idea.idea_card", artifact_id)
        snapshot = self.project / entry["revisions"][0]["snapshot_path"]
        snapshot.write_text("tampered\n", encoding="utf-8")

        result = self.run_ctl("dashboard", "--verify")

        self.assertEqual(result.returncode, 1)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn("snapshot mismatch", page)
        self.assertIn('badge dirty">dirty', page)

    def test_dashboard_replaces_its_projection_atomically(self) -> None:
        before = self.state_path.read_bytes()
        with mock.patch(
            "scripts.researchctl_core.dashboard.os.replace", wraps=os.replace
        ) as replace:
            result = cmd_dashboard(
                self.project,
                load_policy(),
                SimpleNamespace(verify=False, open=False),
            )

        self.assertEqual(result, 0)
        replace.assert_called_once()
        temporary, destination = map(Path, replace.call_args.args)
        self.assertEqual(destination, self.dashboard_path)
        self.assertEqual(temporary.parent, self.dashboard_path.parent)
        self.assertFalse(temporary.exists())
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_invalid_state_renders_an_error_projection_without_mutating_state(self) -> None:
        state = self.load_state()
        state["current_stage"] = "not-a-stage"
        self.write_state(state)
        before = self.state_path.read_bytes()

        result = self.run_ctl("dashboard")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.state_path.read_bytes(), before)
        page = self.dashboard_path.read_text(encoding="utf-8")
        self.assertIn("unknown current_stage", page)
        self.assertIn("ERROR", page)

    def test_v1_state_never_creates_or_overwrites_dashboard(self) -> None:
        self.dashboard_path.write_bytes(b"existing dashboard bytes\n")
        state = self.load_state()
        state["schema_version"] = "1.0"
        state["workflow_version"] = "1.1.0"
        self.write_state(state)
        before_state = self.state_path.read_bytes()
        before_dashboard = self.dashboard_path.read_bytes()
        before_entries = sorted(path.name for path in self.dashboard_path.parent.iterdir())

        result = self.run_ctl("dashboard")

        self.assertEqual(result.returncode, 2)
        self.assertIn("no automatic migration", result.stderr)
        self.assertEqual(self.state_path.read_bytes(), before_state)
        self.assertEqual(self.dashboard_path.read_bytes(), before_dashboard)
        self.assertEqual(
            sorted(path.name for path in self.dashboard_path.parent.iterdir()),
            before_entries,
        )

    def test_open_failure_warns_but_does_not_change_generation_success(self) -> None:
        stderr = mock.Mock()
        with mock.patch(
            "scripts.researchctl_core.dashboard.webbrowser.open", return_value=False
        ), mock.patch("scripts.researchctl_core.dashboard.sys.stderr", stderr):
            result = cmd_dashboard(
                self.project,
                load_policy(),
                SimpleNamespace(verify=False, open=True),
            )

        self.assertEqual(result, 0)
        self.assertTrue(self.dashboard_path.is_file())
        self.assertTrue(stderr.write.called)


if __name__ == "__main__":
    unittest.main()
