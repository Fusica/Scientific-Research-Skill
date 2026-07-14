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
  return reopenedGateFor("release", approveId, reopenId);
}

function reopenedGateFor(gate, approveId, reopenId) {
  const releaseTarget = gate === "release" ? "initial_submission" : null;
  const record = approvedGate(approveId, releaseTarget);
  record.status = "reopened";
  record.latest_decision_id = reopenId;
  const decision = {
    decision_id: reopenId,
    action: "reopen",
    previous_status: "approved",
    new_status: "reopened",
    reason: "The human owner reopened the release for a verified revision.",
    actor: "test-owner",
    decided_at: "2026-07-13T09:00:00Z",
    artifact_refs: [],
  };
  if (releaseTarget !== null) decision.release_target = releaseTarget;
  record.history.push(decision);
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

function stageTransition(fromStage, toStage, index) {
  return {
    from_stage: fromStage,
    to_stage: toStage,
    trigger: `test-transition:${fromStage}:${toStage}`,
    timestamp: `2026-07-13T08:${String(index).padStart(2, "0")}:00Z`,
  };
}

function stateAtStage(stage, options = {}) {
  const gates = Object.fromEntries(policy.gate_order.map((gate) => [gate, pendingGate()]));
  const stageHistory = [];
  if (stage === "literature") {
    stageHistory.push(stageTransition("idea", "literature", 1));
  }
  if (["method", "experiment_results", "paper", "revision"].includes(stage)) {
    gates.idea_freeze = approvedGate("DEC-IDEA");
    stageHistory.push(stageTransition("idea", "method", 1));
  }
  if (["experiment_results", "paper", "revision"].includes(stage)) {
    gates.method_experiment_approval = approvedGate("DEC-METHOD");
    stageHistory.push(stageTransition("method", "experiment_results", 2));
  }
  if (["paper", "revision"].includes(stage)) {
    gates.claim_freeze = approvedGate("DEC-CLAIM");
    stageHistory.push(stageTransition("experiment_results", "paper", 3));
  }
  if (stage === "revision") {
    gates.release = options.releaseReopened
      ? reopenedGate("DEC-RELEASE", "DEC-REOPEN")
      : approvedGate("DEC-RELEASE", "initial_submission");
    stageHistory.push(stageTransition("paper", "revision", 4));
  }
  return makeState({ current_stage: stage, gates, stage_history: stageHistory });
}

function stateForGateStatus(gate, status) {
  const requiredStage = {
    idea_freeze: "idea",
    method_experiment_approval: "method",
    claim_freeze: "experiment_results",
    release: "paper",
  }[gate];
  const advancedStage = {
    idea_freeze: "method",
    method_experiment_approval: "experiment_results",
    claim_freeze: "paper",
    release: "revision",
  }[gate];
  if (status === "pending") return stateAtStage(requiredStage);
  if (status === "approved") return stateAtStage(advancedStage);
  const state = gate === "release"
    ? stateAtStage("revision", { releaseReopened: true })
    : stateAtStage(requiredStage);
  state.gates[gate] = reopenedGateFor(gate, `DEC-${gate}-APPROVE`, `DEC-${gate}-REOPEN`);
  return state;
}

function assertBlockOutput(output, label = "") {
  assert.deepEqual(Object.keys(output).sort(), ["decision", "reason"], label);
  assert.equal(output.decision, "block", label);
  assert.equal(typeof output.reason, "string", label);
  assert.ok(output.reason.trim(), label);
  assert.ok(output.reason.length <= 1800, label);
  assert.equal(Object.prototype.hasOwnProperty.call(output, "continue"), false, label);
  assert.equal(Object.prototype.hasOwnProperty.call(output, "systemMessage"), false, label);
  assert.equal(Object.prototype.hasOwnProperty.call(output, "suppressOutput"), false, label);
}

function assertWarningOutput(output, label = "") {
  assert.deepEqual(Object.keys(output).sort(), ["continue", "systemMessage"], label);
  assert.equal(output.continue, true, label);
  assert.equal(typeof output.systemMessage, "string", label);
  assert.ok(output.systemMessage.trim(), label);
  assert.ok(output.systemMessage.length <= 1800, label);
  assert.equal(Object.prototype.hasOwnProperty.call(output, "decision"), false, label);
  assert.equal(Object.prototype.hasOwnProperty.call(output, "reason"), false, label);
  assert.equal(Object.prototype.hasOwnProperty.call(output, "suppressOutput"), false, label);
}

function serializedObjectAtSize(value, targetSize, paddingKey = "stress_padding") {
  const candidate = { ...value, [paddingKey]: "" };
  const base = JSON.stringify(candidate);
  assert.ok(base.length <= targetSize, `base JSON exceeds target ${targetSize}`);
  candidate[paddingKey] = "x".repeat(targetSize - base.length);
  const serialized = JSON.stringify(candidate);
  assert.equal(serialized.length, targetSize);
  return serialized;
}

function serializedObjectAtByteSize(value, targetSize, paddingKey = "stress_padding") {
  const candidate = { ...value, [paddingKey]: "" };
  const base = JSON.stringify(candidate);
  const paddingBytes = targetSize - Buffer.byteLength(base, "utf8");
  assert.ok(paddingBytes >= 0, `base JSON exceeds target ${targetSize}`);
  candidate[paddingKey] = `${"é".repeat(Math.floor(paddingBytes / 2))}${
    "x".repeat(paddingBytes % 2)
  }`;
  const serialized = JSON.stringify(candidate);
  assert.equal(Buffer.byteLength(serialized, "utf8"), targetSize);
  return serialized;
}

function serializedStringFieldAtSize(value, field, suffix, targetSize) {
  const candidate = { ...value, [field]: "" };
  const base = JSON.stringify(candidate);
  const encodedSuffixSize = JSON.stringify(suffix).length - 2;
  const paddingSize = targetSize - base.length - encodedSuffixSize;
  assert.ok(paddingSize >= 0, `base JSON exceeds target ${targetSize}`);
  candidate[field] = `${"x".repeat(paddingSize)}${suffix}`;
  const serialized = JSON.stringify(candidate);
  assert.equal(serialized.length, targetSize);
  return serialized;
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
  assert.equal(result.stderr, "", "Hook must not use stderr for ordinary outcomes");
  assert.notEqual(result.stdout.trim(), "", "Hook must emit one JSON object");
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
  assert.equal(document.hooks.SessionStart[0].matcher, "startup|resume|clear|compact");
  assert.equal(Object.prototype.hasOwnProperty.call(document.hooks.UserPromptSubmit[0], "matcher"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(document.hooks.Stop[0], "matcher"), false);
  for (const event of events) {
    assert.match(document.hooks[event][0].hooks[0].commandWindows, new RegExp(`${event}$`));
  }
  const stopStatus = document.hooks.Stop[0].hooks[0].statusMessage;
  assert.match(stopStatus, /mechanical research workflow consistency/i);
  assert.doesNotMatch(stopStatus, /audit/i);
});

test("configured Unix Hook command executes when PLUGIN_ROOT contains spaces", () => {
  const fixture = createProject();
  const temporaryPluginParent = fs.mkdtempSync(path.join(os.tmpdir(), "hook-command-space-"));
  const spacedPluginRoot = path.join(temporaryPluginParent, "plugin root with spaces");
  fs.symlinkSync(pluginRoot, spacedPluginRoot, "dir");
  const document = JSON.parse(
    fs.readFileSync(path.join(pluginRoot, "hooks", "hooks.json"), "utf8"),
  );
  const command = document.hooks.SessionStart[0].hooks[0].command;
  const result = spawnSync("/bin/sh", ["-c", command], {
    cwd: fixture.project,
    env: { ...process.env, PLUGIN_ROOT: spacedPluginRoot },
    input: JSON.stringify(officialInput("SessionStart", fixture.project)),
    encoding: "utf8",
    timeout: 6000,
  });
  assert.equal(result.signal, null);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");
  const output = JSON.parse(result.stdout);
  assert.equal(output.hookSpecificOutput.hookEventName, "SessionStart");
  cleanup(fixture.temporary);
  cleanup(temporaryPluginParent);
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

test("UserPromptSubmit injects the stage boundary and pre-answer semantic audit", () => {
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
  assert.ok(context.length <= 2600);
  assert.match(context, /Current stage: experiment_results/);
  assert.match(context, /PROMPT RELEVANT/);
  assert.match(context, /Current-stage prohibited actions/);
  assert.match(context, /change metrics or exclusions after seeing results/);
  assert.match(context, /claim_freeze \(pending\)/);
  assert.match(context, /\.research\/artifacts\/<stage-id>/);
  assert.match(context, /project-root research\/, contracts\/, or artifacts\//);
  assert.match(context, /policy\.yaml review_language/);
  assert.match(context, /Use the \$research Skill/);
  assert.match(context, /Before the first user-facing final answer/);
  assert.match(context, /silently apply the canonical semantic audit/);
  assert.match(context, /Claims, numbers, artifacts/);
  assert.match(context, /Claim scope and certainty/);
  assert.match(context, /current stage, required artifacts, transition, and Gate state/);
  assert.match(context, /Unresolved risks are stated/);
  assert.match(context, /one complete, self-contained answer/);
  assert.match(context, /Do not emit a standalone audit addendum/);
  for (const invariant of policy.semantic_audit) {
    assert.ok(context.includes(invariant), invariant);
  }
  assert.doesNotMatch(context, /beginning with.*Stop Hook Review/i);
  cleanup(fixture.temporary);
});

test("the original Stop replacement incident is locked as an end-to-end regression", () => {
  const fixture = createProject({ state: stateAtStage("experiment_results") });
  const promptOutput = runHook("UserPromptSubmit", fixture.project, {
    prompt: "那么现在 gate 是否通过，是否可以 method 冻结，进入 exp 或者进入 revision？",
  });
  const context = promptOutput.hookSpecificOutput.additionalContext;
  for (const invariant of policy.semantic_audit) assert.ok(context.includes(invariant));
  assert.match(context, /one complete, self-contained answer/);
  assert.match(context, /Do not emit a standalone audit addendum/);
  assert.doesNotMatch(context, /Return only|preceding assistant answer must remain unchanged|Finish after the audit addendum/);

  const originalAnswer = [
    "结论：Method Gate 已通过，Method 已冻结，并且项目已经进入 experiment_results 阶段；目前不能直接进入正式的 revision 阶段。",
    "当前状态：",
    "method_experiment_approval：approved",
    "冻结文件：method_approval_package_v2.md",
    "审批记录：DEC-20260714T095421Z-47A62781",
    "当前阶段：experiment_results",
    "下一道 Gate：claim_freeze，目前为 pending",
    "状态检查：0 errors / 0 warnings",
    "这里的 experiment_results 不代表要求你重新开展整套实验。",
    "正式流程是：experiment_results → 审批 claim_freeze → paper → 审批 release → revision。",
    "因此，现在可以立即继续处理审稿回复和相关证据，但不能把工作流状态直接跳到 revision。",
  ].join("\n");
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: originalAnswer,
    stop_hook_active: false,
  }), {});

  const legacyOnly = runHook("Stop", fixture.project, {
    last_assistant_message: "[Stop Hook Review] 审核通过：未发现实质性问题。",
    stop_hook_active: false,
  });
  assertWarningOutput(legacyOnly, "legacy-only response");

  const contradiction = runHook("Stop", fixture.project, {
    last_assistant_message: "Current stage: revision",
    stop_hook_active: false,
  });
  assertBlockOutput(contradiction, "first contradictory response");
  assert.match(contradiction.reason, /one complete, self-contained corrected answer/);
  assert.doesNotMatch(contradiction.reason, /Return only|must remain unchanged|\[Stop Hook Review\]/);
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "Current stage: revision",
    stop_hook_active: true,
  }), {});
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

