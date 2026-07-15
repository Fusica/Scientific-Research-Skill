"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..");
const HOOK = path.join(ROOT, "hooks", "research-workflow-hook.js");
const HOOKS_JSON = path.join(ROOT, "hooks", "hooks.json");
const RESEARCHCTL = path.join(ROOT, "scripts", "researchctl.py");
const POLICY = JSON.parse(fs.readFileSync(
  path.join(ROOT, "skills", "research", "references", "policy.yaml"),
  "utf8",
));
const RUNTIME_CONTRACT = JSON.parse(fs.readFileSync(
  path.join(ROOT, "skills", "research", "assets", "runtime-contract.json"),
  "utf8",
));
const PYTHON = process.env.RESEARCHCTL_TEST_PYTHON || "python3";

function command(executable, args, options = {}) {
  const result = spawnSync(executable, args, {
    cwd: options.cwd || ROOT,
    env: options.env || process.env,
    input: options.input,
    encoding: "utf8",
    timeout: 30000,
    maxBuffer: 16 * 1024 * 1024,
  });
  assert.equal(result.error, undefined, result.error && result.error.message);
  assert.equal(result.signal, null, `terminated by ${result.signal}`);
  return result;
}

function ctl(project, ...args) {
  const result = command(PYTHON, [RESEARCHCTL, ...args], {
    cwd: project,
    env: { ...process.env, RESEARCHCTL_ACTOR: "hook-test-owner" },
  });
  return result;
}

function createProject(t, options = {}) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "research-hook-v2-"));
  const project = path.join(temporary, "project");
  fs.mkdirSync(project);
  assert.equal(command("git", ["init", "-q", project]).status, 0);
  const initialized = ctl(project, "init");
  assert.equal(initialized.status, 0, initialized.stderr);
  if (options.disabled) {
    const disabled = ctl(project, "disable", "--reason", "Hook fixture disabled.");
    assert.equal(disabled.status, 0, disabled.stderr);
  }
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  return { temporary, project, sources: new Map() };
}

function statePath(fixture) {
  return path.join(fixture.project, ".research", "state.json");
}

function loadState(fixture) {
  return JSON.parse(fs.readFileSync(statePath(fixture), "utf8"));
}

function writeState(fixture, state) {
  fs.writeFileSync(statePath(fixture), `${JSON.stringify(state, null, 2)}\n`, "utf8");
}

function artifactId(roleReference) {
  return roleReference.toUpperCase().replaceAll(".", "-").replaceAll("_", "-");
}

function register(fixture, roleReference, options = {}) {
  const [stage, role] = roleReference.split(".");
  const identifier = options.artifactId || artifactId(roleReference);
  const source = options.source || path.join(fixture.project, "work", stage, `${role}.md`);
  fs.mkdirSync(path.dirname(source), { recursive: true });
  if (options.content !== undefined || !fs.existsSync(source)) {
    fs.writeFileSync(source, options.content || `# ${identifier}\n`, "utf8");
  }
  const result = ctl(
    fixture.project,
    "artifact", "register", role,
    "--stage", stage,
    "--path", source,
    "--artifact-id", identifier,
  );
  fixture.sources.set(roleReference, source);
  return { result, identifier, source };
}

function requiredRoles(gate, releaseTarget = null) {
  const spec = POLICY.gates[gate];
  if (spec.approval_targets) {
    return spec.approval_targets[releaseTarget].required_artifact_roles;
  }
  if (spec.approval_modes) {
    return spec.approval_modes[spec.default_approval_mode].required_artifact_roles;
  }
  return spec.required_artifact_roles;
}

function registerGateRequirements(fixture, gate, releaseTarget = null, overrides = {}) {
  let state = loadState(fixture);
  for (const roleReference of requiredRoles(gate, releaseTarget)) {
    const [stage, role] = roleReference.split(".");
    const bucket = state.artifacts?.[stage]?.[role];
    if (bucket && typeof bucket === "object" && Object.keys(bucket).length) continue;
    const registered = register(fixture, roleReference, { source: overrides[roleReference] });
    assert.equal(registered.result.status, 0, registered.result.stderr);
    state = loadState(fixture);
  }
}

function gate(fixture, action, gateId, selectedId = null, target = null) {
  const args = [
    "gate", action, gateId,
    "--reason", `Explicit Hook test owner decision for ${gateId} ${action}.`,
    "--supporting-evidence-id", `EVID-${gateId}-support`,
    "--decision-condition", `Stop or reopen ${gateId} if its boundary changes.`,
  ];
  if (selectedId !== null) args.push("--selected-id", selectedId);
  if (target !== null) args.push("--target", target);
  return ctl(fixture.project, ...args);
}

function lifecycle(fixture, action, gateId = null, target = null) {
  const args = [
    "lifecycle", action,
    "--reason", `Explicit Hook test owner lifecycle decision: ${action}.`,
    "--supporting-evidence-id", `EVID-LIFECYCLE-${action}`,
    "--decision-condition", `Reassess the same mainline after ${action}.`,
  ];
  if (gateId !== null) args.push("--gate", gateId);
  if (target !== null) args.push("--target", target);
  return ctl(fixture.project, ...args);
}

function approve(fixture, gateId, releaseTarget = null) {
  registerGateRequirements(fixture, gateId, releaseTarget);
  const selectedId = gateId === "idea_freeze"
    ? "IDEA-003"
    : gateId === "method_experiment_approval" ? "METHOD-002" : null;
  const result = gate(fixture, "approve", gateId, selectedId, releaseTarget);
  assert.equal(result.status, 0, result.stderr);
}

function advanceThroughMethod(fixture) {
  approve(fixture, "idea_freeze");
  approve(fixture, "method_experiment_approval");
}

function advanceThroughClaim(fixture) {
  advanceThroughMethod(fixture);
  approve(fixture, "claim_freeze");
}

function hookInput(event, cwd, overrides = {}) {
  const base = { hook_event_name: event, cwd };
  if (event === "PreToolUse" || event === "PostToolUse") {
    Object.assign(base, { tool_name: "Bash", tool_input: { command: "true" } });
  }
  if (event === "Stop") base.last_assistant_message = "done";
  return { ...base, ...overrides };
}

