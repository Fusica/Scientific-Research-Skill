#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const pluginRoot = path.join(__dirname, "..");
const hook = path.join(pluginRoot, "hooks", "research-workflow-hook.js");
const policy = JSON.parse(
  fs.readFileSync(
    path.join(pluginRoot, "skills", "research", "references", "policy.yaml"),
    "utf8",
  ),
);

function write(candidate, content) {
  fs.mkdirSync(path.dirname(candidate), { recursive: true });
  fs.writeFileSync(candidate, content, "utf8");
}

function pendingGate() {
  return { status: "pending", latest_decision_id: null, history: [] };
}

function approvedGate(id, releaseTarget = null) {
  const decision = {
    decision_id: id,
    action: "approve",
    previous_status: "pending",
    new_status: "approved",
    reason: "Approved by the human owner for this isolated Hook test.",
    actor: "test-owner",
    decided_at: "2026-07-13T08:00:00Z",
    artifact_refs: [],
  };
  if (releaseTarget !== null) decision.release_target = releaseTarget;
  return {
    status: "approved",
    latest_decision_id: id,
    history: [decision],
  };
}

function reopenedGate(approveId, reopenId) {
  const record = approvedGate(approveId, "initial_submission");
  record.status = "reopened";
  record.latest_decision_id = reopenId;
  record.history.push({
    decision_id: reopenId,
    action: "reopen",
    previous_status: "approved",
    new_status: "reopened",
    reason: "The human owner reopened the release for a verified revision.",
    actor: "test-owner",
    decided_at: "2026-07-13T09:00:00Z",
    artifact_refs: [],
    release_target: "initial_submission",
  });
  return record;
}

function makeState(overrides = {}) {
  const state = {
    schema_version: policy.schema_version,
    workflow_version: policy.workflow_version,
    enabled: true,
    project_id: "PROJECT-HOOK-TEST",
    project_name: "hook-test-project",
    current_stage: "idea",
    gates: Object.fromEntries(policy.gate_order.map((gate) => [gate, pendingGate()])),
    artifacts: {},
    last_checkpoint: null,
    stage_history: [],
    created_at: "2026-07-13T08:00:00Z",
    updated_at: "2026-07-13T08:00:00Z",
  };
  return { ...state, ...overrides };
}

function createProject(options = {}) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "research-hook-"));
  const project = path.join(temporary, "project");
  fs.mkdirSync(path.join(project, ".git"), { recursive: true });
  if (options.rawState !== undefined) {
    write(path.join(project, ".research", "state.json"), options.rawState);
  } else if (options.withState !== false) {
    write(
      path.join(project, ".research", "state.json"),
      `${JSON.stringify(options.state || makeState(), null, 2)}\n`,
    );
  }
  if (options.withMemory !== false && options.withState !== false) {
    write(
      path.join(project, ".research", "memory.md"),
      options.memory || "# Research Memory\n\n## Research kernel\n\n- Problem: Hook contract verification.\n",
    );
  }
  return { temporary, project };
}

function officialInput(event, cwd, overrides = {}) {
  const common = {
    cwd,
    hook_event_name: event,
    model: "gpt-test",
    permission_mode: "default",
    session_id: "session-test",
    transcript_path: null,
  };
  if (event === "SessionStart") Object.assign(common, { source: "startup" });
  if (event === "UserPromptSubmit") {
    Object.assign(common, { prompt: "Continue the research task.", turn_id: "turn-test" });
  }
  if (event === "PreToolUse") {
    Object.assign(common, {
      turn_id: "turn-test",
      tool_name: "Bash",
      tool_input: { command: "pwd" },
      tool_use_id: "tool-test",
    });
  }
  if (event === "PostToolUse") {
    Object.assign(common, {
      turn_id: "turn-test",
      tool_name: "Bash",
      tool_input: { command: "pwd" },
      tool_response: { stdout: cwd, exit_code: 0 },
      tool_use_id: "tool-test",
    });
  }
  if (event === "Stop") {
    Object.assign(common, {
      turn_id: "turn-test",
      last_assistant_message: "Short handoff.",
      stop_hook_active: false,
    });
  }
  return { ...common, ...overrides };
}

