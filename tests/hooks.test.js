#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const root = path.join(__dirname, "..");
const hook = path.join(root, "hooks", "research-workflow-hook.js");

function runHook(event, cwd, input = {}) {
  const result = spawnSync(process.execPath, [hook, event], {
    cwd,
    input: JSON.stringify({ cwd, ...input }),
    encoding: "utf8",
    env: { ...process.env, PLUGIN_DATA: path.join(cwd, ".plugin-data") },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.notEqual(result.stdout.trim(), "", "hook must emit JSON");
  return JSON.parse(result.stdout);
}

function write(candidate, content) {
  fs.mkdirSync(path.dirname(candidate), { recursive: true });
  fs.writeFileSync(candidate, content, "utf8");
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

test("SessionStart injects parsed metadata and only explicitly active plans", () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "research-hook-"));
  const project = path.join(temporary, "project");
  const nested = path.join(project, "src", "module");
  fs.mkdirSync(path.join(project, ".git"), { recursive: true });
  fs.mkdirSync(nested, { recursive: true });
  write(
    path.join(project, ".research", "project-overview.md"),
    [
      "---",
      "derived_from_state_version: 7",
      "active_planning_tasks:",
      "  - task-alpha",
      "  - task-finished",
      "---",
      "# Project Overview",
      "OVERVIEW-PROSE-MUST-NOT-BE-INJECTED",
      "",
    ].join("\n"),
  );
  write(
    path.join(project, ".research", "project-state.yaml"),
    [
      "project_id: PROJECT-007",
      "current_stage: experiment",
      "gates:",
      "  idea_freeze:",
      "    status: approved",
      "    latest_decision_id: GATE-IDEA-007",
      "  method_experiment:",
      "    status: pending",
      "    latest_decision_id: null",
      "untrusted_note: STATE-PROSE-MUST-NOT-BE-INJECTED",
      "",
    ].join("\n"),
  );
  write(
    path.join(project, ".planning", "task-alpha", "task_plan.md"),
    "---\nstatus: in_progress\n---\n# Task Plan\nPLAN-PROSE-MUST-NOT-BE-INJECTED\n",
  );
  write(
    path.join(project, ".planning", "task-alpha", "findings.md"),
    "# Findings\n",
  );
  write(
    path.join(project, ".planning", "task-alpha", "progress.md"),
    "# Progress\n",
  );
  write(
    path.join(project, ".planning", "task-finished", "task_plan.md"),
    "---\nstatus: completed\n---\nCOMPLETED-PLAN-MUST-NOT-LOAD\n",
  );
  write(
    path.join(project, ".planning", "task-unlisted", "task_plan.md"),
    "---\nstatus: in_progress\n---\nUNLISTED-PLAN-MUST-NOT-LOAD\n",
  );

  const before = snapshotFiles(project);
  const output = runHook("SessionStart", nested);
  const context = output.hookSpecificOutput.additionalContext;

  assert.equal(output.systemMessage, "SCIENTIFIC-RESEARCH-WORKFLOW:DEFAULT");
  assert.equal(output.hookSpecificOutput.hookEventName, "SessionStart");
  assert.match(context, /Project ID: PROJECT-007/);
  assert.match(context, /Current stage: experiment/);
  assert.match(context, /idea_freeze=approved \(decision=GATE-IDEA-007\)/);
  assert.match(context, /derived from state version 7/);
  assert.equal(context.includes("OVERVIEW-PROSE-MUST-NOT-BE-INJECTED"), false);
  assert.equal(context.includes("STATE-PROSE-MUST-NOT-BE-INJECTED"), false);
  assert.equal(context.includes("PLAN-PROSE-MUST-NOT-BE-INJECTED"), false);
  assert.equal(context.includes("COMPLETED-PLAN-MUST-NOT-LOAD"), false);
  assert.equal(context.includes("task-finished"), false);
  assert.equal(context.includes("task-unlisted"), false);
  assert.match(context, /\.planning\/task-alpha/);
  assert.match(context, /sole scientific Gate authority/);
  assert.match(context, /not scientific evidence/);
  assert.equal(context.includes(`Research project root: ${project}`), true);

  const after = snapshotFiles(project);
  assert.deepEqual(after, before, "hook must not create or mutate project files");
  fs.rmSync(temporary, { recursive: true, force: true });
});

test("UserPromptSubmit emits a lightweight mandatory boundary reminder", () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "research-hook-"));
  const project = path.join(temporary, "project");
  fs.mkdirSync(path.join(project, ".git"), { recursive: true });
  write(
    path.join(project, ".research", "project-overview.md"),
    "---\nderived_from_state_version: 2\nactive_planning_tasks: []\n---\n# Project Overview\nDO-NOT-COPY-FULL-OVERVIEW\n",
  );
  write(
    path.join(project, ".research", "project-state.yaml"),
    "current_stage: literature\n",
  );

  const output = runHook("UserPromptSubmit", project, {
    prompt: "Design and run a multi-stage experiment.",
  });
  const context = output.hookSpecificOutput.additionalContext;
  assert.equal(output.hookSpecificOutput.hookEventName, "UserPromptSubmit");
  assert.match(context, /classify it as trivial or non-trivial/);
  assert.match(context, /initialize\/reuse the planning bundle/);
  assert.equal(context.includes("DO-NOT-COPY-FULL-OVERVIEW"), false);
  fs.rmSync(temporary, { recursive: true, force: true });
});

test("ordinary repositories are not opted into the research hook", () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "research-hook-"));
  const project = path.join(temporary, "ordinary-code");
  fs.mkdirSync(path.join(project, ".git"), { recursive: true });
  write(path.join(project, "AGENTS.md"), "# Ordinary project\n");

  const output = runHook("SessionStart", project);
  assert.deepEqual(output, {});
  fs.rmSync(temporary, { recursive: true, force: true });
});

test("hook config registers the two shared command handlers", () => {
  const document = JSON.parse(
    fs.readFileSync(path.join(root, "hooks", "hooks.json"), "utf8"),
  );
  assert.deepEqual(
    Object.keys(document.hooks).sort(),
    ["SessionStart", "UserPromptSubmit"],
  );
  for (const [event, groups] of Object.entries(document.hooks)) {
    assert.equal(groups.length, 1, event);
    assert.equal(groups[0].hooks.length, 1, event);
    const handler = groups[0].hooks[0];
    assert.equal(handler.type, "command");
    assert.equal(handler.timeout, 5);
    assert.match(handler.command, /research-workflow-hook\.js/);
    assert.match(handler.commandWindows, /research-workflow-hook\.js/);
  }
});