function runHook(event, cwd, overrides = {}, options = {}) {
  const input = options.input || hookInput(event, cwd, overrides);
  const result = command("node", [HOOK, event], {
    cwd,
    input: JSON.stringify(input),
    env: { ...process.env, PLUGIN_ROOT: ROOT, ...(options.env || {}) },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");
  assert.notEqual(result.stdout.trim(), "");
  return JSON.parse(result.stdout);
}

function denyReason(output) {
  assert.equal(
    output.hookSpecificOutput?.hookEventName,
    "PreToolUse",
    `expected deny output, received ${JSON.stringify(output)}`,
  );
  assert.equal(output.hookSpecificOutput?.permissionDecision, "deny");
  return output.hookSpecificOutput.permissionDecisionReason;
}

test("Hook config registers five command handlers through PLUGIN_ROOT", () => {
  const hooks = JSON.parse(fs.readFileSync(HOOKS_JSON, "utf8")).hooks;
  const events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"];
  assert.deepEqual(Object.keys(hooks), events);
  for (const event of events) {
    const handler = hooks[event][0].hooks[0];
    assert.equal(handler.type, "command");
    assert.match(handler.command, /\$\{PLUGIN_ROOT\}/);
    assert.match(handler.command, new RegExp(`${event}$`));
    assert.equal(handler.timeout, 5);
  }
  assert.equal(hooks.PreToolUse[0].matcher, ".*");
});

test("absent and disabled projects are strict read-only no-ops for every event", (t) => {
  const ordinary = fs.mkdtempSync(path.join(os.tmpdir(), "ordinary-hook-"));
  t.after(() => fs.rmSync(ordinary, { recursive: true, force: true }));
  const disabled = createProject(t, { disabled: true });
  const before = fs.readFileSync(statePath(disabled));
  for (const event of ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]) {
    assert.deepEqual(runHook(event, ordinary), {});
    assert.deepEqual(runHook(event, disabled.project), {});
  }
  assert.deepEqual(fs.readFileSync(statePath(disabled)), before);
  assert.equal(fs.existsSync(path.join(ordinary, ".research")), false);
});

test("SessionStart and UserPromptSubmit inject only current deterministic metadata", (t) => {
  const fixture = createProject(t);
  const nested = path.join(fixture.project, "src", "nested");
  fs.mkdirSync(nested, { recursive: true });
  const session = runHook("SessionStart", nested);
  const sessionContext = session.hookSpecificOutput.additionalContext;
  assert.match(sessionContext, /ACTIVE PROJECT/);
  assert.match(sessionContext, /Workflow activation/);
  assert.match(sessionContext, /Lifecycle: active/);
  assert.doesNotMatch(sessionContext, /artifact|snapshot|semantic audit/i);

  const prompt = runHook("UserPromptSubmit", nested, { prompt: "refactor a test" });
  const promptContext = prompt.hookSpecificOutput.additionalContext;
  assert.match(promptContext, /Current stage: idea/);
  assert.match(promptContext, /Lifecycle: active/);
  assert.match(promptContext, /idea_freeze \(pending\)/);
  assert.ok(promptContext.length <= 400);
});

test("terminal lifecycle permits audit and explicit reopen but blocks research mutation", (t) => {
  const fixture = createProject(t);
  const registered = register(fixture, "idea.idea_card", { artifactId: "TERMINAL-HOOK" });
  assert.equal(registered.result.status, 0, registered.result.stderr);
  const terminated = lifecycle(fixture, "terminate");
  assert.equal(terminated.status, 0, terminated.stderr);

  const blocked = [
    ["apply_patch", { patch: "*** Update File: notes.md\n" }],
    ["Bash", { command: "touch notes.md" }],
    ["Bash", { command: "torchrun train.py" }],
    ["researchctl:checkpoint", { summary: "not allowed" }],
    ["Bash", { command: "researchctl status && touch bypass.txt" }],
    ["Bash", { command: "git commit -m status" }],
    ["Bash", { command: "git push origin show" }],
    ["Bash", { command: "find . -delete" }],
    ["Bash", { command: "sed -i '' 's/a/b/' notes.md" }],
    ["Bash", { command: "researchctl status --json \"$(mkdir -p hidden-output)\"" }],
    ["Bash", { command: "researchctl doctor `touch hidden-output`" }],
    ["Bash", { command: "cat =(mkdir -p hidden-output)" }],
  ];
  for (const [tool_name, tool_input] of blocked) {
    const output = runHook("PreToolUse", fixture.project, { tool_name, tool_input });
    assert.match(denyReason(output), /lifecycle|terminal|reopen/i, tool_name);
  }

  for (const commandText of [
    "cat .research/state.json",
    "sed -n '1,20p' .research/state.json",
    "find . -maxdepth 2 -type f -print",
    "shasum .research/state.json",
    "git diff --stat",
    "researchctl status",
    "researchctl status --json",
    "researchctl doctor",
    "researchctl dashboard",
    "researchctl disable --reason 'manual escape'",
    "researchctl lifecycle reopen --reason x --supporting-evidence-id E --decision-condition C",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: commandText },
    }), {}, commandText);
  }

  const invalid = loadState(fixture);
  invalid.lifecycle.status = "unknown";
  writeState(fixture, invalid);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "cat .research/state.json" },
  }), {});
  assert.match(denyReason(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "touch invalid-state-write" },
  })), /lifecycle|terminal|reopen/i);
});

test("terminal lifecycle permits read-only web retrieval but still blocks mutation", (t) => {
  const fixture = createProject(t);
  const registered = register(fixture, "idea.idea_card", { artifactId: "TERMINAL-WEB" });
  assert.equal(registered.result.status, 0, registered.result.stderr);
  const terminated = lifecycle(fixture, "terminate");
  assert.equal(terminated.status, 0, terminated.stderr);

  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "web__run",
    tool_input: { search_query: [{ q: "new evidence" }] },
  }), {});
  assert.match(denyReason(runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Update File: notes.md\n" },
  })), /lifecycle|terminal|reopen/i);
});

test("disabled terminal projects remain exact no-ops", (t) => {
  const fixture = createProject(t);
  register(fixture, "idea.idea_card", { artifactId: "TERMINAL-DISABLED" });
  assert.equal(lifecycle(fixture, "terminate").status, 0);
  assert.equal(
    ctl(fixture.project, "disable", "--reason", "Use the operational escape hatch.").status,
    0,
  );
  for (const event of ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]) {
    assert.deepEqual(runHook(event, fixture.project), {});
  }
});

test("PreToolUse blocks dangerous commands and direct state mutation but permits reads", (t) => {
  const fixture = createProject(t);
  const dangerous = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "rm -rf -- /tmp/unrelated" },
  });
  assert.match(denyReason(dangerous), /dangerous operation/);

  const direct = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Update File: .research/state.json\n" },
  });
  assert.match(denyReason(direct), /Direct mutation/);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "cat .research/state.json" },
  }), {});
});

test("state authority protection covers wrappers workdirs aliases and structured paths", (t) => {
  const fixture = createProject(t);
  const control = path.join(fixture.project, ".research");
  const directoryAlias = path.join(fixture.project, "state-link");
  fs.symlinkSync(control, directoryAlias, "dir");
  const stateAlias = path.join(fixture.project, "state-alias.json");
  fs.symlinkSync(statePath(fixture), stateAlias);

  const shellCases = [
    ["sh -c 'cd .research && rm state.json'", {}],
    ["bash -lc 'cd .research && truncate -s 0 state.json'", {}],
    ["Set-Location .research; Remove-Item state.json", {}],
    ["jq '.enabled=false' .research/state.json | sponge .research/state.json", {}],
    ["rm .research/state.lock", {}],
    ["cd state-link && rm state.json", {}],
    ["echo broken > state.json", { workdir: ".research" }],
    ["find . -delete", { workdir: ".research" }],
    ["mv .research research-backup", {}],
    ["truncate -s 0 .research/state.*", {}],
    ["chmod 000 .research", {}],
  ];
  for (const [shell, extra] of shellCases) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: shell, ...extra },
    });
    assert.match(denyReason(output), /Direct mutation|dangerous operation/, shell);
  }

  const structured = [
    ["mcp__filesystem__move_file", { source: ".research/state.json", destination: "backup.json" }],
    ["mcp__filesystem__rename", { oldPath: ".research/state.json", newPath: "backup.json" }],
    ["mcp__filesystem__delete", { filename: ".research/state.json" }],
    ["mcp__filesystem__delete_directory", { path: ".research" }],
    ["mcp__filesystem__move_directory", { source: ".research", destination: "research-backup" }],
    ["mcp__filesystem__write_file", { path: "state-alias.json", content: "{}" }],
    ["mcp__files__update", {
      files: [...Array.from({ length: 24 }, (_, index) => ({ path: `ordinary-${index}.txt` })), { path: ".research/state.json" }],
    }],
  ];
  for (const [tool_name, tool_input] of structured) {
    const output = runHook("PreToolUse", fixture.project, { tool_name, tool_input });
    assert.match(denyReason(output), /Direct mutation/, tool_name);
  }
  const patchMove = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Begin Patch\n*** Update File: harmless.json\n*** Move to: .research/state.json\n*** End Patch" },
  });
  assert.match(denyReason(patchMove), /Direct mutation/);
  const unifiedPatch = runHook("PreToolUse", fixture.project, {
    tool_name: "patch",
    tool_input: { diff: "--- a/.research/state.json\n+++ b/.research/state.json\n" },
  });
  assert.match(denyReason(unifiedPatch), /Direct mutation/);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__filesystem__read_file",
    tool_input: { path: ".research/state.json" },
  }), {});
});

test("dangerous shell table covers wrappers split flags devices and safe dry runs", (t) => {
  const fixture = createProject(t);
  for (const shell of [
    "rm -r -f results",
    "rm --recursive --force results",
    "Remove-Item -Recurse -Force results",
    "git -C another-repo reset --hard",
    "git -c core.autocrlf=false reset --hard",
    "git --no-pager reset --hard",
    "git checkout HEAD -- .",
    "git checkout --ours main.tex",
    "git restore --staged --worktree .",
    "git clean --force -d",
    "bash -lc 'rm -rf results'",
    "sh -c 'git reset --hard'",
    "sudo mkfs.ext4 /dev/fake",
    "diskutil erase /dev/fake",
    "dd if=/dev/zero of=/dev/fake",
    "chmod -R 777 /",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: shell },
    });
    assert.equal(output.hookSpecificOutput?.permissionDecision, "deny", shell);
    assert.match(denyReason(output), /dangerous operation/, shell);
  }
  for (const shell of [
    "git clean -n -d",
    "git clean --dry-run -d",
    "git restore --staged main.tex",
    "git checkout feature-branch",
    "echo 'rm -rf results'",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: shell },
    }), {}, shell);
  }
});