function runHook(event, cwd, overrides = {}, options = {}) {
  const environment = { ...process.env };
  delete environment.PLUGIN_ROOT;
  delete environment.CODEX_PLUGIN_ROOT;
  delete environment.CLAUDE_PLUGIN_ROOT;
  const rootVariable = options.rootVariable || "PLUGIN_ROOT";
  environment[rootVariable] = pluginRoot;
  Object.assign(environment, options.env || {});
  const input = options.input || officialInput(event, cwd, overrides);
  const result = spawnSync(process.execPath, [hook, event], {
    cwd,
    input: JSON.stringify(input),
    encoding: "utf8",
    env: environment,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "", "Hook must not use stderr for ordinary outcomes");
  assert.notEqual(result.stdout.trim(), "", "Hook must emit one JSON object");
  return JSON.parse(result.stdout);
}

function runRaw(event, cwd, raw) {
  const result = spawnSync(process.execPath, [hook, event], {
    cwd,
    input: raw,
    encoding: "utf8",
    env: { ...process.env, PLUGIN_ROOT: pluginRoot },
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

function snapshotFiles(directory) {
  const result = new Map();
  function walk(current) {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const candidate = path.join(current, entry.name);
      if (entry.isDirectory()) walk(candidate);
      else result.set(path.relative(directory, candidate), fs.readFileSync(candidate));
    }
  }
  walk(directory);
  return result;
}

function cleanup(temporary) {
  fs.rmSync(temporary, { recursive: true, force: true });
}

test("Hook config registers five command-only handlers through PLUGIN_ROOT", () => {
  const document = JSON.parse(
    fs.readFileSync(path.join(pluginRoot, "hooks", "hooks.json"), "utf8"),
  );
  const events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"];
  assert.deepEqual(Object.keys(document), ["hooks"]);
  assert.deepEqual(Object.keys(document.hooks).sort(), [...events].sort());
  for (const event of events) {
    const groups = document.hooks[event];
    assert.equal(groups.length, 1, event);
    assert.equal(groups[0].hooks.length, 1, event);
    const handler = groups[0].hooks[0];
    assert.deepEqual(Object.keys(handler).sort(), [
      "command",
      "commandWindows",
      "statusMessage",
      "timeout",
      "type",
    ]);
    assert.equal(handler.type, "command");
    assert.equal(handler.timeout, 5);
    assert.match(handler.command, /\$\{PLUGIN_ROOT\}\/hooks\/research-workflow-hook\.js/);
    assert.match(handler.command, new RegExp(`${event}$`));
    assert.match(handler.commandWindows, /\$env:PLUGIN_ROOT\\hooks\\research-workflow-hook\.js/);
    assert.equal(handler.command.includes("CLAUDE_PLUGIN_ROOT"), false);
  }
  assert.equal(document.hooks.PreToolUse[0].matcher, ".*");
  assert.equal(document.hooks.PostToolUse[0].matcher, ".*");
});

test("ordinary, missing, malformed, and disabled projects are strict no-ops", () => {
  const ordinary = createProject({ withState: false, withMemory: false });
  const malformed = createProject({ rawState: "{not-json\n", withMemory: false });
  const disabled = createProject({ state: makeState({ enabled: false }) });
  const events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"];

  for (const event of events) {
    assert.deepEqual(runHook(event, ordinary.project), {}, `ordinary ${event}`);
    assert.deepEqual(runHook(event, malformed.project), {}, `malformed ${event}`);
    assert.deepEqual(runHook(event, disabled.project), {}, `disabled ${event}`);
  }
  assert.deepEqual(runRaw("SessionStart", ordinary.project, "not-json"), {});
  cleanup(ordinary.temporary);
  cleanup(malformed.temporary);
  cleanup(disabled.temporary);
});

test("all inactive Hook events are read-only and create no plugin data", () => {
  const fixture = createProject({ withState: false, withMemory: false });
  write(path.join(fixture.project, "README.md"), "ordinary repository\n");
  const before = snapshotFiles(fixture.project);
  for (const event of ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]) {
    runHook(event, fixture.project);
  }
  assert.deepEqual(snapshotFiles(fixture.project), before);
  cleanup(fixture.temporary);
});

test("SessionStart finds a parent project and injects only the minimal activation boundary", () => {
  const existingArtifact = "evidence/idea-card.md";
  const artifactContent = "# Idea card\n";
  const artifactHash = `sha256:${crypto.createHash("sha256").update(artifactContent).digest("hex")}`;
  const memory = [
    "# Research Memory",
    "",
    "## Research kernel",
    "- Problem: bounded context injection.",
    "- Current hypothesis: memory is navigation only.",
    "",
    "## Verified facts",
    ...Array.from({ length: 500 }, (_, index) => `- FACT-${index}: ${"x".repeat(24)}`),
    "",
    "## Next checkpoint",
    "- Next smallest action: verify the tail survives bounded injection.",
  ].join("\n");
  const state = makeState({
    artifacts: {
      idea: {
        idea_card: {
          "IDEA-CARD-001": {
            path: existingArtifact,
            artifact_id: "IDEA-CARD-001",
            version: "1",
            content_hash: artifactHash,
            status: "approval-ready",
          },
        },
      },
    },
    last_checkpoint: { summary: "Inspect exact Hook JSON", timestamp: "2026-07-13T08:10:00Z" },
  });
  const fixture = createProject({ state, memory });
  write(path.join(fixture.project, existingArtifact), artifactContent);
  const nested = path.join(fixture.project, "src", "deep");
  fs.mkdirSync(nested, { recursive: true });

  const before = snapshotFiles(fixture.project);
  const output = runHook("SessionStart", nested);
  assert.deepEqual(Object.keys(output), ["hookSpecificOutput"]);
  assert.equal(output.hookSpecificOutput.hookEventName, "SessionStart");
  const context = output.hookSpecificOutput.additionalContext;
  assert.ok(context.length <= 800, `context length was ${context.length}`);
  assert.match(context, /Project: hook-test-project/);
  assert.match(context, /Project ID: PROJECT-HOOK-TEST/);
  assert.match(context, /Current stage: idea/);
  assert.match(context, /Gate to exit: idea_freeze \(pending\)/);
  assert.match(context, /state\.json is the project state authority/);
  assert.match(context, /Mechanical Hook checks remain active/);
  assert.doesNotMatch(context, /evidence\/idea-card\.md/);
  assert.doesNotMatch(context, /IDEA-CARD-001@1/);
  assert.doesNotMatch(context, /Inspect exact Hook JSON/);
  assert.doesNotMatch(context, /verify the tail survives bounded injection/);
  assert.deepEqual(snapshotFiles(fixture.project), before, "SessionStart must be read-only");
  cleanup(fixture.temporary);
});

test("a nested Git repository does not inherit an unrelated parent research state", () => {
  const fixture = createProject();
  const nestedRepository = path.join(fixture.project, "external", "ordinary-repo");
  fs.mkdirSync(path.join(nestedRepository, ".git"), { recursive: true });
  assert.deepEqual(runHook("SessionStart", nestedRepository), {});
  cleanup(fixture.temporary);
});

test("SessionStart resolves CODEX_PLUGIN_ROOT and CLAUDE_PLUGIN_ROOT fallbacks", () => {
  const fixture = createProject();
  for (const rootVariable of ["CODEX_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"]) {
    const output = runHook("SessionStart", fixture.project, {}, { rootVariable });
    assert.equal(output.hookSpecificOutput.hookEventName, "SessionStart");
  }
  cleanup(fixture.temporary);
});

test("UserPromptSubmit injects a compact current-stage boundary for research work", () => {
  const gates = Object.fromEntries(policy.gate_order.map((gate) => [gate, pendingGate()]));
  gates.idea_freeze = approvedGate("DEC-IDEA");
  gates.method_experiment_approval = approvedGate("DEC-METHOD");
  const fixture = createProject({
    state: makeState({ current_stage: "experiment_results", gates }),
  });
  const output = runHook("UserPromptSubmit", fixture.project, {
    prompt: "Analyze registered experiment outputs and decide which claims survive.",
  });
  assert.deepEqual(Object.keys(output), ["hookSpecificOutput"]);
  assert.equal(output.hookSpecificOutput.hookEventName, "UserPromptSubmit");
  const context = output.hookSpecificOutput.additionalContext;
  assert.ok(context.length <= 1200);
  assert.match(context, /Current stage: experiment_results/);
  assert.match(context, /PROMPT RELEVANT/);
  assert.match(context, /Current-stage prohibited actions/);
  assert.match(context, /change metrics or exclusions after seeing results/);
  assert.match(context, /claim_freeze \(pending\)/);
  assert.match(context, /Use the \$research Skill/);
  cleanup(fixture.temporary);
});

test("UserPromptSubmit skips stage context for clear English and Chinese code-only work", () => {
  const fixture = createProject();
  const prompts = [
    "Refactor this parser function and run its unit tests.",
    "Refactor the experiment script without changing its behavior.",
    "Optimize the training code and keep the public API stable.",
    "Explain the loss function implementation in this module.",
    "Refactor paper_generator.py without changing its output format.",
    "Analyze the map function implementation and simplify the callback logic.",
    "Improve the accuracy calculation in metrics.py.",
    "解释一下这个函数的代码逻辑，顺便修复报错。",
    "重构实验脚本，运行单元测试，但不要修改算法逻辑。",
    "优化训练代码，并解释损失函数实现。",
    "重构论文生成脚本，但不要改变输出格式。",
    "分析实验结果解析函数的代码并修复 bug。",
  ];
  for (const prompt of prompts) {
    assert.deepEqual(runHook("UserPromptSubmit", fixture.project, { prompt }), {}, prompt);
  }
  cleanup(fixture.temporary);
});

test("UserPromptSubmit treats mixed code and research work conservatively", () => {
  const fixture = createProject();
  const output = runHook("UserPromptSubmit", fixture.project, {
    prompt: "Refactor the analysis code, then verify the experiment results and paper claim.",
  });
  assert.equal(output.hookSpecificOutput.hookEventName, "UserPromptSubmit");
  assert.match(output.hookSpecificOutput.additionalContext, /PROMPT RELEVANT/);

  const unclear = runHook("UserPromptSubmit", fixture.project, {
    prompt: "Please continue with the next task.",
  });
  assert.equal(unclear.hookSpecificOutput.hookEventName, "UserPromptSubmit");

  const paperWork = runHook("UserPromptSubmit", fixture.project, {
    prompt: "重构论文的论证结构，并核验核心主张。",
  });
  assert.equal(paperWork.hookSpecificOutput.hookEventName, "UserPromptSubmit");

  for (const prompt of [
    "Review the code and summarize the paper findings.",
    "解释这段代码，然后总结论文结论。",
  ]) {
    const mixed = runHook("UserPromptSubmit", fixture.project, { prompt });
    assert.equal(mixed.hookSpecificOutput.hookEventName, "UserPromptSubmit", prompt);
  }
  cleanup(fixture.temporary);
});

test("PreToolUse returns the exact Codex deny shape for dangerous Bash", () => {
  const fixture = createProject();
  const output = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "rm -rf results" },
  });
  assert.deepEqual(Object.keys(output), ["hookSpecificOutput"]);
  assert.deepEqual(Object.keys(output.hookSpecificOutput).sort(), [
    "hookEventName",
    "permissionDecision",
    "permissionDecisionReason",
  ]);
  assert.equal(output.hookSpecificOutput.hookEventName, "PreToolUse");
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
  assert.match(output.hookSpecificOutput.permissionDecisionReason, /dangerous operation/);

  const restore = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "git checkout -- ." },
  });
  assert.equal(restore.hookSpecificOutput.permissionDecision, "deny");
  assert.match(restore.hookSpecificOutput.permissionDecisionReason, /worktree restoration/);
  cleanup(fixture.temporary);
});

