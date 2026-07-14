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
    timeout: 6000,
  });
  assert.equal(result.error, undefined, result.error && result.error.message);
  assert.equal(result.signal, null, "Hook must not hang or be terminated by a signal");
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
    timeout: 6000,
  });
  assert.equal(result.error, undefined, result.error && result.error.message);
  assert.equal(result.signal, null, "Hook must not hang or be terminated by a signal");
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

test("Hooks reject an artifact layout outside .research", () => {
  const fixture = createProject();
  const temporaryPlugin = fs.mkdtempSync(path.join(os.tmpdir(), "research-hook-plugin-"));
  const unsafePolicy = structuredClone(policy);
  unsafePolicy.artifact_layout = {
    generated_root: "contracts",
    stage_path_template: "contracts/<stage-id>",
    instruction: "Write new workflow artifacts under contracts/<stage-id>/.",
  };
  write(
    path.join(temporaryPlugin, "skills", "research", "references", "policy.yaml"),
    `${JSON.stringify(unsafePolicy, null, 2)}\n`,
  );

  assert.deepEqual(runHook(
    "UserPromptSubmit",
    fixture.project,
    { prompt: "Continue the research task." },
    { env: { PLUGIN_ROOT: temporaryPlugin } },
  ), {});

  cleanup(fixture.temporary);
  cleanup(temporaryPlugin);
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
  assert.match(context, /\.research\/artifacts\/<stage-id>/);
  assert.match(context, /project-root research\/, contracts\/, or artifacts\//);
  assert.match(context, /policy\.yaml review_language/);
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
      command: "python3 /plugin/scripts/researchctl.py artifact register idea_card --path .research/artifacts/idea/idea.md --artifact-id IDEA-1 --version 1 --status ready",
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
  assert.match(output.hookSpecificOutput.additionalContext, /Quick structural state\/Gate checks found no issue/);
  assert.match(output.hookSpecificOutput.additionalContext, /missing-runs\.json/);
  assert.equal(Object.prototype.hasOwnProperty.call(output, "decision"), false);
  cleanup(fixture.temporary);
});

test("PostToolUse recognizes researchctl artifact registration as a state mutation", () => {
  const fixture = createProject();
  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: {
      command: "python3 /plugin/scripts/researchctl.py artifact register idea_card --path .research/artifacts/idea/idea.md --artifact-id IDEA-1 --version 1 --status ready",
    },
    tool_response: { stdout: "registered artifact", exit_code: 0 },
  });
  assert.equal(output.hookSpecificOutput.hookEventName, "PostToolUse");
  assert.match(output.hookSpecificOutput.additionalContext, /authoritative hash verification/);
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

test("Stop preserves the preceding answer and requests one audit addendum", () => {
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
  assert.match(output.reason, /preceding assistant answer must remain unchanged/);
  assert.match(output.reason, /do not reproduce, rewrite, replace, or silently correct it/);
  assert.match(output.reason, /Return only a concise, evidence-bounded audit addendum/);
  assert.match(output.reason, /beginning with `\[Stop Hook Review\]`/);
  assert.doesNotMatch(output.reason, /corrected, evidence-bounded user-facing answer/);
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

test("event disagreement between argv and stdin returns no decision", () => {
  const fixture = createProject();
  const disguised = officialInput("SessionStart", fixture.project);
  assert.deepEqual(runHook("PreToolUse", fixture.project, {}, { input: disguised }), {});
  cleanup(fixture.temporary);
});

test("a valid multi-hundred-KiB state keeps every Hook event active", () => {
  const fixture = createProject({
    state: makeState({ stress_padding: "x".repeat(384 * 1024) }),
  });
  const session = runHook("SessionStart", fixture.project);
  assert.equal(session.hookSpecificOutput.hookEventName, "SessionStart");
  const pre = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "rm -r -f results" },
  });
  assert.equal(pre.hookSpecificOutput.permissionDecision, "deny");
  const post = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "researchctl status" },
  });
  assert.equal(post.hookSpecificOutput.hookEventName, "PostToolUse");
  const stop = runHook("Stop", fixture.project, {
    last_assistant_message: "Table 2 reports top-1 accuracy of 91.2%.",
  });
  assert.equal(stop.decision, "block");
  cleanup(fixture.temporary);
});