test("Stop leaves ordinary and material answers untouched after the pre-answer audit", () => {
  const fixture = createProject();
  for (const last_assistant_message of [
    "好的。",
    "Refactored the parser, simplified its control flow, and all unit tests pass.",
    "mAP improved by 2.3%, which supports the central claim.",
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
    assert.deepEqual(output, {}, last_assistant_message);
  }
  cleanup(fixture.temporary);
});

test("Stop warns non-blockingly when a response is only a legacy audit addendum", () => {
  const fixture = createProject();
  const output = runHook("Stop", fixture.project, {
    last_assistant_message: "[Stop Hook Review] 审核通过：未发现实质性问题。",
    stop_hook_active: false,
  });
  assert.deepEqual(Object.keys(output).sort(), ["continue", "systemMessage"]);
  assert.equal(output.continue, true);
  assert.match(output.systemMessage, /legacy standalone \[Stop Hook Review\]/);
  assert.match(output.systemMessage, /did not request another model turn/);
  assert.equal(Object.prototype.hasOwnProperty.call(output, "decision"), false);
  cleanup(fixture.temporary);
});

test("Stop reports invalid workflow state without changing the assistant answer", () => {
  const fixture = createProject({
    state: makeState({ stage_history: "not-an-array" }),
  });
  const output = runHook("Stop", fixture.project, {
    last_assistant_message: "Current stage: idea",
    stop_hook_active: false,
  });
  assert.deepEqual(Object.keys(output).sort(), ["continue", "systemMessage"]);
  assert.equal(output.continue, true);
  assert.match(output.systemMessage, /failed mechanical validation/);
  assert.match(output.systemMessage, /stage_history must be an array/);
  assert.match(output.systemMessage, /researchctl doctor/);
  assert.match(output.systemMessage, /No additional model turn was requested/);
  cleanup(fixture.temporary);
});

test("Stop blocks only explicit current-stage or Gate contradictions", () => {
  const gates = Object.fromEntries(policy.gate_order.map((gate) => [gate, pendingGate()]));
  gates.idea_freeze = approvedGate("DEC-IDEA");
  gates.method_experiment_approval = approvedGate("DEC-METHOD");
  const fixture = createProject({
    state: makeState({
      current_stage: "experiment_results",
      gates,
      stage_history: [
        {
          from_stage: "idea",
          to_stage: "method",
          trigger: "gate:idea_freeze:approve:DEC-IDEA",
          timestamp: "2026-07-13T08:00:00Z",
        },
        {
          from_stage: "method",
          to_stage: "experiment_results",
          trigger: "gate:method_experiment_approval:approve:DEC-METHOD",
          timestamp: "2026-07-13T08:00:00Z",
        },
      ],
    }),
  });

  for (const accurate of [
    "Current stage: experiment_results\nmethod_experiment_approval: approved\nGate to exit: claim_freeze (pending)",
    "Current stage: __experiment_results__",
    "Current stage: _experiment_results_",
    "当前阶段：experiment_results\n下一道 Gate：claim_freeze，目前为 pending\nclaim_freeze 待审批",
    "Do not claim \"claim_freeze: approved\"; state remains pending.",
    "After claim_freeze is approved, the workflow may enter paper.",
    "```text\nclaim_freeze: approved\n```",
    "````text\n```\nCurrent stage: revision\n```\n````",
    "~~~text\nCurrent stage: revision\n~~~",
    "    Current stage: revision",
    "> claim_freeze: approved",
    "`claim_freeze: approved`",
    "`claim_freeze: approved` is the incorrect example.",
    "claim_freeze: approved?",
    "Current stage: revision would be wrong.",
    "Current stage: revision? No.",
    "Current stage: revision? Not according to state.",
    "Current stage: revision is not correct; it remains experiment_results.",
    "Current stage: revision only after release is approved.",
    "Current stage: revision provided that release is approved.",
    "Current stage: revision assuming release approval.",
    "Current stage: revision subject to release approval.",
    "Current stage: revision; this is wrong.",
    "Current stage: revision isn't correct.",
    "Current stage: revision isn't right.",
    "Current stage: revision is an example.",
    "Current stage: revision is an illustration.",
    "Gate to exit: release (pending) is an invalid example.",
    "| Gate | Required status |\n| release | approved |",
    "Release approved artifacts only after review.",
    "release pending work is documented.",
    "Incorrect examples:\n- claim_freeze: approved\n- Current stage: revision",
    "错误示例：\n- 当前阶段：revision\n- claim_freeze 已批准",
    "For example:\nCurrent stage: revision\nclaim_freeze: approved",
    "For instance:\nCurrent stage: revision\nclaim_freeze: approved",
    "e.g.,\nCurrent stage: revision\nclaim_freeze: approved",
    "Example output:\nCurrent stage: revision\nclaim_freeze: approved",
    "例如：\n当前阶段：revision\nclaim_freeze 已批准",
    "比如：\n当前阶段：revision\nclaim_freeze 已批准",
    "以下为错误示例：\n当前阶段：revision\nclaim_freeze 已批准",
    "Examples (invalid):\nCurrent stage: revision\nclaim_freeze: approved",
    "Hypothetical:\nCurrent stage: revision\nclaim_freeze: approved",
    "Examples below:\nCurrent stage: revision\nclaim_freeze: approved",
    "Example table:\n\n| Gate | Current status |\n| --- | --- |\n| claim_freeze | approved |",
    "Expected output:\n\nGate | Current status\n--- | ---\nclaim_freeze | approved",
    "错误输出：\n当前阶段：revision\nclaim_freeze 已批准",
    "假设如下：\n当前阶段：revision\nclaim_freeze 已批准",
    "当前阶段：revision？不是。",
    "当前阶段：revision，并不正确。",
    "当前阶段：revision，是错误的。",
    "当前阶段：revision，不对。",
    "当前阶段：revision，并非如此。",
    "当前阶段：revision，是错的。",
    "当前阶段：revision，不是真的。",
    "当前阶段：revision 仅在 release 批准后才能进入。",
    "Incorrect example: Current stage: paper; claim_freeze: approved.",
    "Do not claim Current stage: paper; claim_freeze: approved.",
    "If release is approved; Current stage: revision.",
    "Do not write \"foo; Current stage: revision\".",
    "Example: (foo; claim_freeze: approved).",
    "Incorrect literal: `foo; claim_freeze: approved`.",
    "The literal is 'foo; Current stage: revision'.",
    "The literal is 'isn't valid; Current stage: revision'.",
    "不要写成“foo；当前阶段：revision”。",
    "The following is incorrect, Current stage: revision.",
    "The following is incorrect. Current stage: revision.",
    "For example. Current stage: revision.",
    "e.g. Current stage: revision.",
    "Example. Current stage: revision.",
    "**`claim_freeze: approved`**",
    "    | Gate | Current status |\n    | --- | --- |\n    | claim_freeze | approved |",
    "\t| Gate | Current status |\n\t| --- | --- |\n\t| claim_freeze | approved |",
    "> | Gate | Current status |\n> | --- | --- |\n> | claim_freeze | approved |",
    "`Gate | Current status`\n`--- | ---`\n`claim_freeze | approved`",
    "```text\n    ```\nCurrent stage: revision\n```",
    "| Gate | Current status |\n| --- | --- |\n| claim_freeze | pending |",
    "Gate | Current status\n--- | ---\nclaim_freeze | pending",
    "| Gate | Current status |\n| --- | --- |\n| claim_freeze | pending |\n| Gate | Required status |\n| --- | --- |\n| claim_freeze | approved |",
    "| Field | Current value |\n| --- | --- |\n| Current stage | experiment_results |",
  ]) {
    assert.deepEqual(runHook("Stop", fixture.project, {
      last_assistant_message: accurate,
      stop_hook_active: false,
    }), {}, accurate);
  }

  for (const contradiction of [
    "Current stage: revision",
    "**Current stage:** revision",
    "Current stage: **revision**",
    "- **Current stage:** revision",
    "1. Current stage: revision",
    "Current stage: revision — Review and revision",
    "Current stage: revision — review will start next.",
    "Current stage: revision. Next action is submission.",
    "Current stage: revision. What next?",
    "Current stage: revision; claim_freeze: approved.",
    "Current stage: experiment_results; claim_freeze: approved.",
    "Current stage: experiment_results. claim_freeze: approved.",
    "当前阶段：experiment_results。claim_freeze：已批准。",
    "Current stage: experiment_results, claim_freeze: approved.",
    "Current stage: experiment_results — claim_freeze: approved.",
    "If you want details, ask me. Current stage: revision.",
    "Do not claim unsupported numbers. Current stage: revision.",
    "Current stage: revision after claim_freeze approval.",
    "Current workflow stage: revision",
    "Current-stage: revision",
    "Current stage — revision",
    "Current research stage: paper",
    "当前阶段：paper",
    "当前工作流阶段：paper",
    "claim_freeze: approved",
    "- **claim_freeze:** approved",
    "claim_freeze (approved)",
    "claim_freeze: approved; next step is paper.",
    "claim_freeze: approved because the next step is paper.",
    "claim_freeze: approved. Continue?",
    "claim_freeze: approved after human review.",
    "claim_freeze: approved when the review finished.",
    "claim_freeze approved",
    "claim_freeze approved after human review.",
    "claim_freeze approved because the owner signed.",
    "claim_freeze approved — current",
    "claim_freeze approved today.",
    "claim_freeze approved by the owner.",
    "claim_freeze approved and recorded.",
    "claim_freeze: （approved）",
    "claim_freeze 已批准",
    "claim_freeze：已通过",
    "Gate to exit: claim_freeze (approved)",
    "Gate to exit: release (pending)",
    "Gate to exit: release — pending",
    "The next Gate is release",
    "下一道 Gate 是 release",
    "下一道 Gate：release（pending）",
    "下一道 Gate：claim_freeze，目前为 approved",
    "Current stage: *revision*",
    "Current stage: _revision_",
    "Current stage: __revision__",
    "Current stage: __experiment_results__，claim_freeze: approved.",
    "__claim_freeze__: approved",
    "_claim_freeze_: approved",
    "Current stage: revision? Yes.",
    "Current stage: revision? Correct.",
    "Current stage: revision? Yep.",
    "Current stage: revision? Exactly.",
    "当前阶段：revision（当前）",
    "当前阶段：revision。下一步呢？",
    "当前阶段：revision？是。",
    "当前阶段：revision？是的。",
    "当前阶段：revision？对的。",
    "The reviewers' notes are ready. Current stage: revision.",
    "The users' report is ready; Current stage: revision.",
    "It's ready. Current stage: revision.",
    "Intro\u2028Current stage: revision",
    "Intro\u2029Current stage: revision",
    "Intro\u0085Current stage: revision",
    "Incorrect examples:\n- Current stage: paper\nActual status:\nCurrent stage: revision",
    "Incorrect examples:\n- Current stage: paper\n## Actual state\nCurrent stage: revision",
    "错误示例：\n- 当前阶段：paper\n实际状态：\n当前阶段：revision",
    "| Gate | Current status |\n| --- | --- |\n| claim_freeze | approved |",
    "| Gate | 当前状态 |\n| --- | --- |\n| claim_freeze | 已批准 |",
    "Gate | Current status\n--- | ---\nclaim_freeze | approved",
    "| Field | Current value |\n| --- | --- |\n| Current stage | revision |",
    "    ```text\n    code\nCurrent stage: revision",
  ]) {
    const output = runHook("Stop", fixture.project, {
      last_assistant_message: contradiction,
      stop_hook_active: false,
    });
    assert.deepEqual(Object.keys(output).sort(), ["decision", "reason"], contradiction);
    assert.equal(output.decision, "block", contradiction);
    assert.ok(output.reason.length <= 1800);
    assert.match(output.reason, /explicit mechanical contradiction/);
    assert.match(output.reason, /\.research\/state\.json/);
    assert.match(output.reason, /one complete, self-contained corrected answer/);
    assert.match(output.reason, /do not return a standalone audit addendum/);
    assert.doesNotMatch(output.reason, /\[Stop Hook Review\]/);
  }

  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "Current stage: revision\nclaim_freeze: approved",
    stop_hook_active: true,
  }), {});

  const camelInput = officialInput("Stop", fixture.project, {
    last_assistant_message: "Current stage: revision",
  });
  delete camelInput.stop_hook_active;
  camelInput.stopHookActive = true;
  assert.deepEqual(runHook("Stop", fixture.project, {}, { input: camelInput }), {});
  cleanup(fixture.temporary);
});