test("experiment launch table covers runners wrappers modules and help-only calls", (t) => {
  const fixture = createProject(t);
  for (const [tool_name, shell] of [
    ["Bash", "torchrun --nproc_per_node=8 train.py"],
    ["Bash", "accelerate launch scripts/train.py"],
    ["Bash", "deepspeed train_model.py"],
    ["Bash", "python -m project.train --config run.yaml"],
    ["Bash", "python scripts/my_train.py"],
    ["Bash", "qsub run.sh && deepspeed --version"],
    ["Bash", "srun python train.py"],
    ["Bash", "mpirun python train.py"],
    ["Bash", "mpiexec python train.py"],
    ["Bash", "nohup torchrun train.py > train.log 2>&1 &"],
    ["Bash", "env -u DEBUG CUDA_VISIBLE_DEVICES=0 torchrun train.py"],
    ["Bash", "time -p deepspeed train.py"],
    ["Bash", "bash -lc 'torchrun train.py'"],
    ["Bash", "make train"],
    ["Bash", "wandb sweep sweep.yaml"],
    ["Bash", "ros2 launch package experiment.launch.py"],
    ["run_experiment", "true"],
    ["arm_drone", "true"],
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name,
      tool_input: { command: shell },
    });
    assert.match(denyReason(output), /method_experiment_approval/, `${tool_name}: ${shell}`);
  }
  for (const shell of [
    "python test_training.py",
    "python training_utils.py",
    "python -m pytest tests/train.py",
    "torchrun --help",
    "deepspeed --version",
    "accelerate launch -h",
    "srun --help",
    "echo 'python train.py'",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: shell },
    }), {}, shell);
  }
});

test("manuscript mutation table covers shell direction structured tools and patch targets", (t) => {
  const fixture = createProject(t);
  for (const shell of [
    "cp draft.tex main.tex",
    "mv revised.tex rebuttal.tex",
    "rm appendix.tex",
    "truncate -s 0 response.md",
    "rsync draft.tex paper.tex",
    "pandoc draft.md | sponge main.tex",
    "echo hi > main.tex",
    "cat draft.md >> 'rebuttal.tex'",
    "sed -i s/a/b/ appendix.tex",
    "perl -pi -e s/a/b/ supplement.tex",
    "bash -lc 'rm main.tex'",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: shell },
    });
    assert.equal(output.hookSpecificOutput?.permissionDecision, "deny", shell);
    assert.match(denyReason(output), /claim_freeze/, shell);
  }
  for (const [tool_name, tool_input] of [
    ["mcp__filesystem__move_file", { source: "main.tex", destination: "backup.tex" }],
    ["mcp__filesystem__delete", { path: "appendix.tex" }],
    ["mcp__filesystem__copy_file", { source: "draft.tex", destination: "main.tex" }],
    ["mcp__filesystem__write_file", { path: "cover-letter.docx", content: "x" }],
  ]) {
    const output = runHook("PreToolUse", fixture.project, { tool_name, tool_input });
    assert.equal(output.hookSpecificOutput?.permissionDecision, "deny", tool_name);
    assert.match(denyReason(output), /claim_freeze/, tool_name);
  }
  const patch = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Begin Patch\n*** Update File: draft.tex\n*** Move to: supplement.tex\n*** End Patch" },
  });
  assert.match(denyReason(patch), /claim_freeze/);
  for (const shell of ["cat main.tex", "git diff -- main.tex", "wc -l rebuttal.tex", "cp main.tex backup.tex"]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: shell },
    }), {}, shell);
  }
});

test("release transfer table distinguishes submissions from downloads and backups", (t) => {
  const fixture = createProject(t);
  for (const shell of [
    "scp paper.pdf conference:/submissions/",
    "sftp paper.pdf editor@example.org:/incoming/",
    "rsync rebuttal.pdf conference:/incoming/",
    "aws s3 cp paper.pdf s3://bucket/submissions/paper.pdf",
    "curl -Tpaper.pdf https://openreview.net/submissions/paper.pdf",
    "curl --upload-file paper.pdf https://softconf.com/submission/",
    "curl -Ffile=@paper.pdf https://openreview.net/submissions/",
    "curl --data-binary @paper.pdf https://openreview.net/submissions/",
    "curl -X POST --json @paper.json https://openreview.net/submissions/",
    "bash -lc 'scp paper.pdf conference:/submissions/'",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: shell },
    });
    assert.match(denyReason(output), /release/, shell);
  }
  for (const shell of [
    "scp paper.pdf backup@example.org:/archive/",
    "scp chair@conference.org:/incoming/rebuttal.pdf rebuttal.pdf",
    "aws s3 cp s3://bucket/submissions/paper.pdf paper.pdf",
    "curl https://openreview.net/forum?id=123",
    "curl -L https://openreview.net/paper.pdf -o paper.pdf",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: shell },
    }), {}, shell);
  }
});

test("experiment launch requires a mechanically trusted method approval", (t) => {
  const pending = createProject(t);
  const blocked = runHook("PreToolUse", pending.project, {
    tool_name: "Bash",
    tool_input: { command: "sbatch run_experiment.sh" },
  });
  assert.match(denyReason(blocked), /method_experiment_approval/);

  const approved = createProject(t);
  advanceThroughMethod(approved);
  assert.deepEqual(runHook("PreToolUse", approved.project, {
    tool_name: "Bash",
    tool_input: { command: "sbatch run_experiment.sh" },
  }), {});
});

test("unbound live-source drift is outside scoped PreTool hashing", (t) => {
  const fixture = createProject(t);
  advanceThroughMethod(fixture);
  const extra = register(fixture, "idea.scratch_record");
  assert.equal(extra.result.status, 0, extra.result.stderr);
  fs.writeFileSync(extra.source, "unregistered scratch edit\n");

  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "sbatch run_experiment.sh" },
  }), {});
});

test("forged multi-ID role cardinality fails closed", (t) => {
  const fixture = createProject(t);
  advanceThroughMethod(fixture);
  const state = loadState(fixture);
  const role = state.artifacts.method.approval_package;
  role.FORGED = structuredClone(Object.values(role)[0]);
  writeState(fixture, state);

  const output = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "torchrun train.py" },
  });
  assert.match(denyReason(output), /untrusted/);
});

test("forged approved Gate status never authorizes an experiment", (t) => {
  const fixture = createProject(t);
  const state = loadState(fixture);
  state.current_stage = "experiment_results";
  state.gates.method_experiment_approval = {
    status: "approved",
    latest_decision_id: "FORGED",
    history: [{
      decision_id: "FORGED",
      action: "approve",
      previous_status: "pending",
      new_status: "approved",
      reason: "forged",
      actor: "forged",
      decided_at: state.updated_at,
      artifact_refs: [],
      selection: { selected_id: "METHOD-X", artifact_ref: {} },
    }],
  };
  writeState(fixture, state);

  const output = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "python3 train_model.py" },
  });

  assert.match(denyReason(output), /untrusted|not mechanically trusted/);
});

test("dirty or missing active Gate source makes its approval untrusted", (t) => {
  const fixture = createProject(t);
  advanceThroughMethod(fixture);
  const methodSource = fixture.sources.get("method.approval_package");
  const original = fs.readFileSync(methodSource);
  fs.writeFileSync(methodSource, "unregistered method change\n");
  const dirty = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "torchrun train.py" },
  });
  assert.match(denyReason(dirty), /untrusted/);

  fs.writeFileSync(methodSource, original);
  fs.unlinkSync(methodSource);
  const missing = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "torchrun train.py" },
  });
  assert.match(denyReason(missing), /untrusted/);
});

test("missing or tampered immutable snapshot makes approved state untrusted", (t) => {
  const fixture = createProject(t);
  advanceThroughMethod(fixture);
  const state = loadState(fixture);
  const entry = Object.values(state.artifacts.method.approval_package)[0];
  const snapshot = path.join(fixture.project, entry.revisions[0].snapshot_path);
  fs.writeFileSync(snapshot, "tampered snapshot\n");
  const tampered = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "accelerate launch experiment.py" },
  });
  assert.match(denyReason(tampered), /untrusted/);

  fs.rmSync(snapshot);
  const missing = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "accelerate launch experiment.py" },
  });
  assert.match(denyReason(missing), /untrusted/);
});