test("large official tool responses do not truncate the Hook input envelope", () => {
  const fixture = createProject();
  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "researchctl status" },
    tool_response: { stdout: "x".repeat(512 * 1024), exit_code: 0 },
  });
  assert.equal(output.hookSpecificOutput.hookEventName, "PostToolUse");
  assert.match(output.hookSpecificOutput.additionalContext, /QUICK CHECK/);
  cleanup(fixture.temporary);
});

test("state mutation detection covers shell wrappers PowerShell sponge and the transaction lock", () => {
  const fixture = createProject();
  for (const command of [
    "sh -c 'cd .research && rm state.json'",
    "bash -lc 'cd .research && truncate -s 0 state.json'",
    "Set-Location .research; Remove-Item state.json",
    "jq '.enabled=false' .research/state.json | sponge .research/state.json",
    "rm .research/state.lock",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", command);
  }
  cleanup(fixture.temporary);
});

test("state mutation detection resolves symlink workdirs and scans every path field", () => {
  const fixture = createProject();
  const alias = path.join(fixture.temporary, "research-alias");
  fs.symlinkSync(path.join(fixture.project, ".research"), alias, "dir");
  const symlinked = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "rm state.json", workdir: alias },
  });
  assert.equal(symlinked.hookSpecificOutput.permissionDecision, "deny");
  const directoryAlias = path.join(fixture.project, "state-link");
  fs.symlinkSync(path.join(fixture.project, ".research"), directoryAlias, "dir");
  const changedIntoAlias = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "cd state-link && rm state.json" },
  });
  assert.equal(changedIntoAlias.hookSpecificOutput.permissionDecision, "deny");

  const files = Array.from({ length: 48 }, (_, index) => ({ path: `ordinary-${index}.txt` }));
  files.push({ path: ".research/state.json" });
  const batched = runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__files__update",
    tool_input: { files },
  });
  assert.equal(batched.hookSpecificOutput.permissionDecision, "deny");
  cleanup(fixture.temporary);
});

test("dangerous command table covers split flags git global options and dry runs", () => {
  const fixture = createProject();
  for (const command of [
    "rm -r -f results",
    "rm --recursive --force results",
    "git -C another-repo reset --hard",
    "git checkout HEAD -- .",
    "git clean --force -d",
    "git clean -f",
    "git restore --staged --worktree .",
    "Remove-Item -Recurse -Force results",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", command);
  }
  for (const command of [
    "git clean -n -d",
    "git clean --dry-run -d",
    "git restore --staged main.tex",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    }), {}, command);
  }
  cleanup(fixture.temporary);
});

test("experiment launch table blocks common runners without blocking test utilities", () => {
  const fixture = createProject();
  for (const command of [
    "torchrun --nproc_per_node=8 train.py",
    "accelerate launch scripts/train.py",
    "deepspeed train_model.py",
    "python -m project.train --config run.yaml",
    "python scripts/my_train.py",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", command);
    assert.match(output.hookSpecificOutput.permissionDecisionReason, /method_experiment_approval/);
  }
  for (const command of [
    "python test_training.py",
    "python training_utils.py",
    "pytest tests/test_training.py",
    "python -m pytest tests/train.py",
    "torchrun --help",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    }), {}, command);
  }
  cleanup(fixture.temporary);
});

test("manuscript mutation table covers ordinary filesystem commands", () => {
  const fixture = createProject();
  for (const command of [
    "cp draft.tex main.tex",
    "mv revised.tex rebuttal.tex",
    "rm appendix.tex",
    "truncate -s 0 response.md",
    "rsync draft.tex paper.tex",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", command);
    assert.match(output.hookSpecificOutput.permissionDecisionReason, /claim_freeze/);
  }
  for (const command of ["cat main.tex", "git diff -- main.tex", "wc -l rebuttal.tex"]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    }), {}, command);
  }
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "cp main.tex backup.tex" },
  }), {});
  const sponge = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "pandoc draft.md | sponge main.tex" },
  });
  assert.equal(sponge.hookSpecificOutput.permissionDecision, "deny");
  cleanup(fixture.temporary);
});