test("Stop checks every actual and asserted workflow stage in English and Chinese", () => {
  for (const actualStage of policy.stage_order) {
    const fixture = createProject({ state: stateAtStage(actualStage) });
    for (const assertedStage of policy.stage_order) {
      for (const message of [
        `Current stage: ${assertedStage}`,
        `当前工作流阶段：${assertedStage}`,
      ]) {
        const output = runHook("Stop", fixture.project, {
          last_assistant_message: message,
          stop_hook_active: false,
        });
        if (assertedStage === actualStage) {
          assert.deepEqual(output, {}, `${actualStage}: ${message}`);
        } else {
          assertBlockOutput(output, `${actualStage}: ${message}`);
          assert.match(output.reason, new RegExp(`current_stage=${assertedStage}`));
          assert.match(output.reason, new RegExp(`says ${actualStage}`));
        }
      }
    }
    cleanup(fixture.temporary);
  }
});

test("Stop checks every stage exit Gate including the literature none boundary", () => {
  const assertedTargets = [...policy.gate_order, "none"];
  for (const actualStage of policy.stage_order) {
    const fixture = createProject({ state: stateAtStage(actualStage) });
    const expected = policy.stages[actualStage].gate_to_exit || "none";
    for (const asserted of assertedTargets) {
      for (const message of [
        `Gate to exit: ${asserted}`,
        `下一道 Gate：${asserted === "none" ? "无" : asserted}`,
      ]) {
        const output = runHook("Stop", fixture.project, {
          last_assistant_message: message,
          stop_hook_active: false,
        });
        if (asserted === expected) {
          assert.deepEqual(output, {}, `${actualStage}: ${message}`);
        } else {
          assertBlockOutput(output, `${actualStage}: ${message}`);
          assert.match(output.reason, new RegExp(`gate_to_exit=${asserted}`));
          assert.match(output.reason, new RegExp(`require ${expected}`));
        }
      }
    }
    cleanup(fixture.temporary);
  }
});