test("PreToolUse blocks direct state edits but permits reads and researchctl", () => {
  const fixture = createProject();
  const patchOutput = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: {
      command: "*** Begin Patch\n*** Update File: .research/state.json\n@@\n- pending\n+ approved\n*** End Patch",
    },
  });
  assert.equal(patchOutput.hookSpecificOutput.permissionDecision, "deny");
  assert.match(patchOutput.hookSpecificOutput.permissionDecisionReason, /researchctl/);

  const shellOutput = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "jq '.gates.idea_freeze.status=\"approved\"' .research/state.json > /tmp/state && mv /tmp/state .research/state.json" },
  });
  assert.equal(shellOutput.hookSpecificOutput.permissionDecision, "deny");

  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "cat .research/state.json" },
  }), {});
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: {
      command: "python3 /plugin/scripts/researchctl.py gate approve idea_freeze --reason 'Human approved the frozen idea.'",
    },
  }), {});
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: {
      command: "python3 /plugin/scripts/researchctl.py artifact register idea_card --path artifacts/idea.md --artifact-id IDEA-1 --version 1 --status ready",
    },
  }), {});

  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: {
      command: "*** Begin Patch\n*** Update File: README.md\n@@\n+Do not edit .research/state.json directly.\n*** End Patch",
    },
  }), {}, "state path in patch prose is not a state target");

  const chainedBypass = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: {
      command: "python3 /plugin/scripts/researchctl.py status; rm .research/state.json",
    },
  });
  assert.equal(chainedBypass.hookSpecificOutput.permissionDecision, "deny");

  for (const command of [
    "cd .research && sed -i '' 's/pending/approved/' state.json",
    "cd .research && rm state.json",
    "cd -- .research && rm state.json",
  ]) {
    const relativeBypass = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(relativeBypass.hookSpecificOutput.permissionDecision, "deny", command);
  }
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "cd .research && cat state.json" },
  }), {});
  const workdirBypass = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "rm state.json", workdir: ".research" },
  });
  assert.equal(workdirBypass.hookSpecificOutput.permissionDecision, "deny");
  cleanup(fixture.temporary);
});