test("manuscript mutation requires trusted claim freeze", (t) => {
  const fixture = createProject(t);
  advanceThroughMethod(fixture);
  const blocked = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Update File: main.tex\n" },
  });
  assert.match(denyReason(blocked), /claim_freeze/);

  approve(fixture, "claim_freeze");
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Update File: main.tex\n" },
  }), {});
});

test("retrospective claim freeze keeps one manuscript identity mutable", (t) => {
  const fixture = createProject(t);
  advanceThroughMethod(fixture);
  const manuscript = path.join(fixture.project, "submission", "legacy-paper.source");
  const paper = register(fixture, "paper.manuscript", { source: manuscript });
  assert.equal(paper.result.status, 0, paper.result.stderr);
  for (const role of [
    "experiment_results.claim_ledger",
    "experiment_results.provenance_gap_record",
  ]) {
    const result = register(fixture, role);
    assert.equal(result.result.status, 0, result.result.stderr);
  }
  const approved = ctl(
    fixture.project,
    "gate", "approve", "claim_freeze",
    "--reason", "Explicit retrospective import authorization.",
    "--supporting-evidence-id", "EVID-RETROSPECTIVE-IMPORT",
    "--decision-condition", "Stop if the declared provenance gap changes.",
    "--retrospective-revision-import",
  );
  assert.equal(approved.status, 0, approved.stderr);

  fs.writeFileSync(manuscript, "registered retrospective revision\n");
  const next = register(fixture, "paper.manuscript", {
    source: manuscript,
    artifactId: paper.identifier,
  });
  assert.equal(next.result.status, 0, next.result.stderr);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: `*** Update File: ${manuscript}\n` },
  }), {});
});

test("initial release snapshot permits the registered manuscript to enter revision", (t) => {
  const fixture = createProject(t);
  advanceThroughClaim(fixture);
  const custom = path.join(fixture.project, "submission", "camera-ready.source");
  const registered = register(fixture, "paper.manuscript", { source: custom });
  assert.equal(registered.result.status, 0, registered.result.stderr);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: `touch '${custom}'` },
  }), {});

  registerGateRequirements(fixture, "release", "initial_submission");
  const release = gate(
    fixture, "approve", "release", null, "initial_submission",
  );
  assert.equal(release.status, 0, release.stderr);
  assert.equal(
    runHook("SessionStart", fixture.project).hookSpecificOutput?.hookEventName,
    "SessionStart",
  );
  const revisionEdit = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: `touch '${custom}'` },
  });
  assert.deepEqual(revisionEdit, {});
});

test("approved revision release freezes its bound manuscript after an experiment checkpoint", (t) => {
  const fixture = createProject(t);
  advanceThroughClaim(fixture);
  const paperManuscript = path.join(fixture.project, "submission", "working-paper.tex");
  registerGateRequirements(fixture, "release", "initial_submission", {
    "paper.manuscript": paperManuscript,
  });
  assert.equal(
    gate(fixture, "approve", "release", null, "initial_submission").status,
    0,
  );

  const revisedManuscript = path.join(fixture.project, "revision", "revised-paper.tex");
  registerGateRequirements(fixture, "release", "revision_rebuttal", {
    "revision.revised_manuscript": revisedManuscript,
  });
  assert.equal(
    gate(fixture, "approve", "release", null, "revision_rebuttal").status,
    0,
  );
  const checkpoint = ctl(
    fixture.project,
    "checkpoint", "--stage", "experiment_results", "--summary", "Recheck evidence.",
  );
  assert.equal(checkpoint.status, 0, checkpoint.stderr);

  const output = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: `*** Update File: ${revisedManuscript}\n` },
  });
  assert.match(denyReason(output), /release\/revision_rebuttal|approved release binding/);
});

test("approved revision release freezes every nonmutable artifact binding", (t) => {
  const fixture = createProject(t);
  advanceThroughClaim(fixture);
  registerGateRequirements(fixture, "release", "initial_submission");
  assert.equal(
    gate(fixture, "approve", "release", null, "initial_submission").status,
    0,
  );

  const releaseChecklist = path.join(
    fixture.project, "revision", "release-checklist.yaml",
  );
  registerGateRequirements(fixture, "release", "revision_rebuttal", {
    "revision.release_checklist": releaseChecklist,
  });
  assert.equal(
    gate(fixture, "approve", "release", null, "revision_rebuttal").status,
    0,
  );
  const checkpoint = ctl(
    fixture.project,
    "checkpoint", "--stage", "experiment_results", "--summary", "Recheck evidence.",
  );
  assert.equal(checkpoint.status, 0, checkpoint.stderr);

  const output = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: `*** Update File: ${releaseChecklist}\n` },
  });
  assert.match(denyReason(output), /release\/revision_rebuttal|approved release binding/);
});

test("paper checkpoint keeps the initial release manuscript mutable after revision approval", (t) => {
  const fixture = createProject(t);
  advanceThroughClaim(fixture);
  const paperManuscript = path.join(fixture.project, "submission", "working-paper.tex");
  registerGateRequirements(fixture, "release", "initial_submission", {
    "paper.manuscript": paperManuscript,
  });
  assert.equal(
    gate(fixture, "approve", "release", null, "initial_submission").status,
    0,
  );

  const revisedManuscript = path.join(fixture.project, "revision", "revised-paper.tex");
  registerGateRequirements(fixture, "release", "revision_rebuttal", {
    "revision.revised_manuscript": revisedManuscript,
  });
  assert.equal(
    gate(fixture, "approve", "release", null, "revision_rebuttal").status,
    0,
  );
  const checkpoint = ctl(
    fixture.project,
    "checkpoint", "--stage", "paper", "--summary", "Revise the working manuscript.",
  );
  assert.equal(checkpoint.status, 0, checkpoint.stderr);

  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: `*** Update File: ${paperManuscript}\n` },
  }), {});
});

test("exact release target controls external send and freezes only the approved revision package", (t) => {
  const fixture = createProject(t);
  advanceThroughClaim(fixture);
  const beforeRelease = runHook("PreToolUse", fixture.project, {
    tool_name: "gmail:send",
    tool_input: { subject: "Manuscript submission", body: "Submit the paper." },
  });
  assert.match(denyReason(beforeRelease), /release/);

  registerGateRequirements(fixture, "release", "initial_submission");
  const approved = gate(
    fixture, "approve", "release", null, "initial_submission",
  );
  assert.equal(approved.status, 0, approved.stderr);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "gmail:send",
    tool_input: { subject: "Initial manuscript submission", body: "Submit the paper." },
  }), {});
  const wrongTarget = runHook("PreToolUse", fixture.project, {
    tool_name: "gmail:send",
    tool_input: { subject: "Reviewer response", body: "Send the rebuttal." },
  });
  assert.match(denyReason(wrongTarget), /release target|matching release target/);
  const genericRevisionBeforeApproval = runHook("PreToolUse", fixture.project, {
    tool_name: "gmail:send",
    tool_input: { subject: "Manuscript upload", body: "Upload the paper." },
  });
  assert.match(denyReason(genericRevisionBeforeApproval), /revision_rebuttal/);
  const localEdit = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Update File: main.tex\n" },
  });
  assert.deepEqual(localEdit, {});

  const frozenManuscript = path.join(
    fixture.project, "revision", "approved-revised-manuscript.tex",
  );
  registerGateRequirements(fixture, "release", "revision_rebuttal", {
    "revision.revised_manuscript": frozenManuscript,
  });
  const revisionApproved = gate(
    fixture, "approve", "release", null, "revision_rebuttal",
  );
  assert.equal(revisionApproved.status, 0, revisionApproved.stderr);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "gmail:send",
    tool_input: { subject: "Manuscript upload", body: "Upload the paper." },
  }), {});
  const frozenRevision = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: `*** Update File: ${frozenManuscript}\n` },
  });
  assert.match(denyReason(frozenRevision), /revision_rebuttal|exact target/);

  const reopened = gate(
    fixture, "reopen", "release", null, "revision_rebuttal",
  );
  assert.equal(reopened.status, 0, reopened.stderr);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: `*** Update File: ${frozenManuscript}\n` },
  }), {});
  const send = runHook("PreToolUse", fixture.project, {
    tool_name: "gmail:send",
    tool_input: { subject: "Revised manuscript", body: "Send reviewer response." },
  });
  assert.match(denyReason(send), /release/);
});