test("Stop checks all canonical Gate statuses and Chinese aliases", () => {
  const statuses = policy.state_contract.gate_statuses;
  const chinese = { pending: "待审批", approved: "已批准", reopened: "已重开" };
  for (const gate of policy.gate_order) {
    for (const actualStatus of statuses) {
      const fixture = createProject({ state: stateForGateStatus(gate, actualStatus) });
      for (const assertedStatus of statuses) {
        for (const message of [
          `${gate}: ${assertedStatus}`,
          `${gate}：${chinese[assertedStatus]}`,
        ]) {
          const output = runHook("Stop", fixture.project, {
            last_assistant_message: message,
            stop_hook_active: false,
          });
          if (assertedStatus === actualStatus) {
            assert.deepEqual(output, {}, `${gate}/${actualStatus}: ${message}`);
          } else {
            assertBlockOutput(output, `${gate}/${actualStatus}: ${message}`);
            assert.match(output.reason, new RegExp(`${gate}=${assertedStatus}`));
            assert.match(output.reason, new RegExp(`says ${actualStatus}`));
          }
        }
      }
      if (actualStatus === "approved") {
        assert.deepEqual(runHook("Stop", fixture.project, {
          last_assistant_message: `${gate} Gate 已通过`,
          stop_hook_active: false,
        }), {}, `${gate} approved alias`);
      }
      cleanup(fixture.temporary);
    }
  }
});