test("release detection covers explicit submission transfers but permits backups", () => {
  const fixture = createProject();
  const submission = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "scp paper.pdf editor@example.org:/submissions/" },
  });
  assert.equal(submission.hookSpecificOutput.permissionDecision, "deny");
  const backup = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "scp paper.pdf backup@example.org:/archive/" },
  });
  assert.deepEqual(backup, {});
  const chair = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "scp rebuttal.pdf chair@example.org:/incoming/" },
  });
  assert.equal(chair.hookSpecificOutput.permissionDecision, "deny");
  const download = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "curl https://openreview.net/paper.pdf -o paper.pdf" },
  });
  assert.deepEqual(download, {});
  cleanup(fixture.temporary);
});

test("PostToolUse calls its validation quick and rejects drifted fields and timestamps", () => {
  const fixture = createProject({
    state: makeState({
      created_at: "2026-07-14T00:00:00",
      artifacts: {
        idea: {
          idea_card: {
            "WRONG-KEY": {
              path: ".research/memory.md",
              artifact_id: "IDEA-CARD-1",
              version: true,
              content_hash: "not-a-hash",
              status: "",
            },
          },
        },
      },
    }),
  });
  const output = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "researchctl status" },
  });
  const context = output.hookSpecificOutput.additionalContext;
  assert.match(context, /QUICK CHECK/);
  assert.match(context, /created_at must be a timezone-explicit UTC timestamp/);
  assert.match(context, /research control metadata/);
  assert.equal(context.includes("Schema and Gate invariants: valid"), false);
  cleanup(fixture.temporary);
});

test("PostToolUse detects control-file symlink and hardlink artifact aliases", (t) => {
  const fixture = createProject();
  const artifactDirectory = path.join(fixture.project, ".research", "artifacts", "idea");
  fs.mkdirSync(artifactDirectory, { recursive: true });
  const aliases = [path.join(artifactDirectory, "state-symlink.json")];
  fs.symlinkSync(path.join(fixture.project, ".research", "state.json"), aliases[0]);
  const hardlink = path.join(artifactDirectory, "state-hardlink.json");
  try {
    fs.linkSync(path.join(fixture.project, ".research", "state.json"), hardlink);
    aliases.push(hardlink);
  } catch (_error) {
    t.diagnostic("hardlinks are unavailable on this filesystem");
  }
  for (const [index, alias] of aliases.entries()) {
    const relative = path.relative(fixture.project, alias);
    const state = makeState({
      artifacts: {
        idea: {
          idea_card: {
            [`IDEA-${index}`]: {
              path: relative,
              artifact_id: `IDEA-${index}`,
              version: "1",
              content_hash: `sha256:${"0".repeat(64)}`,
              status: "current",
            },
          },
        },
      },
    });
    write(path.join(fixture.project, ".research", "state.json"), `${JSON.stringify(state)}\n`);
    const output = runHook("PostToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command: "researchctl status" },
    });
    assert.match(output.hookSpecificOutput.additionalContext, /research control metadata/);
  }
  cleanup(fixture.temporary);
});

test("Prompt routing recognizes failing tests lint and typecheck as code-only", () => {
  const fixture = createProject();
  for (const prompt of [
    "Why does this test fail?",
    "Investigate the failing CI test.",
    "Run lint and typecheck.",
    "为什么这个测试失败？",
  ]) {
    assert.deepEqual(runHook("UserPromptSubmit", fixture.project, { prompt }), {}, prompt);
  }
  cleanup(fixture.temporary);
});