test("PostToolUse is quiet for unrelated calls and validates state-touching calls", (t) => {
  const fixture = createProject(t);
  assert.deepEqual(runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "git status --short" },
  }), {});
  const valid = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "python3 scripts/researchctl.py status" },
  });
  assert.match(valid.hookSpecificOutput.additionalContext, /found no issue/);

  const state = loadState(fixture);
  state.schema_version = "forged";
  writeState(fixture, state);
  const invalid = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "python3 scripts/researchctl.py status" },
  });
  assert.match(invalid.hookSpecificOutput.additionalContext, /schema_version/);
});

test("PostToolUse validates lifecycle decisions and activation history", (t) => {
  const fixture = createProject(t);
  register(fixture, "idea.idea_card", { artifactId: "POST-LIFECYCLE" });
  assert.equal(lifecycle(fixture, "terminate").status, 0);

  const terminalCheck = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "researchctl lifecycle terminate" },
  });
  assert.match(
    terminalCheck.hookSpecificOutput.additionalContext,
    /mechanical state.*found no issue/i,
  );

  assert.equal(lifecycle(fixture, "reopen").status, 0);
  assert.equal(
    ctl(fixture.project, "disable", "--reason", "Exercise activation audit.").status,
    0,
  );
  assert.equal(
    ctl(fixture.project, "enable", "--reason", "Resume activation audit.").status,
    0,
  );
  const activationCheck = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "researchctl enable" },
  });
  assert.match(
    activationCheck.hookSpecificOutput.additionalContext,
    /mechanical state.*found no issue/i,
  );

  const state = loadState(fixture);
  state.lifecycle.history[0].supporting_evidence_ids = [];
  state.activation_history[1].reason = "";
  writeState(fixture, state);
  const invalid = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "researchctl status" },
  }).hookSpecificOutput.additionalContext;
  assert.match(invalid, /supporting_evidence_ids/);
  assert.match(invalid, /activation_history\[1\]/);
});

test("PostToolUse accepts fresh-workspace termination without artifact refs", (t) => {
  const fixture = createProject(t);
  const terminated = lifecycle(fixture, "terminate");
  assert.equal(terminated.status, 0, terminated.stderr);

  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "researchctl:lifecycle",
    tool_input: { action: "terminate" },
  });
  assert.match(output.hookSpecificOutput.additionalContext, /found no issue/);
});

test("PostToolUse rejects empty lifecycle refs when artifacts already existed", (t) => {
  const fixture = createProject(t);
  const registered = register(fixture, "idea.idea_card", { artifactId: "LIFECYCLE-REF" });
  assert.equal(registered.result.status, 0, registered.result.stderr);
  const terminated = lifecycle(fixture, "terminate");
  assert.equal(terminated.status, 0, terminated.stderr);
  const state = loadState(fixture);
  state.lifecycle.history[0].artifact_refs = [];
  writeState(fixture, state);

  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "researchctl:lifecycle",
    tool_input: { action: "terminate" },
  });
  assert.match(
    output.hookSpecificOutput.additionalContext,
    /artifact_refs must be non-empty when registered artifacts existed at decision time/,
  );
});

test("policy loading rejects malformed authority and exercises environment fallbacks", (t) => {
  const fixture = createProject(t);
  assert.equal(runHook("SessionStart", fixture.project, {}, {
    env: { PLUGIN_ROOT: "", CODEX_PLUGIN_ROOT: ROOT },
  }).hookSpecificOutput?.hookEventName, "SessionStart");
  assert.equal(runHook("SessionStart", fixture.project, {}, {
    env: { PLUGIN_ROOT: "", CODEX_PLUGIN_ROOT: "", CLAUDE_PLUGIN_ROOT: ROOT },
  }).hookSpecificOutput?.hookEventName, "SessionStart");
  assert.equal(runHook("SessionStart", fixture.project, {}, {
    env: { PLUGIN_ROOT: "", CODEX_PLUGIN_ROOT: "", CLAUDE_PLUGIN_ROOT: "" },
  }).hookSpecificOutput?.hookEventName, "SessionStart");

  const plugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-policy-hook-"));
  t.after(() => fs.rmSync(plugin, { recursive: true, force: true }));
  const policyPath = path.join(plugin, "skills", "research", "references", "policy.yaml");
  const runtimePath = path.join(
    plugin, "skills", "research", "assets", "runtime-contract.json",
  );
  fs.mkdirSync(path.dirname(policyPath), { recursive: true });
  fs.mkdirSync(path.dirname(runtimePath), { recursive: true });
  const runAuthority = (mutatePolicy = () => {}, mutateRuntime = () => {}) => {
    const policy = structuredClone(POLICY);
    const runtime = structuredClone(RUNTIME_CONTRACT);
    mutatePolicy(policy);
    mutateRuntime(runtime);
    fs.writeFileSync(policyPath, `${JSON.stringify(policy)}\n`);
    fs.writeFileSync(runtimePath, `${JSON.stringify(runtime)}\n`);
    return runHook("SessionStart", fixture.project, {}, { env: { PLUGIN_ROOT: plugin } });
  };
  const runPolicy = (mutate) => runAuthority(mutate);
  const runRuntime = (mutate) => runAuthority(() => {}, mutate);

  const fallback = runPolicy(() => {});
  assert.equal(fallback.hookSpecificOutput?.hookEventName, "SessionStart");
  const fallbackReleaseCheck = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "scp paper.pdf conference:/submissions/" },
  }, { env: { PLUGIN_ROOT: plugin } });
  assert.match(denyReason(fallbackReleaseCheck), /release/);

  const invalidMutations = [
    (policy) => { policy.stages = []; },
    (policy) => { policy.gates = []; },
    (policy) => { policy.artifact_layout = []; },
    (policy) => { policy.artifact_layout.generated_root = "outside"; },
    (policy) => { policy.artifact_layout.stage_path_template = "wrong"; },
    (policy) => { policy.artifact_layout.instruction = "wrong"; },
    (policy) => {
      policy.workflow_graph.stage_order.push(policy.workflow_graph.stage_order[0]);
    },
    (policy) => { delete policy.workflow_graph.stage_exit_requirements.idea; },
    (policy) => { delete policy.stages[policy.workflow_graph.stage_order[0]]; },
    (policy) => { policy.artifact_layout.snapshot_root = "../snapshots"; },
    (policy) => { policy.artifact_layout.snapshot_stage_path_template = "wrong"; },
    (policy) => { policy.artifact_layout.snapshot_root = policy.artifact_layout.generated_root; },
    (policy) => { policy.artifact_role_cardinality_default = "many"; },
    (policy) => { delete policy.gates.release.approval_targets; },
    (policy) => {
      policy.workflow_graph.stage_exit_requirements.revision.target = "unknown";
    },
    (policy) => { delete policy.gates.claim_freeze.approval_modes.normal; },
    (policy) => {
      policy.gates.claim_freeze.approval_modes.provisional = {
        required_artifact_roles: ["experiment_results.claim_ledger"],
      };
    },
    (policy) => {
      policy.gates.claim_freeze.default_approval_mode = "retrospective_revision_import";
    },
    (policy) => {
      delete policy.gates.claim_freeze.approval_modes.retrospective_revision_import
        .required_artifact_roles;
    },
    (policy) => {
      const claim = policy.gates.claim_freeze;
      claim.required_artifact_roles = claim.approval_modes.normal.required_artifact_roles;
      delete claim.approval_modes;
      delete claim.default_approval_mode;
    },
  ];
  for (const mutate of invalidMutations) assert.deepEqual(runPolicy(mutate), {});

  const invalidRuntimeMutations = [
    (runtime) => { runtime.extra = true; },
    (runtime) => { runtime.contract_version = "unknown"; },
    (runtime) => { runtime.state = []; },
    (runtime) => { runtime.state.required_fields = []; },
    (runtime) => { runtime.lifecycle.record_fields = ["status", "latest_decision_id"]; },
    (runtime) => { runtime.gate.target_container_fields = ["rounds"]; },
    (runtime) => { runtime.decision.required_fields = ["decision_id"]; },
    (runtime) => { runtime.activation.event_fields = ["action"]; },
    (runtime) => { runtime.gate.cascade_fields = ["upstream_decision_id"]; },
    (runtime) => { runtime.gate.selection_fields = ["selected_id"]; },
    (runtime) => { runtime.artifact.entry_fields = ["current_revision"]; },
    (runtime) => { runtime.artifact.revision_fields = ["revision"]; },
    (runtime) => { runtime.checkpoint.fields = ["summary"]; },
    (runtime) => { runtime.stage_transition.fields = ["from_stage", "to_stage"]; },
    (runtime) => { runtime.gate.statuses = ["pending", "approved", "approved"]; },
    (runtime) => { runtime.gate.actions = ["approve"]; },
    (runtime) => { runtime.gate.decision_required_fields = ["decision_id"]; },
    (runtime) => { runtime.gate.record_fields.push("targets"); },
    (runtime) => { runtime.stage_transition.trigger_prefixes = ["checkpoint"]; },
  ];
  for (const mutate of invalidRuntimeMutations) {
    assert.deepEqual(runRuntime(mutate), {});
  }

  runPolicy((policy) => {
    policy.workflow_graph.stage_exit_requirements.revision.target = "unknown";
  });

  // An enabled project with a malformed canonical policy must not silently
  // authorize protected actions, even though context-only events stay quiet.
  const invalidPolicyLaunch = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "torchrun train.py" },
  }, { env: { PLUGIN_ROOT: plugin } });
  assert.match(denyReason(invalidPolicyLaunch), /policy or runtime contract is missing or invalid/);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "python3 scripts/researchctl.py doctor" },
  }, { env: { PLUGIN_ROOT: plugin } }), {});

  fs.writeFileSync(policyPath, '{"stage_order": ["idea"], "stage_order": ["idea"]}\n');
  assert.deepEqual(runHook("SessionStart", fixture.project, {}, {
    env: { PLUGIN_ROOT: plugin },
  }), {});
});