test("Stop output precedence and aliases are deterministic", () => {
  const fixture = createProject({ state: stateAtStage("experiment_results") });
  const contradiction = "Current stage: revision";

  for (const activeInput of [
    { stop_hook_active: true },
    { stopHookActive: true, stop_hook_active: undefined },
  ]) {
    const input = officialInput("Stop", fixture.project, {
      last_assistant_message: contradiction,
    });
    if (activeInput.stop_hook_active === undefined) delete input.stop_hook_active;
    Object.assign(input, activeInput);
    assert.deepEqual(runHook("Stop", fixture.project, {}, { input }), {});
  }

  for (const notBooleanTrue of ["true", 1, false, null]) {
    const output = runHook("Stop", fixture.project, {
      last_assistant_message: contradiction,
      stop_hook_active: notBooleanTrue,
    });
    assertBlockOutput(output, `stop_hook_active=${String(notBooleanTrue)}`);
  }

  const snakeWins = officialInput("Stop", fixture.project, {
    last_assistant_message: contradiction,
    stop_hook_active: false,
  });
  snakeWins.stopHookActive = true;
  assertBlockOutput(runHook("Stop", fixture.project, {}, { input: snakeWins }), "snake precedence");

  const camelMessage = officialInput("Stop", fixture.project, { stop_hook_active: false });
  delete camelMessage.last_assistant_message;
  camelMessage.lastAssistantMessage = contradiction;
  assertBlockOutput(runHook("Stop", fixture.project, {}, { input: camelMessage }), "camel message");

  const contradictionBeforeLegacy = runHook("Stop", fixture.project, {
    last_assistant_message: "[Stop Hook Review] audit passed\nCurrent stage: revision",
    stop_hook_active: false,
  });
  assertBlockOutput(contradictionBeforeLegacy, "contradiction outranks legacy marker");

  for (const emptyMessage of ["", "   ", null, 42, {}, []]) {
    assert.deepEqual(runHook("Stop", fixture.project, {
      last_assistant_message: emptyMessage,
      stop_hook_active: false,
    }), {});
  }
  cleanup(fixture.temporary);

  const invalidFixture = createProject({
    state: makeState({ stage_history: "not-an-array" }),
  });
  assert.deepEqual(runHook("Stop", invalidFixture.project, {
    last_assistant_message: "[Stop Hook Review] audit passed\nCurrent stage: revision",
    stop_hook_active: true,
  }), {}, "stop_hook_active outranks invalid state and legacy marker");
  cleanup(invalidFixture.temporary);
});