test("PreToolUse handles camelCase fields and blocks explicit pre-Gate launches", () => {
  const gates = Object.fromEntries(policy.gate_order.map((gate) => [gate, pendingGate()]));
  gates.idea_freeze = approvedGate("DEC-IDEA");
  const fixture = createProject({ state: makeState({ current_stage: "method", gates }) });
  const input = officialInput("PreToolUse", fixture.project);
  delete input.tool_name;
  delete input.tool_input;
  input.toolName = "Bash";
  input.toolInput = { command: "sbatch run_registered_experiment.sh" };
  const output = runHook("PreToolUse", fixture.project, {}, { input });
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
  assert.match(output.hookSpecificOutput.permissionDecisionReason, /method_experiment_approval/);

  const chained = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: {
      command: "python3 /plugin/scripts/researchctl.py status && sbatch run_registered_experiment.sh",
    },
  });
  assert.equal(chained.hookSpecificOutput.permissionDecision, "deny");
  cleanup(fixture.temporary);
});

test("PreToolUse blocks manuscript mutation before claim freeze", () => {
  const gates = Object.fromEntries(policy.gate_order.map((gate) => [gate, pendingGate()]));
  gates.idea_freeze = approvedGate("DEC-IDEA");
  gates.method_experiment_approval = approvedGate("DEC-METHOD");
  const fixture = createProject({
    state: makeState({ current_stage: "experiment_results", gates }),
  });
  const output = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { command: "*** Update File: main.tex\n+Unsupported claim" },
  });
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
  assert.match(output.hookSpecificOutput.permissionDecisionReason, /claim_freeze/);
  cleanup(fixture.temporary);
});