test("Stop separates code metric repairs from material top-1 claims", () => {
  const fixture = createProject();
  const code = runHook("Stop", fixture.project, {
    last_assistant_message: "Fixed the metric parser bug that incorrectly reported accuracy improved by 12%; unit tests pass.",
    stop_hook_active: false,
  });
  assert.deepEqual(code, {});
  const claim = runHook("Stop", fixture.project, {
    last_assistant_message: "Table 2 reports a top-1 score of 91.2%, and the conclusions were updated accordingly.",
    stop_hook_active: false,
  });
  assert.equal(claim.decision, "block");
  const mixed = runHook("Stop", fixture.project, {
    last_assistant_message: "Fixed the analysis script bug; mAP improved by 2.3%, supporting the central claim.",
    stop_hook_active: false,
  });
  assert.equal(mixed.decision, "block");
  cleanup(fixture.temporary);
});

test("deep and wide Hook inputs cannot bypass state protection or crash quick validation", () => {
  const fixture = createProject();
  const envelope = officialInput("PreToolUse", fixture.project, { tool_name: "mcp__files__update" });
  delete envelope.tool_input;
  const deepToolInput = `${'{"nested":'.repeat(12000)}{"path":".research/state.json"}${"}".repeat(12000)}`;
  const deepRaw = `${JSON.stringify(envelope).slice(0, -1)},"tool_input":${deepToolInput}}`;
  const deepOutput = runRaw("PreToolUse", fixture.project, deepRaw);
  assert.equal(deepOutput.hookSpecificOutput.permissionDecision, "deny");

  const wide = Array.from({ length: 150000 }, () => ({}));
  wide.push({ path: ".research/state.json" });
  const wideOutput = runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__files__update",
    tool_input: { files: wide },
  });
  assert.equal(wideOutput.hookSpecificOutput.permissionDecision, "deny");

  const state = makeState();
  delete state.artifacts;
  const deepArtifacts = `${'{"x":'.repeat(8000)}null${"}".repeat(8000)}`;
  const rawState = `${JSON.stringify(state).slice(0, -1)},"artifacts":{"_legacy":${deepArtifacts}}}`;
  write(path.join(fixture.project, ".research", "state.json"), rawState);
  const post = runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "researchctl status" },
  });
  assert.match(post.hookSpecificOutput.additionalContext, /QUICK CHECK/);
  cleanup(fixture.temporary);
});

test("state authority protection covers patch moves structured tools control directories and nested shells", () => {
  const fixture = createProject();
  const commands = [
    "rm -r .research",
    "mv .research research-backup",
    "rm .research/state.*",
    "find .research -type f -delete",
    "truncate -s 0 .research/state.*",
    "chmod 000 .research",
    "Remove-Item -Recurse .research",
    "Move-Item .research research-backup",
    "Rename-Item .research research-old",
    "bash -lc 'rm -r .research'",
    "bash -lc 'cd .research && rm -r *'",
    "cd .research && echo broken>state.json",
  ];
  for (const command of commands) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", command);
  }
  const cwdOutput = runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "find . -delete", workdir: ".research" },
  });
  assert.equal(cwdOutput.hookSpecificOutput.permissionDecision, "deny");

  for (const [tool_name, tool_input] of [
    ["mcp__filesystem__move_file", { source: ".research/state.json", destination: "backup.json" }],
    ["mcp__filesystem__rename", { oldPath: ".research/state.json", newPath: "backup.json" }],
    ["mcp__filesystem__delete", { filename: ".research/state.json" }],
    ["mcp__filesystem__delete_directory", { path: ".research" }],
    ["mcp__filesystem__move_directory", { source: ".research", destination: "research-backup" }],
  ]) {
    const output = runHook("PreToolUse", fixture.project, { tool_name, tool_input });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", tool_name);
  }
  const patchMove = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Begin Patch\n*** Update File: harmless.json\n*** Move to: .research/state.json\n*** End Patch" },
  });
  assert.equal(patchMove.hookSpecificOutput.permissionDecision, "deny");

  const stateAlias = path.join(fixture.project, "state-alias.json");
  fs.symlinkSync(path.join(fixture.project, ".research", "state.json"), stateAlias);
  const aliasWrite = runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__filesystem__write_file",
    tool_input: { path: "state-alias.json", content: "{}" },
  });
  assert.equal(aliasWrite.hookSpecificOutput.permissionDecision, "deny");
  const stateHardlink = path.join(fixture.project, "state-hardlink.json");
  try {
    fs.linkSync(path.join(fixture.project, ".research", "state.json"), stateHardlink);
    const hardlinkWrite = runHook("PreToolUse", fixture.project, {
      tool_name: "mcp__filesystem__write_file",
      tool_input: { path: "state-hardlink.json", content: "{}" },
    });
    assert.equal(hardlinkWrite.hookSpecificOutput.permissionDecision, "deny");
  } catch (error) {
    if (!["EPERM", "EACCES", "ENOTSUP", "EXDEV"].includes(error.code)) throw error;
  }
  cleanup(fixture.temporary);
});