test("Stop invalid-state warning outranks contradictions and legacy markers", () => {
  const cases = [
    { state: makeState({ stage_history: "not-an-array" }), memory: undefined },
    { state: makeState({ gates: { ...makeState().gates, claim_freeze: { status: "approved", latest_decision_id: null, history: [] } } }), memory: undefined },
  ];
  for (const [index, item] of cases.entries()) {
    const fixture = createProject({ state: item.state, memory: item.memory });
    for (const message of [
      "Current stage: revision",
      "[Stop Hook Review] audit passed",
      "[Stop Hook Review] audit passed\nCurrent stage: revision",
    ]) {
      const output = runHook("Stop", fixture.project, {
        last_assistant_message: message,
        stop_hook_active: false,
      });
      assertWarningOutput(output, `invalid case ${index}: ${message}`);
      assert.match(output.systemMessage, /failed mechanical validation/);
    }
    cleanup(fixture.temporary);
  }

  const missingMemory = createProject({ withMemory: false });
  assertWarningOutput(runHook("Stop", missingMemory.project, {
    last_assistant_message: "Current stage: revision",
    stop_hook_active: false,
  }), "missing memory");
  cleanup(missingMemory.temporary);
});

test("Stop recognizes legacy audit markers without requesting a model continuation", () => {
  const fixture = createProject();
  for (const marker of [
    "[Stop Hook Review] audit passed",
    "   [stop hook review] audit passed",
    "# [Stop Hook Review] audit passed",
    "- [Stop Hook Review] audit passed",
  ]) {
    const output = runHook("Stop", fixture.project, {
      last_assistant_message: marker,
      stop_hook_active: false,
    });
    assertWarningOutput(output, marker);
    assert.match(output.systemMessage, /did not request another model turn/);
  }
  cleanup(fixture.temporary);
});