test("PreToolUse blocks clear external release actions before release approval", () => {
  const fixture = createProject();
  const output = runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__gmail__send_email",
    tool_input: {
      to: "editor@example.org",
      subject: "Revised manuscript submission",
      body: "Please find the manuscript and reviewer response attached.",
    },
  });
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
  assert.match(output.hookSpecificOutput.permissionDecisionReason, /release Gate/);
  cleanup(fixture.temporary);
});

test("PreToolUse requires release reopening before revising an approved artifact", () => {
  const gates = Object.fromEntries(policy.gate_order.map((gate) => [gate, pendingGate()]));
  gates.idea_freeze = approvedGate("DEC-IDEA");
  gates.method_experiment_approval = approvedGate("DEC-METHOD");
  gates.claim_freeze = approvedGate("DEC-CLAIM");
  gates.release = approvedGate("DEC-RELEASE");
  const fixture = createProject({
    state: makeState({ current_stage: "revision", gates }),
  });
  const output = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { command: "*** Update File: rebuttal.tex\n+Revised response" },
  });
  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
  assert.match(output.hookSpecificOutput.permissionDecisionReason, /Reopen release through researchctl/);

  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: {
      command: "python3 /plugin/scripts/researchctl.py gate reopen release --reason 'Revision requested by the human owner.'",
    },
  }), {});
  cleanup(fixture.temporary);
});