test("dangerous shell parsing handles quotes wrappers and common destructive git variants", () => {
  const fixture = createProject();
  for (const command of [
    "rm '-rf' results",
    "git restore --staged -W .",
    "git checkout HEAD main.tex",
    "git checkout --ours main.tex",
    "git -c core.autocrlf=false reset --hard",
    "git --no-pager reset --hard",
    "bash -lc 'rm -rf results'",
    "sh -c 'git reset --hard'",
    "zsh -c 'git clean -f'",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", command);
  }
  for (const command of [
    "echo 'rm -r -f results'",
    "echo 'git reset --hard'",
    "git checkout feature-branch",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    }), {}, command);
  }
  cleanup(fixture.temporary);
});

test("experiment launch detection handles compound commands help flags and server wrappers", () => {
  const fixture = createProject();
  for (const command of [
    "python -m pytest tests; torchrun train.py",
    "python -m unittest tests; sbatch train.sh",
    "sbatch train.sh; torchrun --help",
    "qsub run.sh && deepspeed --version",
    "srun python train.py",
    "mpirun python train.py",
    "mpiexec python train.py",
    "torchrun train.py --help",
    "deepspeed train.py --version",
    "accelerate launch train.py -h",
    "nohup torchrun --nproc_per_node=8 train.py > train.log 2>&1 &",
    "env CUDA_VISIBLE_DEVICES=0 torchrun train.py",
    "time deepspeed train.py",
    "bash -lc 'torchrun train.py'",
    "python -m project.training --config x",
    "make train",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", command);
  }
  for (const command of [
    "torchrun --help",
    "deepspeed --version",
    "accelerate launch -h",
    "srun --help",
    "mpirun --version",
    "echo 'python train.py'",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    }), {}, command);
  }
  cleanup(fixture.temporary);
});

test("manuscript protection follows actual mutation direction patch targets and registered paths", () => {
  const fixture = createProject({
    state: makeState({
      artifacts: {
        paper: {
          manuscript: {
            "MANUSCRIPT-CUSTOM": {
              path: "paper/custom-submission.tex",
              artifact_id: "MANUSCRIPT-CUSTOM",
              version: "1",
              content_hash: `sha256:${"0".repeat(64)}`,
              status: "current",
            },
            "MANUSCRIPT-OPAQUE": {
              path: "paper/custom-submission.xyz",
              artifact_id: "MANUSCRIPT-OPAQUE",
              version: "1",
              content_hash: `sha256:${"1".repeat(64)}`,
              status: "current",
            },
          },
        },
      },
    }),
  });
  write(path.join(fixture.project, "paper", "custom-submission.tex"), "custom manuscript\n");
  write(path.join(fixture.project, "paper", "custom-submission.xyz"), "opaque manuscript\n");
  for (const command of [
    "mv main.tex backup.tex",
    "bash -lc 'rm main.tex'",
    "echo hi>main.tex",
    "cat draft.md>>rebuttal.tex",
    "python x.py 2>paper.md",
    "rm -r paper",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", command);
  }
  for (const command of [
    "rm scratch && cat main.tex",
    "cp main.tex backup.tex",
    "echo 'literal >main.tex'",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    }), {}, command);
  }
  const move = runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__filesystem__move_file",
    tool_input: { source: "main.tex", destination: "backup.tex" },
  });
  assert.equal(move.hookSpecificOutput.permissionDecision, "deny");
  const registeredMove = runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__filesystem__move_file",
    tool_input: { source: "paper/custom-submission.tex", destination: "backup.tex" },
  });
  assert.equal(registeredMove.hookSpecificOutput.permissionDecision, "deny");
  const registeredParentDelete = runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__filesystem__delete_directory",
    tool_input: { path: "paper" },
  });
  assert.equal(registeredParentDelete.hookSpecificOutput.permissionDecision, "deny");
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "mcp__filesystem__copy_file",
    tool_input: { source: "main.tex", destination: "backup.tex" },
  }), {});
  assert.deepEqual(runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Begin Patch\n*** Update File: README.md\n@@\n+See main.tex\n*** End Patch" },
  }), {});
  const patchMove = runHook("PreToolUse", fixture.project, {
    tool_name: "apply_patch",
    tool_input: { patch: "*** Begin Patch\n*** Update File: draft.tex\n*** Move to: supplement.tex\n*** End Patch" },
  });
  assert.equal(patchMove.hookSpecificOutput.permissionDecision, "deny");
  cleanup(fixture.temporary);
});

