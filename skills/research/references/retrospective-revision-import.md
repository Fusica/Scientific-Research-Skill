# Conditional mode: Retrospective revision import

Load `policy.gates.claim_freeze.approval_modes.retrospective_revision_import` before using this procedure. That object is the sole authority for eligibility, required current artifacts, waivable historical roles, post-approval mutability, and claim scope. Confirm its eligibility facts explicitly; this reference does not add or relax them.

Register the current artifacts named by `required_artifact_roles`. In the registered provenance-gap artifact, account for every unavailable role considered under `waivable_historical_roles`, its surviving evidence, affected claims, unknowns, risks, and next verification action. Then run `researchctl doctor` and record the explicit decision:

```bash
researchctl gate approve claim_freeze \
  --retrospective-revision-import \
  --reason "人工确认：这是工作流启用前已完成稿件的返修接入，并接受已记录的历史证据缺口" \
  --supporting-evidence-id EVID-LEGACY-MANUSCRIPT-001 \
  --opposing-evidence-id EVID-PROVENANCE-GAP-001 \
  --unresolved-risk "历史实验 provenance 无法完整恢复" \
  --decision-condition "Claim 超出当前稿件边界或出现新实验时重开"
```

Inspect the resulting Gate record with `researchctl status --json` and `researchctl doctor`; the runtime records the approval mode and exact waived roles. Apply `policy.workspace_lifecycle.cross_workspace_reuse` to any old data reused by value. No prior Gate or claim judgment transfers, and all later work follows the normal registered path unless the policy-bound mode is explicitly approved again.