test("an explicitly reopened release permits local revision but still blocks external send", () => {
  const gates = Object.fromEntries(policy.gate_order.map((gate) => [gate, pendingGate()]));
  gates.idea_freeze = approvedGate("DEC-IDEA");
  gates.method_experiment_approval = approvedGate("DEC-METHOD");
  gates.claim_freeze = approvedGate("DEC-CLAIM");
  gates.release = reopenedGate("DEC-RELEASE", "DEC-REOPEN");
  const fixture = createProject({
    state: makeState({ current_stage: "revision", gates }),
  });
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { command: "*** Update File: rebuttal.tex\n+Verified revision" },
  }), {});

  const send = runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__gmail__send_email",
    tool_input: {
      subject: "Reviewer response submission",
      body: "Send the revised manuscript and reviewer response.",
    },
  });
  assert.equal(send.hookSpecificOutput.permissionDecision, "deny");
  cleanup(fixture.temporary);
});

test("PostToolUse is quiet unless state was touched", () => {
  const fixture = createProject();
  assert.deepEqual(runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "git status --short" },
    tool_response: { stdout: "", exit_code: 0 },
  }), {});
  cleanup(fixture.temporary);
});

test("PostToolUse validates touched state and artifact pointers with exact output shape", () => {
  const fixture = createProject({
    state: makeState({ artifacts: { run_registry: { path: "artifacts/missing-runs.json" } } }),
  });
  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "python3 /plugin/scripts/researchctl.py status" },
    tool_response: { stdout: "current_stage: idea", exit_code: 0 },
  });
  assert.deepEqual(Object.keys(output), ["hookSpecificOutput"]);
  assert.deepEqual(Object.keys(output.hookSpecificOutput).sort(), [
    "additionalContext",
    "hookEventName",
  ]);
  assert.equal(output.hookSpecificOutput.hookEventName, "PostToolUse");
  assert.match(output.hookSpecificOutput.additionalContext, /Schema and Gate invariants: valid/);
  assert.match(output.hookSpecificOutput.additionalContext, /missing-runs\.json/);
  assert.equal(Object.prototype.hasOwnProperty.call(output, "decision"), false);
  cleanup(fixture.temporary);
});

test("PostToolUse recognizes researchctl artifact registration as a state mutation", () => {
  const fixture = createProject();
  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: {
      command: "python3 /plugin/scripts/researchctl.py artifact register idea_card --path artifacts/idea.md --artifact-id IDEA-1 --version 1 --status ready",
    },
    tool_response: { stdout: "registered artifact", exit_code: 0 },
  });
  assert.equal(output.hookSpecificOutput.hookEventName, "PostToolUse");
  assert.match(output.hookSpecificOutput.additionalContext, /Schema and Gate invariants: valid/);
  cleanup(fixture.temporary);
});

test("PostToolUse rejects research control metadata as a canonical artifact", () => {
  const fixture = createProject({
    state: makeState({
      artifacts: {
        idea: {
          idea_card: {
            "IDEA-1": {
              path: ".research/memory.md",
              artifact_id: "IDEA-1",
              version: "1",
              content_hash: `sha256:${"0".repeat(64)}`,
              status: "current",
            },
          },
        },
      },
    }),
  });
  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "python3 /plugin/scripts/researchctl.py status" },
    tool_response: { exit_code: 0 },
  });
  assert.match(
    output.hookSpecificOutput.additionalContext,
    /research control metadata, which cannot be evidence/,
  );
  cleanup(fixture.temporary);
});

test("PostToolUse reports schema and Gate violations after a touched state", () => {
  const fixture = createProject({
    state: makeState({ current_stage: "method", stage_history: "invalid" }),
  });
  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { command: "*** Update File: .research/state.json\n*** End Patch" },
    tool_response: { ok: true },
  });
  const context = output.hookSpecificOutput.additionalContext;
  assert.match(context, /requires approved Gate idea_freeze/);
  assert.match(context, /stage_history must be an array/);
  assert.match(context, /researchctl doctor/);
  cleanup(fixture.temporary);
});