test("release transfer detection distinguishes uploads from downloads across common CLIs", () => {
  const fixture = createProject();
  for (const command of [
    "scp paper.pdf conference:/submissions/",
    "rsync rebuttal.pdf conference:/incoming/",
    "aws s3 cp paper.pdf s3://bucket/submissions/paper.pdf",
    "curl -Tpaper.pdf https://openreview.net/submissions/paper.pdf",
    "curl -Ffile=@paper.pdf https://openreview.net/submissions/",
    "curl --data-binary @paper.pdf https://openreview.net/submissions/",
    "curl -X POST --json @paper.json https://openreview.net/submissions/",
    "bash -lc 'scp paper.pdf conference:/submissions/'",
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", command);
  }
  for (const command of [
    "scp chair@conference.org:/incoming/rebuttal.pdf rebuttal.pdf",
    "aws s3 cp s3://bucket/submissions/paper.pdf paper.pdf",
    "curl https://openreview.net/forum?id=123",
    "curl -L https://openreview.net/paper.pdf -o paper.pdf",
  ]) {
    assert.deepEqual(runHook("PreToolUse", fixture.project, {
      tool_name: "Bash",
      tool_input: { command },
    }), {}, command);
  }
  cleanup(fixture.temporary);
});

test("prompt and Stop routing separates code detail from mixed and material scientific claims", () => {
  const fixture = createProject();
  for (const prompt of [
    "What does this function do?",
    "What is this class for?",
    "Review the git diff.",
    "Why does pytest fail?",
    "Explain the regex.",
    "Can you simplify this SQL query?",
  ]) {
    assert.deepEqual(runHook("UserPromptSubmit", fixture.project, { prompt }), {}, prompt);
  }
  for (const prompt of [
    "Inspect the failing hypothesis test.",
    "Fix the experiment result parser, then assess accuracy on the final runs.",
    "Refactor accuracy calculation, then compare mAP against baseline.",
    "修复实验结果解析器，然后评估最终运行的准确率。",
  ]) {
    const output = runHook("UserPromptSubmit", fixture.project, { prompt });
    assert.match(output.hookSpecificOutput.additionalContext, /PROMPT RELEVANT/, prompt);
  }
  for (const message of [
    "Fixed the script bug; accuracy improved by 12%.",
    "Table 2 reports 91.2% top-1 accuracy on the held-out set.",
    "RMSE decreased to 0.42.",
    "The difference was significant (p=0.03, 95% CI 0.1-0.4).",
    "Three failed runs were excluded from analysis.",
    "No significant difference was found.",
    "修复脚本后，准确率提升了12%。",
  ]) {
    const output = runHook("Stop", fixture.project, {
      last_assistant_message: message,
      stop_hook_active: false,
    });
    assert.equal(output.decision, "block", message);
  }
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "Fixed the parser that incorrectly reported accuracy improved by 12%; tests pass.",
    stop_hook_active: false,
  }), {});
  cleanup(fixture.temporary);
});