test("long prompt, Stop text, and structured tool fields are position invariant below the input cap", () => {
  const fixture = createProject({ state: stateAtStage("experiment_results") });
  const padding = "x".repeat(300 * 1024);
  const contradiction = "Current stage: revision";
  for (const message of [
    `${contradiction}\n${padding}`,
    `${padding.slice(0, padding.length / 2)}\n${contradiction}\n${padding.slice(padding.length / 2)}`,
    `${padding}\n${contradiction}`,
  ]) {
    assertBlockOutput(runHook("Stop", fixture.project, {
      last_assistant_message: message,
      stop_hook_active: false,
    }), "long Stop position");
  }

  const codePrompt = "Refactor the parser code and run unit tests.";
  const researchPrompt = "Verify experiment results and audit the research claims.";
  for (const prompt of [
    `${researchPrompt}\n${padding}\n${codePrompt}`,
    `${codePrompt}\n${padding.slice(0, padding.length / 2)}\n${researchPrompt}\n${padding.slice(padding.length / 2)}`,
    `${codePrompt}\n${padding}\n${researchPrompt}`,
  ]) {
    const output = runHook("UserPromptSubmit", fixture.project, { prompt });
    assert.equal(output.hookSpecificOutput.hookEventName, "UserPromptSubmit");
    assert.match(output.hookSpecificOutput.additionalContext, /Canonical semantic audit/);
  }

  const large = "x".repeat(70 * 1024);
  for (const tool_input of [
    { subject: "Revised manuscript submission", body: "Please submit the paper.", padding: large },
    { padding: large, subject: "Revised manuscript submission", body: "Please submit the paper." },
    { subject: "Revised manuscript submission", padding: large, body: "Please submit the paper." },
  ]) {
    const output = runHook("PreToolUse", fixture.project, {
      tool_name: "mcp__gmail__send_email",
      tool_input,
    });
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
  }
  cleanup(fixture.temporary);
});

test("Hook input and state size limits have explicit MAX-1 MAX and MAX+1 behavior", () => {
  const limit = 8 * 1024 * 1024;
  const fixture = createProject();
  const dangerous = officialInput("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "rm -rf results" },
  });
  for (const target of [limit - 1, limit]) {
    const output = runRaw("PreToolUse", fixture.project, serializedObjectAtSize(dangerous, target));
    assert.equal(output.hookSpecificOutput.permissionDecision, "deny", `stdin size ${target}`);

    const stopInput = officialInput("Stop", fixture.project, { stop_hook_active: false });
    const stopRaw = serializedStringFieldAtSize(
      stopInput,
      "last_assistant_message",
      "\nCurrent stage: revision",
      target,
    );
    assertBlockOutput(runRaw("Stop", fixture.project, stopRaw), `Stop stdin size ${target}`);

    const promptInput = officialInput("UserPromptSubmit", fixture.project);
    const promptRaw = serializedStringFieldAtSize(
      promptInput,
      "prompt",
      "\nVerify experiment results and audit the research claims.",
      target,
    );
    const promptOutput = runRaw("UserPromptSubmit", fixture.project, promptRaw);
    assert.equal(
      promptOutput.hookSpecificOutput.hookEventName,
      "UserPromptSubmit",
      `prompt stdin size ${target}`,
    );
  }
  assert.deepEqual(
    runRaw("PreToolUse", fixture.project, serializedObjectAtSize(dangerous, limit + 1)),
    {},
    "an over-limit envelope is intentionally treated as malformed input",
  );
  const serializedDangerous = JSON.stringify(dangerous);
  const trailingWhitespaceOverflow = `${serializedDangerous}${
    " ".repeat(limit + 1 - Buffer.byteLength(serializedDangerous, "utf8"))
  }`;
  assert.deepEqual(
    runRaw("PreToolUse", fixture.project, trailingWhitespaceOverflow),
    {},
    "a complete JSON prefix plus over-limit whitespace is still rejected",
  );
  const unicodeAtLimit = serializedObjectAtByteSize(dangerous, limit);
  assert.equal(
    runRaw("PreToolUse", fixture.project, unicodeAtLimit)
      .hookSpecificOutput.permissionDecision,
    "deny",
    "the stdin limit is measured in UTF-8 bytes",
  );
  assert.deepEqual(
    runRaw("PreToolUse", fixture.project, serializedObjectAtByteSize(dangerous, limit + 1)),
    {},
    "a non-ASCII envelope over the UTF-8 byte limit is rejected",
  );
  cleanup(fixture.temporary);

  for (const target of [limit - 1, limit, limit + 1]) {
    const stateFixture = createProject({
      rawState: serializedObjectAtSize(makeState(), target),
    });
    const output = runHook("SessionStart", stateFixture.project);
    if (target <= limit) {
      assert.equal(output.hookSpecificOutput.hookEventName, "SessionStart", `state size ${target}`);
    } else {
      assert.deepEqual(output, {}, "an over-limit state is outside the active-project boundary");
    }
    cleanup(stateFixture.temporary);
  }
});