test("runtime loading accepts canonical field-list evolution without a JS mirror", (t) => {
  const fixture = createProject(t);
  const plugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-runtime-hook-"));
  t.after(() => fs.rmSync(plugin, { recursive: true, force: true }));
  const policyPath = path.join(plugin, "skills", "research", "references", "policy.yaml");
  const runtimePath = path.join(
    plugin, "skills", "research", "assets", "runtime-contract.json",
  );
  fs.mkdirSync(path.dirname(policyPath), { recursive: true });
  fs.mkdirSync(path.dirname(runtimePath), { recursive: true });
  const runtime = structuredClone(RUNTIME_CONTRACT);
  runtime.gate.decision_optional_fields.push("audit_note");
  runtime.lifecycle.decision_optional_fields.push("review_context");
  fs.writeFileSync(policyPath, `${JSON.stringify(POLICY)}\n`);
  fs.writeFileSync(runtimePath, `${JSON.stringify(runtime)}\n`);

  assert.equal(runHook("SessionStart", fixture.project, {}, {
    env: { PLUGIN_ROOT: plugin },
  }).hookSpecificOutput?.hookEventName, "SessionStart");
});

test("policy loading rejects malformed semantic governance sections", (t) => {
  const fixture = createProject(t);
  const plugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-governance-hook-"));
  t.after(() => fs.rmSync(plugin, { recursive: true, force: true }));
  const policyPath = path.join(plugin, "skills", "research", "references", "policy.yaml");
  const runtimePath = path.join(
    plugin, "skills", "research", "assets", "runtime-contract.json",
  );
  fs.mkdirSync(path.dirname(policyPath), { recursive: true });
  fs.mkdirSync(path.dirname(runtimePath), { recursive: true });
  fs.writeFileSync(runtimePath, `${JSON.stringify(RUNTIME_CONTRACT)}\n`);
  const rejects = (mutate) => {
    const policy = structuredClone(POLICY);
    mutate(policy);
    fs.writeFileSync(policyPath, `${JSON.stringify(policy)}\n`);
    assert.deepEqual(runHook("SessionStart", fixture.project, {}, {
      env: { PLUGIN_ROOT: plugin },
    }), {});
  };

  for (const mutate of [
    (policy) => { delete policy.workspace_lifecycle.terminal_access; },
    (policy) => { policy.workspace_lifecycle.scope = ""; },
    (policy) => { policy.authority_boundary = []; },
    (policy) => { policy.review_language.instruction = ""; },
    (policy) => { policy.global_prohibited_actions = []; },
    (policy) => { policy.semantic_audit = "ignored prose"; },
    (policy) => { delete policy.stages.idea.required_inputs; },
    (policy) => { policy.stages.idea.unchecked = ["ignored"]; },
    (policy) => { policy.gates.idea_freeze.shadow_requirement = ["ignored"]; },
    (policy) => { policy.artifact_layout.shadow_root = ".research/shadow"; },
  ]) rejects(mutate);
});

test("policy loading rejects retrospective CLI flags reserved by Gate", (t) => {
  const fixture = createProject(t);
  const plugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-reserved-flag-hook-"));
  t.after(() => fs.rmSync(plugin, { recursive: true, force: true }));
  const policyPath = path.join(plugin, "skills", "research", "references", "policy.yaml");
  const runtimePath = path.join(
    plugin, "skills", "research", "assets", "runtime-contract.json",
  );
  fs.mkdirSync(path.dirname(policyPath), { recursive: true });
  fs.mkdirSync(path.dirname(runtimePath), { recursive: true });
  fs.writeFileSync(runtimePath, `${JSON.stringify(RUNTIME_CONTRACT)}\n`);

  for (const reserved of [
    "--help",
    "--reason",
    "--supporting-evidence-id",
    "--opposing-evidence-id",
    "--unresolved-risk",
    "--decision-condition",
    "--target",
    "--selected-id",
    "--approval-mode",
  ]) {
    const policy = structuredClone(POLICY);
    policy.gates.claim_freeze.approval_modes.retrospective_revision_import.cli_flag
      = reserved;
    fs.writeFileSync(policyPath, `${JSON.stringify(policy)}\n`);
    assert.deepEqual(runHook("SessionStart", fixture.project, {}, {
      env: { PLUGIN_ROOT: plugin },
    }), {}, reserved);
  }
});

test("Hook derives retrospective approval mode semantics from canonical policy", (t) => {
  const fixture = createProject(t);
  advanceThroughMethod(fixture);
  assert.equal(register(fixture, "paper.manuscript").result.status, 0);
  for (const role of [
    "experiment_results.claim_ledger",
    "experiment_results.provenance_gap_record",
  ]) {
    const registered = register(fixture, role);
    assert.equal(registered.result.status, 0, registered.result.stderr);
  }
  const approved = ctl(
    fixture.project,
    "gate", "approve", "claim_freeze",
    "--reason", "Explicit retrospective import authorization.",
    "--supporting-evidence-id", "EVID-RETROSPECTIVE-IMPORT",
    "--decision-condition", "Stop if the declared provenance gap changes.",
    "--retrospective-revision-import",
  );
  assert.equal(approved.status, 0, approved.stderr);

  const state = loadState(fixture);
  state.gates.claim_freeze.history.at(-1).approval_mode = "rescue_import";
  writeState(fixture, state);
  const policy = structuredClone(POLICY);
  const claim = policy.gates.claim_freeze;
  claim.approval_modes = {
    standard: claim.approval_modes.normal,
    rescue_import: claim.approval_modes.retrospective_revision_import,
  };
  claim.default_approval_mode = "standard";

  const plugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-mode-hook-"));
  t.after(() => fs.rmSync(plugin, { recursive: true, force: true }));
  const policyPath = path.join(plugin, "skills", "research", "references", "policy.yaml");
  const runtimePath = path.join(
    plugin, "skills", "research", "assets", "runtime-contract.json",
  );
  fs.mkdirSync(path.dirname(policyPath), { recursive: true });
  fs.mkdirSync(path.dirname(runtimePath), { recursive: true });
  fs.writeFileSync(policyPath, `${JSON.stringify(policy)}\n`);
  fs.writeFileSync(runtimePath, `${JSON.stringify(RUNTIME_CONTRACT)}\n`);

  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "researchctl:status",
    tool_input: {},
  }, { env: { PLUGIN_ROOT: plugin } });
  assert.match(output.hookSpecificOutput.additionalContext, /found no issue/);
});

test("Hook accepts a canonical release Gate with one ordered target", (t) => {
  const fixture = createProject(t);
  const policy = structuredClone(POLICY);
  policy.workflow_graph.stage_exit_requirements.revision = null;
  delete policy.gates.release.approval_targets.revision_rebuttal;

  const plugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-release-hook-"));
  t.after(() => fs.rmSync(plugin, { recursive: true, force: true }));
  const policyPath = path.join(plugin, "skills", "research", "references", "policy.yaml");
  const runtimePath = path.join(
    plugin, "skills", "research", "assets", "runtime-contract.json",
  );
  fs.mkdirSync(path.dirname(policyPath), { recursive: true });
  fs.mkdirSync(path.dirname(runtimePath), { recursive: true });
  fs.writeFileSync(policyPath, `${JSON.stringify(policy)}\n`);
  fs.writeFileSync(runtimePath, `${JSON.stringify(RUNTIME_CONTRACT)}\n`);

  assert.equal(runHook("SessionStart", fixture.project, {}, {
    env: { PLUGIN_ROOT: plugin },
  }).hookSpecificOutput?.hookEventName, "SessionStart");
});