test("PostToolUse accepts an explicitly reopened downstream Gate and rejects pointer metadata without path", () => {
  const gates = Object.fromEntries(policy.gate_order.map((gate) => [gate, pendingGate()]));
  gates.idea_freeze = approvedGate("DEC-IDEA");
  gates.method_experiment_approval = approvedGate("DEC-METHOD");
  gates.claim_freeze = approvedGate("DEC-CLAIM");
  gates.release = reopenedGate("DEC-RELEASE", "DEC-REOPEN");
  const fixture = createProject({
    state: makeState({
      current_stage: "revision",
      gates,
      artifacts: { response: { artifact_id: "RESP-1", version: "v2" } },
    }),
  });
  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "python3 /plugin/scripts/researchctl.py status" },
    tool_response: { exit_code: 0 },
  });
  const context = output.hookSpecificOutput.additionalContext;
  assert.equal(context.includes("current_stage revision requires Gate release"), false);
  assert.match(context, /artifacts\.response is an artifact pointer but has no path/);
  cleanup(fixture.temporary);
});

test("Stop blocks once for a material semantic audit and emits the exact release shape", () => {
  const fixture = createProject();
  const material = "The experiment is completed and verified. Accuracy improved by 12%, all citations were checked, and the evidence proves the claim is novel.";
  const output = runHook("Stop", fixture.project, {
    last_assistant_message: material,
    stop_hook_active: false,
  });
  assert.deepEqual(Object.keys(output).sort(), ["decision", "reason"]);
  assert.equal(output.decision, "block");
  assert.ok(output.reason.length <= 1800);
  assert.match(output.reason, /single stop-time semantic audit/);
  assert.match(output.reason, /applicable policy invariants/);
  assert.match(output.reason, /Claims, numbers, artifacts/);
  assert.match(output.reason, /Claim scope and certainty/);
  assert.match(output.reason, /Gate to exit: idea_freeze \(pending\)/);
  cleanup(fixture.temporary);
});

test("Stop skips ordinary answers and never loops when already active", () => {
  const fixture = createProject();
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "好的。",
    stop_hook_active: false,
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "Refactored the parser, simplified its control flow, and all unit tests pass.",
    stop_hook_active: false,
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "已经解释了函数细节并修复代码报错，单元测试通过。",
    stop_hook_active: false,
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "已重构实验脚本，单元测试通过，未改变算法逻辑。",
    stop_hook_active: false,
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "The experiment script refactor is complete and all unit tests pass.",
    stop_hook_active: false,
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "The loss function implementation is complete and its unit tests pass.",
    stop_hook_active: false,
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "The map function implementation improved and its unit tests pass.",
    stop_hook_active: false,
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "论文生成脚本已更新，单元测试通过。",
    stop_hook_active: false,
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "The manuscript parser is ready and all unit tests pass.",
    stop_hook_active: false,
  }), {});
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "The researchctl parser was updated and its tests pass.",
    stop_hook_active: false,
  }), {});

  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "结果已经完成并验证，准确率提升 12%。",
    stop_hook_active: true,
  }), {});

  const camelInput = officialInput("Stop", fixture.project, {
    last_assistant_message: "A material research result was completed and verified.",
  });
  delete camelInput.stop_hook_active;
  camelInput.stopHookActive = true;
  assert.deepEqual(runHook("Stop", fixture.project, {}, { input: camelInput }), {});
  cleanup(fixture.temporary);
});

test("Stop audits research deliverables and Gate claims", () => {
  const fixture = createProject();
  for (const last_assistant_message of [
    "mAP improved by 2.3%, which supports the central claim.",
    "The claim ledger is completed and the release Gate is approved.",
    "The paper was revised.",
    "The paper parser is ready, and the paper itself was revised.",
    "I edited the manuscript after checking its citations.",
    "Our method outperforms the baseline.",
    "The benchmark shows our method is significantly better.",
    "论文已修改，审稿回复已准备完成。",
    "论文已经重写，引用也已核验。",
    "基准结果表明我们的方法显著更好。",
  ]) {
    const output = runHook("Stop", fixture.project, {
      last_assistant_message,
      stop_hook_active: false,
    });
    assert.equal(output.decision, "block", last_assistant_message);
  }
  cleanup(fixture.temporary);
});

test("Stop audits short Chinese material claims", () => {
  const fixture = createProject();
  const output = runHook("Stop", fixture.project, {
    last_assistant_message: "结果已经完成并验证，准确率提升 12%。",
    stop_hook_active: false,
  });
  assert.equal(output.decision, "block");
  cleanup(fixture.temporary);
});