test("malformed envelopes and unsupported events always emit exactly one no-op JSON object", () => {
  const fixture = createProject();
  for (const raw of [
    "",
    "   \n",
    "null",
    "[]",
    "42",
    "\"text\"",
    "{not-json",
    "{}{}",
  ]) {
    assert.deepEqual(runRaw("Stop", fixture.project, raw), {}, JSON.stringify(raw));
  }
  assert.deepEqual(runRaw("UnknownEvent", fixture.project, JSON.stringify({
    cwd: fixture.project,
    hook_event_name: "UnknownEvent",
  })), {});
  assert.deepEqual(runRaw("SessionStart", fixture.project, "\uFEFF{}"), {});

  const bomInput = `\uFEFF${JSON.stringify(officialInput("SessionStart", fixture.project))}`;
  const bomOutput = runRaw("SessionStart", fixture.project, bomInput);
  assert.equal(bomOutput.hookSpecificOutput.hookEventName, "SessionStart");
  cleanup(fixture.temporary);
});

test("all active Hook paths are read-only for both project and plugin data", () => {
  const fixture = createProject({ state: stateAtStage("experiment_results") });
  const pluginData = path.join(fixture.temporary, "plugin data");
  fs.mkdirSync(pluginData, { recursive: true });
  write(path.join(pluginData, "sentinel.txt"), "unchanged\n");
  const projectBefore = snapshotFiles(fixture.project);
  const pluginBefore = snapshotFiles(pluginData);
  const env = { PLUGIN_DATA: pluginData };

  runHook("SessionStart", fixture.project, {}, { env });
  runHook("UserPromptSubmit", fixture.project, { prompt: "Audit the experiment results." }, { env });
  runHook("PreToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "rm -rf results" },
  }, { env });
  runHook("PostToolUse", fixture.project, {
    tool_name: "Bash",
    tool_input: { command: "python3 scripts/researchctl.py status" },
    tool_response: { exit_code: 0 },
  }, { env });
  runHook("Stop", fixture.project, {
    last_assistant_message: "Current stage: revision",
    stop_hook_active: false,
  }, { env });

  assert.deepEqual(snapshotFiles(fixture.project), projectBefore);
  assert.deepEqual(snapshotFiles(pluginData), pluginBefore);
  cleanup(fixture.temporary);
});

test("Stop deduplicates repeated contradictions and keeps feedback bounded", () => {
  const fixture = createProject({ state: stateAtStage("experiment_results") });
  const message = Array.from({ length: 12000 }, () => (
    "Current stage: revision\nclaim_freeze: approved\nGate to exit: release"
  )).join("\n");
  const output = runHook("Stop", fixture.project, {
    last_assistant_message: message,
    stop_hook_active: false,
  });
  assertBlockOutput(output, "repeated contradictions");
  assert.equal((output.reason.match(/current_stage=revision/g) || []).length, 1);
  assert.equal((output.reason.match(/claim_freeze=approved/g) || []).length, 1);
  assert.equal((output.reason.match(/gate_to_exit=release/g) || []).length, 1);
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
  assert.deepEqual(stop, {});
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

test("Stop does not rerun semantic review for code repairs or material metrics", () => {
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
  assert.deepEqual(claim, {});
  const mixed = runHook("Stop", fixture.project, {
    last_assistant_message: "Fixed the analysis script bug; mAP improved by 2.3%, supporting the central claim.",
    stop_hook_active: false,
  });
  assert.deepEqual(mixed, {});
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
    assert.deepEqual(output, {}, message);
  }
  assert.deepEqual(runHook("Stop", fixture.project, {
    last_assistant_message: "Fixed the parser that incorrectly reported accuracy improved by 12%; tests pass.",
    stop_hook_active: false,
  }), {});
  cleanup(fixture.temporary);
});