test("Hook uses the first ordered release target for an explicit initial submission", (t) => {
  const fixture = createProject(t);
  advanceThroughClaim(fixture);
  registerGateRequirements(fixture, "release", "initial_submission");
  assert.equal(
    gate(fixture, "approve", "release", null, "initial_submission").status,
    0,
  );

  const policy = structuredClone(POLICY);
  policy.workflow_graph.stage_exit_requirements.revision = null;
  delete policy.gates.release.approval_targets.revision_rebuttal;
  const state = loadState(fixture);
  delete state.gates.release.targets.revision_rebuttal;
  writeState(fixture, state);

  const plugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-one-release-hook-"));
  t.after(() => fs.rmSync(plugin, { recursive: true, force: true }));
  const policyPath = path.join(plugin, "skills", "research", "references", "policy.yaml");
  const runtimePath = path.join(
    plugin, "skills", "research", "assets", "runtime-contract.json",
  );
  fs.mkdirSync(path.dirname(policyPath), { recursive: true });
  fs.mkdirSync(path.dirname(runtimePath), { recursive: true });
  fs.writeFileSync(policyPath, `${JSON.stringify(policy)}\n`);
  fs.writeFileSync(runtimePath, `${JSON.stringify(RUNTIME_CONTRACT)}\n`);

  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "gmail:send",
    tool_input: { subject: "Initial manuscript submission", body: "Submit the paper." },
  }, { env: { PLUGIN_ROOT: plugin } }), {});
});

test("reviewer response uses the downstream release target after a paper checkpoint", (t) => {
  const fixture = createProject(t);
  advanceThroughClaim(fixture);
  registerGateRequirements(fixture, "release", "initial_submission");
  assert.equal(
    gate(fixture, "approve", "release", null, "initial_submission").status,
    0,
  );
  registerGateRequirements(fixture, "release", "revision_rebuttal");
  assert.equal(
    gate(fixture, "approve", "release", null, "revision_rebuttal").status,
    0,
  );
  const checkpoint = ctl(
    fixture.project,
    "checkpoint", "--stage", "paper", "--summary", "Revise the working paper.",
  );
  assert.equal(checkpoint.status, 0, checkpoint.stderr);

  const policy = structuredClone(POLICY);
  const release = policy.gates.release;
  release.approval_targets = {
    first_round: release.approval_targets.initial_submission,
    review_round: release.approval_targets.revision_rebuttal,
  };
  policy.workflow_graph.stage_exit_requirements.paper.target = "first_round";
  policy.workflow_graph.stage_exit_requirements.revision.target = "review_round";

  const state = loadState(fixture);
  const approvedReviewRound = structuredClone(
    state.gates.release.targets.revision_rebuttal,
  );
  state.gates.release.targets = {
    first_round: state.gates.release.targets.initial_submission,
    review_round: { status: "pending", latest_decision_id: null, history: [] },
  };
  writeState(fixture, state);

  const plugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-review-target-hook-"));
  t.after(() => fs.rmSync(plugin, { recursive: true, force: true }));
  const policyPath = path.join(plugin, "skills", "research", "references", "policy.yaml");
  const runtimePath = path.join(
    plugin, "skills", "research", "assets", "runtime-contract.json",
  );
  fs.mkdirSync(path.dirname(policyPath), { recursive: true });
  fs.mkdirSync(path.dirname(runtimePath), { recursive: true });
  fs.writeFileSync(policyPath, `${JSON.stringify(policy)}\n`);
  fs.writeFileSync(runtimePath, `${JSON.stringify(RUNTIME_CONTRACT)}\n`);
  const reviewerResponse = () => runHook("PreToolUse", fixture.project, {
    tool_name: "gmail:send",
    tool_input: { subject: "Reviewer response", body: "Send the response document." },
  }, { env: { PLUGIN_ROOT: plugin } });

  assert.match(denyReason(reviewerResponse()), /release\/review_round/);
  const approvedState = loadState(fixture);
  approvedState.gates.release.targets.review_round = approvedReviewRound;
  writeState(fixture, approvedState);
  assert.deepEqual(reviewerResponse(), {});
});

test("Hook derives the initial release target when validating completion", (t) => {
  const fixture = createProject(t);
  advanceThroughClaim(fixture);
  registerGateRequirements(fixture, "release", "initial_submission");
  assert.equal(
    gate(fixture, "approve", "release", null, "initial_submission").status,
    0,
  );
  const completed = lifecycle(fixture, "complete");
  assert.equal(completed.status, 0, completed.stderr);

  const policy = structuredClone(POLICY);
  const release = policy.gates.release;
  release.approval_targets = {
    first_delivery: release.approval_targets.initial_submission,
    response_delivery: release.approval_targets.revision_rebuttal,
  };
  policy.gates.publication = release;
  delete policy.gates.release;
  policy.workflow_graph.stage_exit_requirements.paper = {
    gate: "publication", target: "first_delivery",
  };
  policy.workflow_graph.stage_exit_requirements.revision = {
    gate: "publication", target: "response_delivery",
  };

  const state = loadState(fixture);
  const releaseRecord = state.gates.release;
  releaseRecord.targets = {
    first_delivery: releaseRecord.targets.initial_submission,
    response_delivery: releaseRecord.targets.revision_rebuttal,
  };
  state.gates.publication = releaseRecord;
  delete state.gates.release;
  for (const decision of state.lifecycle.history) {
    if (decision.gate_ref?.gate === "release") {
      decision.gate_ref.gate = "publication";
      if (decision.gate_ref.target === "initial_submission") {
        decision.gate_ref.target = "first_delivery";
      } else if (decision.gate_ref.target === "revision_rebuttal") {
        decision.gate_ref.target = "response_delivery";
      }
    }
  }
  writeState(fixture, state);

  const plugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-renamed-release-hook-"));
  t.after(() => fs.rmSync(plugin, { recursive: true, force: true }));
  const policyPath = path.join(plugin, "skills", "research", "references", "policy.yaml");
  const runtimePath = path.join(
    plugin, "skills", "research", "assets", "runtime-contract.json",
  );
  fs.mkdirSync(path.dirname(policyPath), { recursive: true });
  fs.mkdirSync(path.dirname(runtimePath), { recursive: true });
  fs.writeFileSync(policyPath, `${JSON.stringify(policy)}\n`);
  fs.writeFileSync(runtimePath, `${JSON.stringify(RUNTIME_CONTRACT)}\n`);

  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "researchctl:status",
    tool_input: {},
  }, { env: { PLUGIN_ROOT: plugin } });
  assert.match(output.hookSpecificOutput.additionalContext, /found no issue/);
});

test("upstream Gate reopen records and validates the v2 downstream cascade", (t) => {
  const fixture = createProject(t);
  advanceThroughClaim(fixture);

  const reopened = gate(fixture, "reopen", "idea_freeze");
  assert.equal(reopened.status, 0, reopened.stderr);
  const state = loadState(fixture);
  assert.equal(state.gates.idea_freeze.status, "reopened");
  assert.equal(state.gates.method_experiment_approval.status, "reopened");
  assert.equal(state.gates.claim_freeze.status, "reopened");

  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "researchctl:status",
    tool_input: {},
  });
  assert.match(output.hookSpecificOutput.additionalContext, /found no issue/);

  const forged = loadState(fixture);
  const cascadedDecision = forged.gates.claim_freeze.history.at(-1);
  assert.deepEqual(cascadedDecision.cascade.upstream_gate_ref, { gate: "idea_freeze" });
  cascadedDecision.cascade.upstream_gate_ref = { gate: "method_experiment_approval" };
  writeState(fixture, forged);
  const rejected = runHook("PostToolUse", fixture.project, {
    tool_name: "researchctl:status",
    tool_input: {},
  });
  assert.match(rejected.hookSpecificOutput.additionalContext, /provenance does not match/);
});

test("PostToolUse accepts lossless return through a still-active earlier Gate approval", (t) => {
  const fixture = createProject(t);
  approve(fixture, "idea_freeze");
  assert.equal(ctl(
    fixture.project,
    "checkpoint", "--stage", "literature", "--summary", "Revisit literature.",
  ).status, 0);
  const returned = ctl(
    fixture.project,
    "checkpoint", "--stage", "method", "--summary", "Resume unchanged method work.",
  );
  assert.equal(returned.status, 0, returned.stderr);

  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "researchctl:checkpoint",
    tool_input: { stage: "method" },
  });
  assert.match(output.hookSpecificOutput.additionalContext, /found no issue/);
});

test("PostToolUse rejects lossless return through a reopened stale approval", (t) => {
  const fixture = createProject(t);
  approve(fixture, "idea_freeze");
  assert.equal(ctl(
    fixture.project,
    "checkpoint", "--stage", "literature", "--summary", "Revisit literature.",
  ).status, 0);
  assert.equal(gate(fixture, "reopen", "idea_freeze").status, 0);

  const state = loadState(fixture);
  const record = state.gates.idea_freeze;
  const approval = record.history.find((decision) => decision.action === "approve");
  const reopened = record.history.at(-1);
  const forgedTimestamp = new Date(
    Date.parse(reopened.decided_at) + 1000,
  ).toISOString();
  state.stage_history.push({
    from_stage: "idea",
    to_stage: "method",
    trigger: `gate-approve:${approval.decision_id}`,
    timestamp: forgedTimestamp,
  });
  state.current_stage = "method";
  state.updated_at = forgedTimestamp;
  writeState(fixture, state);

  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "researchctl:checkpoint",
    tool_input: { stage: "method" },
  });
  assert.match(
    output.hookSpecificOutput.additionalContext,
    /active Gate approval at the transition timestamp/,
  );
});

test("PostToolUse reports independent v2 envelope registry Gate and timeline failures", (t) => {
  const fixture = createProject(t);
  const post = (state) => {
    writeState(fixture, state);
    const output = runHook("PostToolUse", fixture.project, {
      tool_name: "researchctl:status",
      tool_input: {},
    });
    assert.equal(output.hookSpecificOutput?.hookEventName, "PostToolUse");
    assert.match(output.hookSpecificOutput.additionalContext, /Detected mechanical/);
    return output.hookSpecificOutput.additionalContext;
  };
  const clean = loadState(fixture);

  const envelope = structuredClone(clean);
  envelope.extra = true;
  envelope.schema_version = "wrong";
  envelope.workflow_version = "wrong";
  envelope.project_id = "";
  envelope.project_name = 7;
  envelope.current_stage = "unknown";
  envelope.created_at = "invalid";
  envelope.updated_at = "2020-01-01T00:00:00Z";
  envelope.artifacts = [];
  envelope.gates = [];
  envelope.last_checkpoint = {};
  envelope.stage_history = {};
  const envelopeContext = post(envelope);
  assert.match(envelopeContext, /unknown state fields|unknown current_stage|artifacts must be an object/);

  const revision = {
    revision: 0,
    source_path: "",
    snapshot_path: "",
    content_hash: "not-a-hash",
    size_bytes: -1,
    registered_at: "invalid",
  };
  const later = {
    revision: 9,
    source_path: "work/source.md",
    snapshot_path: ".research/snapshots/idea/shared.snapshot",
    content_hash: `sha256:${"0".repeat(64)}`,
    size_bytes: 0,
    registered_at: clean.created_at,
  };
  const registry = structuredClone(clean);
  registry.artifacts = {
    unknown: {},
    idea: {
      "Bad-Role": {},
      bad_bucket: [],
      empty_bucket: {},
      broken_entry: { BROKEN: null },
      empty_revisions: { EMPTY: { current_revision: 0, revisions: [] } },
      bad_identifier: { "!bad": { current_revision: 1, revisions: [later] } },
      idea_card: { GOOD: { current_revision: 5, revisions: [revision, later] } },
      duplicate_snapshot: {
        DUP: { current_revision: 1, revisions: [{ ...later, revision: 1 }] },
      },
    },
  };
  const registryContext = post(registry);
  assert.match(registryContext, /unknown stage|lower_snake_case|positive integer|content_hash/);

  const gates = structuredClone(clean);
  delete gates.gates.idea_freeze;
  gates.gates.unknown_gate = { status: "pending", latest_decision_id: null, history: [] };
  gates.gates.method_experiment_approval = {
    status: "invalid",
    latest_decision_id: "X",
    history: "invalid",
  };
  gates.gates.claim_freeze = {
    status: "approved",
    latest_decision_id: "X",
    history: [],
  };
  gates.gates.release = null;
  const gateContext = post(gates);
  assert.match(gateContext, /missing Gate|unknown Gate|invalid status|history must be an array/);

  const timeline = structuredClone(clean);
  timeline.current_stage = "method";
  timeline.last_checkpoint = { summary: "", timestamp: "invalid" };
  timeline.stage_history = [
    { from_stage: "unknown", to_stage: "unknown", trigger: "", timestamp: "invalid" },
    { from_stage: "idea", to_stage: "literature", trigger: "unsupported", timestamp: clean.created_at },
    { from_stage: "literature", to_stage: "method", trigger: "gate-approve:UNKNOWN", timestamp: clean.created_at },
  ];
  const timelineContext = post(timeline);
  assert.match(timelineContext, /stage_history|unsupported trigger|unknown Gate decision/);

  const exactCheckpoint = structuredClone(clean);
  exactCheckpoint.last_checkpoint = {
    summary: "resume",
    timestamp: clean.updated_at,
    extra: true,
  };
  const checkpointContext = post(exactCheckpoint);
  assert.match(checkpointContext, /last_checkpoint has unknown fields/);

  const exactTransition = structuredClone(clean);
  exactTransition.current_stage = "literature";
  exactTransition.stage_history = [{
    from_stage: "idea",
    to_stage: "literature",
    trigger: "checkpoint",
    timestamp: clean.updated_at,
    extra: true,
  }];
  const transitionContext = post(exactTransition);
  assert.match(transitionContext, /stage_history\[0\] has unknown fields/);
});

test("Stop ignores natural language and compares only explicit structured workflow fields", (t) => {
  const fixture = createProject(t);
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "Current stage: revision; release: approved. This is only prose.",
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    workflow_assertions: {
      current_stage: "idea",
      stage_exit_requirement: { gate: "idea_freeze" },
      gates: { idea_freeze: "pending" },
    },
  }), {});

  const contradiction = runHook("Stop", fixture.project, {
    workflow_assertions: {
      current_stage: "revision",
      gates: { "release/initial_submission": "approved" },
    },
  });
  assert.equal(contradiction.decision, "block");
  assert.match(contradiction.reason, /structured current_stage/);
  assert.match(contradiction.reason, /structured release/);
  assert.deepEqual(runHook("Stop", fixture.project, {
    stop_hook_active: true,
    workflow_assertions: { current_stage: "revision" },
  }), {});
});

test("Stop reports invalid state non-blockingly without parsing or replacing the answer", (t) => {
  const fixture = createProject(t);
  const state = loadState(fixture);
  state.gates.idea_freeze.status = "approved";
  writeState(fixture, state);
  const output = runHook("Stop", fixture.project, {
    last_assistant_message: "ordinary answer",
    workflow_assertions: { current_stage: "revision" },
  });
  assert.equal(output.continue, true);
  assert.match(output.systemMessage, /failed mechanical validation/);
  assert.match(output.systemMessage, /not parsed or replaced/);
  assert.equal(output.decision, undefined);
});

test("malformed envelopes, event disagreement, and oversized input fail closed to no-op", (t) => {
  const fixture = createProject(t);
  const malformed = command("node", [HOOK, "PreToolUse"], {
    cwd: fixture.project,
    input: "not-json",
    env: { ...process.env, PLUGIN_ROOT: ROOT },
  });
  assert.equal(malformed.stdout, "{}");
  assert.deepEqual(runHook("PreToolUse", fixture.project, {}, {
    input: hookInput("PostToolUse", fixture.project),
  }), {});
  const oversized = command("node", [HOOK, "SessionStart"], {
    cwd: fixture.project,
    input: JSON.stringify({
      hook_event_name: "SessionStart",
      cwd: fixture.project,
      padding: "x".repeat(8 * 1024 * 1024),
    }),
    env: { ...process.env, PLUGIN_ROOT: ROOT },
  });
  assert.equal(oversized.stdout, "{}");
});
