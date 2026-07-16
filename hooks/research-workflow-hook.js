#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const SUPPORTED_EVENTS = new Set([
  "SessionStart",
  "UserPromptSubmit",
  "PreToolUse",
  "PostToolUse",
  "Stop",
]);
const MAX_INPUT_BYTES = 8 * 1024 * 1024;
const MAX_POLICY_BYTES = 256 * 1024;
const MAX_STATE_BYTES = 8 * 1024 * 1024;
const MAX_SESSION_CONTEXT_CHARS = 800;
const MAX_PROMPT_CONTEXT_CHARS = 400;
const MAX_POST_CONTEXT_CHARS = 4200;
const MAX_STOP_REASON_CHARS = 1800;
const MAX_TOOL_TEXT_CHARS = MAX_INPUT_BYTES;
function readStdin() {
  return new Promise((resolve) => {
    const chunks = [];
    let inputBytes = 0;
    let overflow = false;
    process.stdin.on("data", (chunk) => {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      inputBytes += buffer.length;
      if (inputBytes > MAX_INPUT_BYTES) {
        overflow = true;
        return;
      }
      chunks.push(buffer);
    });
    process.stdin.on("end", () => resolve(
      overflow ? "" : Buffer.concat(chunks, inputBytes).toString("utf8"),
    ));
    process.stdin.on("error", () => resolve(""));
  });
}

function parseJsonWithoutDuplicateKeys(raw) {
  const source = raw.replace(/^\uFEFF/, "");
  let cursor = 0;
  const fail = () => { throw new SyntaxError("invalid or duplicate-key JSON"); };
  const skipWhitespace = () => {
    while (cursor < source.length && /[\t\n\r ]/.test(source[cursor])) cursor += 1;
  };
  const parseString = () => {
    if (source[cursor] !== '"') fail();
    const start = cursor;
    cursor += 1;
    while (cursor < source.length) {
      const character = source[cursor];
      if (character === '"') {
        cursor += 1;
        return JSON.parse(source.slice(start, cursor));
      }
      if (character === "\\") {
        cursor += 1;
        if (cursor >= source.length) fail();
        if (source[cursor] === "u") {
          if (!/^[0-9a-fA-F]{4}$/.test(source.slice(cursor + 1, cursor + 5))) fail();
          cursor += 5;
          continue;
        }
        if (!/["\\/bfnrt]/.test(source[cursor])) fail();
        cursor += 1;
        continue;
      }
      if (character.charCodeAt(0) < 0x20) fail();
      cursor += 1;
    }
    fail();
    return "";
  };
  const parseValue = () => {
    skipWhitespace();
    const character = source[cursor];
    if (character === "{") {
      cursor += 1;
      skipWhitespace();
      const keys = new Set();
      if (source[cursor] === "}") {
        cursor += 1;
        return;
      }
      while (cursor < source.length) {
        skipWhitespace();
        const key = parseString();
        if (keys.has(key)) fail();
        keys.add(key);
        skipWhitespace();
        if (source[cursor] !== ":") fail();
        cursor += 1;
        parseValue();
        skipWhitespace();
        if (source[cursor] === "}") {
          cursor += 1;
          return;
        }
        if (source[cursor] !== ",") fail();
        cursor += 1;
      }
      fail();
      return;
    }
    if (character === "[") {
      cursor += 1;
      skipWhitespace();
      if (source[cursor] === "]") {
        cursor += 1;
        return;
      }
      while (cursor < source.length) {
        parseValue();
        skipWhitespace();
        if (source[cursor] === "]") {
          cursor += 1;
          return;
        }
        if (source[cursor] !== ",") fail();
        cursor += 1;
      }
      fail();
      return;
    }
    if (character === '"') {
      parseString();
      return;
    }
    for (const literal of ["true", "false", "null"]) {
      if (source.startsWith(literal, cursor)) {
        cursor += literal.length;
        return;
      }
    }
    const number = source.slice(cursor).match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/);
    if (!number) fail();
    cursor += number[0].length;
  };
  parseValue();
  skipWhitespace();
  if (cursor !== source.length) fail();
  return JSON.parse(source);
}

function parseObject(raw) {
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    const value = parseJsonWithoutDuplicateKeys(raw);
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch (_error) {
    return null;
  }
}

function isFile(candidate) {
  try {
    return fs.statSync(candidate).isFile();
  } catch (_error) {
    return false;
  }
}

function samePhysicalFile(left, right) {
  try {
    const leftStat = fs.statSync(left);
    const rightStat = fs.statSync(right);
    return leftStat.dev === rightStat.dev && leftStat.ino === rightStat.ino;
  } catch (_error) {
    return false;
  }
}

function safeReadText(candidate, maxBytes) {
  try {
    const stats = fs.statSync(candidate);
    if (!stats.isFile() || stats.size > maxBytes) return null;
    return fs.readFileSync(candidate, "utf8").slice(0, maxBytes);
  } catch (_error) {
    return null;
  }
}

function safeReadObject(candidate, maxBytes = MAX_INPUT_BYTES) {
  const text = safeReadText(candidate, maxBytes);
  return text === null ? null : parseObject(text);
}

function cleanText(value) {
  return String(value ?? "")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "?")
    .replace(/\r\n?|[\u0085\u2028\u2029]/g, "\n");
}

function bounded(value, maxChars) {
  const text = cleanText(value).trim();
  if (text.length <= maxChars) return text;
  const marker = "\n... [bounded by research hook] ...\n";
  const available = Math.max(0, maxChars - marker.length);
  const head = Math.ceil(available * 0.62);
  const tail = available - head;
  return `${text.slice(0, head)}${marker}${text.slice(text.length - tail)}`;
}

function scalar(value, fallback = "unset", maxChars = 180) {
  if (typeof value !== "string" && typeof value !== "number") return fallback;
  const text = cleanText(value).replace(/\s+/g, " ").trim();
  return text ? bounded(text, maxChars) : fallback;
}

function firstDefined(object, keys) {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(object, key)) return object[key];
  }
  return undefined;
}

function inputCwd(input) {
  const value = firstDefined(input, ["cwd", "working_directory", "workingDirectory"]);
  return typeof value === "string" && value.trim() ? value : null;
}

function eventName(input) {
  const fromInput = firstDefined(input, ["hook_event_name", "hookEventName", "event_name"]);
  const fromArg = process.argv[2];
  const inputEvent = typeof fromInput === "string" && SUPPORTED_EVENTS.has(fromInput)
    ? fromInput
    : null;
  const argumentEvent = SUPPORTED_EVENTS.has(fromArg) ? fromArg : null;
  if (inputEvent && argumentEvent && inputEvent !== argumentEvent) return null;
  return inputEvent || argumentEvent;
}

function findResearchRoot(start) {
  if (typeof start !== "string" || !start.trim()) return null;
  let current;
  try {
    const resolved = path.resolve(start);
    current = fs.realpathSync.native(resolved);
  } catch (_error) {
    return null;
  }
  const filesystemRoot = path.parse(current).root;
  while (true) {
    if (isFile(path.join(current, ".research", "state.json"))) return current;
    // Do not inherit research state from outside the current Git worktree. This
    // keeps an ordinary nested repository from being activated by an unrelated
    // parent project while still allowing subdirectories of the active project.
    if (fs.existsSync(path.join(current, ".git"))) return null;
    if (current === filesystemRoot) return null;
    current = path.dirname(current);
  }
}

function pluginRoot() {
  for (const name of ["PLUGIN_ROOT", "CODEX_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"]) {
    const value = process.env[name];
    if (typeof value === "string" && value.trim()) return path.resolve(value);
  }
  return path.resolve(__dirname, "..");
}

function uniqueStringList(value) {
  return Array.isArray(value) && value.length > 0
    && value.every((item) => typeof item === "string" && item.trim())
    && new Set(value).size === value.length;
}

function optionalUniqueStringList(value) {
  return Array.isArray(value)
    && value.every((item) => typeof item === "string" && item.trim())
    && new Set(value).size === value.length;
}

function artifactRoleReference(value, stageIds) {
  const match = typeof value === "string"
    ? /^([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)$/.exec(value)
    : null;
  return match !== null && stageIds.includes(match[1]);
}

function validArtifactContract(contract, stageIds) {
  if (!plainObject(contract)
    || !uniqueStringList(contract.required_artifact_roles)
    || contract.required_artifact_roles.some(
      (role) => !artifactRoleReference(role, stageIds),
    )) return false;
  const mutable = contract.mutable_after_approval_roles ?? [];
  return optionalUniqueStringList(mutable)
    && mutable.every((role) => artifactRoleReference(role, stageIds))
    && mutable.every((role) => contract.required_artifact_roles.includes(role));
}

const RETROSPECTIVE_MODE_MARKERS = [
  "cli_flag", "eligibility", "claim_scope", "waivable_historical_roles",
];
const RESERVED_GATE_CLI_FLAGS = new Set([
  "--help",
  "--reason",
  "--supporting-evidence-id",
  "--opposing-evidence-id",
  "--unresolved-risk",
  "--decision-condition",
  "--target",
  "--selected-id",
  "--approval-mode",
]);

function sameFieldSet(value, expected) {
  return plainObject(value)
    && Object.keys(value).sort().join("\0") === [...expected].sort().join("\0");
}

function sameStringSet(value, expected) {
  return uniqueStringList(value)
    && [...value].sort().join("\0") === [...expected].sort().join("\0");
}

function retrospectiveApprovalMode(spec) {
  const modes = plainObject(spec) ? spec.approval_modes : null;
  if (!plainObject(modes)) return null;
  const matches = Object.entries(modes).filter(([mode, contract]) => (
    mode !== spec.default_approval_mode
    && plainObject(contract)
    && RETROSPECTIVE_MODE_MARKERS.every((field) => (
      Object.prototype.hasOwnProperty.call(contract, field)
    ))
  ));
  return matches.length === 1 ? matches[0][0] : null;
}

function loadRuntimeContract() {
  const candidate = path.join(
    pluginRoot(), "skills", "research", "assets", "runtime-contract.json",
  );
  const runtime = safeReadObject(candidate, MAX_POLICY_BYTES);
  if (!sameFieldSet(runtime, [
    "contract_version", "state_schema_version", "state", "decision", "lifecycle",
    "activation", "gate", "artifact", "checkpoint", "stage_transition",
    "scientific_record", "adapter_exchange",
  ]) || runtime.contract_version !== "2.0"
    || typeof runtime.state_schema_version !== "string"
    || !runtime.state_schema_version.trim()) return null;
  const requiredLists = {
    state: ["required_fields"],
    decision: ["required_fields"],
    lifecycle: [
      "statuses", "actions", "record_fields", "decision_fields",
      "decision_optional_fields",
    ],
    activation: ["actions", "event_fields"],
    gate: [
      "statuses", "actions", "record_fields", "target_container_fields",
      "decision_optional_fields", "cascade_fields", "gate_ref_required_fields",
      "gate_ref_optional_fields", "selection_fields",
    ],
    artifact: ["entry_fields", "revision_fields", "reference_prefix_fields"],
    checkpoint: ["fields"],
    stage_transition: ["fields", "trigger_prefixes"],
  };
  for (const [sectionName, fieldNames] of Object.entries(requiredLists)) {
    const section = runtime[sectionName];
    if (!sameFieldSet(section, fieldNames)
      || fieldNames.some((field) => !uniqueStringList(section[field]))) {
      return null;
    }
  }
  const scientificRecord = runtime.scientific_record;
  const scientificRecordLists = [
    "manifest_fields", "record_fields", "source_fields", "relation_fields",
    "record_kinds", "relation_kinds",
  ];
  if (!sameFieldSet(scientificRecord, [
    "manifest_schema_version", "artifact_role", ...scientificRecordLists,
    "relation_signatures",
  ])
    || typeof scientificRecord.manifest_schema_version !== "string"
    || !scientificRecord.manifest_schema_version.trim()
    || typeof scientificRecord.artifact_role !== "string"
    || !/^[a-z][a-z0-9_]*$/.test(scientificRecord.artifact_role)
    || scientificRecordLists.some(
      (field) => !uniqueStringList(scientificRecord[field]),
    )) return null;
  const relationSignatures = scientificRecord.relation_signatures;
  if (!sameFieldSet(relationSignatures, scientificRecord.relation_kinds)) return null;
  for (const relationKind of scientificRecord.relation_kinds) {
    const signature = relationSignatures[relationKind];
    if (!sameFieldSet(signature, ["source_kinds", "target_kinds"])
      || !uniqueStringList(signature.source_kinds)
      || !uniqueStringList(signature.target_kinds)
      || [...signature.source_kinds, ...signature.target_kinds].some(
        (kind) => !scientificRecord.record_kinds.includes(kind),
      )) return null;
  }
  const adapterExchange = runtime.adapter_exchange;
  const adapterExchangeLists = [
    "manifest_fields", "request_fields", "payload_fields", "gate_binding_fields",
    "human_authorization_fields", "retry_policy_fields", "receipt_fields",
    "adapter_fields", "verification_fields", "operation_kinds", "effect_classes",
    "retry_modes", "receipt_statuses",
  ];
  if (!sameFieldSet(adapterExchange, [
    "manifest_schema_version", "protocol_version", "artifact_role",
    ...adapterExchangeLists,
  ])
    || typeof adapterExchange.manifest_schema_version !== "string"
    || !adapterExchange.manifest_schema_version.trim()
    || typeof adapterExchange.protocol_version !== "string"
    || !adapterExchange.protocol_version.trim()
    || typeof adapterExchange.artifact_role !== "string"
    || !/^[a-z][a-z0-9_]*$/.test(adapterExchange.artifact_role)
    || adapterExchange.artifact_role === scientificRecord.artifact_role
    || adapterExchangeLists.some(
      (field) => !uniqueStringList(adapterExchange[field]),
    )) return null;
  const disjoint = (left, right) => !left.some((field) => right.includes(field));
  for (const [left, right] of [
    [runtime.decision.required_fields, runtime.lifecycle.decision_fields],
    [runtime.decision.required_fields, runtime.lifecycle.decision_optional_fields],
    [runtime.lifecycle.decision_fields, runtime.lifecycle.decision_optional_fields],
    [runtime.decision.required_fields, runtime.gate.decision_optional_fields],
    [runtime.gate.record_fields, runtime.gate.target_container_fields],
    [runtime.gate.gate_ref_required_fields, runtime.gate.gate_ref_optional_fields],
    [runtime.artifact.reference_prefix_fields, runtime.artifact.revision_fields],
  ]) {
    if (!disjoint(left, right)) return null;
  }
  const fixedV2Fields = [
    [runtime.state.required_fields, [
      "schema_version", "workflow_version", "enabled", "project_id", "project_name",
      "current_stage", "lifecycle", "activation_history", "gates", "artifacts",
      "last_checkpoint", "stage_history", "created_at", "updated_at",
    ]],
    [runtime.decision.required_fields, [
      "decision_id", "action", "previous_status", "new_status", "reason", "actor",
      "decided_at", "artifact_refs", "supporting_evidence_ids",
      "opposing_evidence_ids", "unresolved_risks", "decision_conditions",
    ]],
    [runtime.lifecycle.record_fields, ["status", "latest_decision_id", "history"]],
    [runtime.lifecycle.decision_fields, ["stage"]],
    [runtime.activation.event_fields, [
      "action", "previous_enabled", "new_enabled", "reason", "actor", "decided_at",
    ]],
    [runtime.gate.record_fields, ["status", "latest_decision_id", "history"]],
    [runtime.gate.target_container_fields, ["targets"]],
    [runtime.gate.cascade_fields, [
      "upstream_gate_ref", "upstream_decision_id", "upstream_reason",
    ]],
    [runtime.gate.gate_ref_required_fields, ["gate"]],
    [runtime.gate.gate_ref_optional_fields, ["target"]],
    [runtime.gate.selection_fields, ["selected_id", "artifact_ref"]],
    [runtime.artifact.entry_fields, ["current_revision", "revisions"]],
    [runtime.artifact.revision_fields, [
      "revision", "source_path", "snapshot_path", "content_hash", "size_bytes",
      "registered_at",
    ]],
    [runtime.artifact.reference_prefix_fields, ["label", "artifact_id"]],
    [runtime.checkpoint.fields, ["summary", "timestamp"]],
    [runtime.stage_transition.fields, [
      "from_stage", "to_stage", "trigger", "timestamp",
    ]],
    [runtime.scientific_record.manifest_fields, [
      "schema_version", "stage", "records",
    ]],
    [runtime.scientific_record.record_fields, [
      "record_id", "record_kind", "source", "supersedes", "relations",
    ]],
    [runtime.scientific_record.source_fields, [
      "artifact_role", "artifact_id", "revision", "locator",
    ]],
    [runtime.scientific_record.relation_fields, ["relation", "target_id"]],
    [runtime.adapter_exchange.manifest_fields, [
      "schema_version", "stage", "requests", "receipts",
    ]],
    [runtime.adapter_exchange.request_fields, [
      "request_id", "operation_kind", "created_at", "gate_binding", "payload",
      "input_artifact_refs", "effect_class", "human_authorization", "retry_policy",
    ]],
    [runtime.adapter_exchange.payload_fields, ["artifact_ref", "locator"]],
    [runtime.adapter_exchange.gate_binding_fields, [
      "gate_ref", "gate_decision_id", "artifact_refs",
    ]],
    [runtime.adapter_exchange.human_authorization_fields, [
      "authorization_id", "actor", "authorized_at", "scope",
    ]],
    [runtime.adapter_exchange.retry_policy_fields, [
      "mode", "max_attempts", "idempotency_key",
    ]],
    [runtime.adapter_exchange.receipt_fields, [
      "receipt_id", "request_id", "request_hash", "attempt_id",
      "retry_of_attempt_id", "supersedes", "adapter", "status", "observed_at",
      "external_id", "output_artifact_refs", "log_artifact_refs", "message",
    ]],
    [runtime.adapter_exchange.adapter_fields, [
      "adapter_id", "adapter_version", "protocol_version",
    ]],
    [runtime.adapter_exchange.verification_fields, [
      "schema_version", "verification", "verified_at", "request_hash", "attempt_id",
      "retry_of_attempt_id", "request",
    ]],
  ];
  if (fixedV2Fields.some(([value, expected]) => !sameStringSet(value, expected))) {
    return null;
  }
  for (const [value, required] of [
    [runtime.lifecycle.decision_optional_fields, ["gate_ref", "gate_decision_id"]],
    [runtime.gate.decision_optional_fields, [
      "approval_mode", "waived_artifact_roles", "selection", "cascade",
    ]],
    [runtime.scientific_record.record_kinds, [
      "candidate", "search_run", "passage_evidence", "experiment", "attempt",
      "analysis", "claim", "paper_location", "review_concern",
    ]],
    [runtime.scientific_record.relation_kinds, [
      "derived_from", "discovered_by", "supports", "contradicts", "qualifies",
      "tests", "attempt_of", "analyzes", "expresses", "addresses",
    ]],
  ]) {
    if (required.some((field) => !value.includes(field))) return null;
  }
  for (const [value, expected] of [
    [runtime.lifecycle.statuses, ["active", "terminated", "completed"]],
    [runtime.lifecycle.actions, ["terminate", "complete", "reopen"]],
    [runtime.activation.actions, ["enable", "disable"]],
    [runtime.gate.statuses, ["pending", "approved", "reopened"]],
    [runtime.gate.actions, ["approve", "reopen"]],
    [runtime.stage_transition.trigger_prefixes, [
      "checkpoint", "gate-approve", "gate-reopen",
    ]],
    [runtime.adapter_exchange.operation_kinds, [
      "evidence_retrieval", "experiment_execution", "result_import",
      "paper_production", "external_release",
    ]],
    [runtime.adapter_exchange.effect_classes, [
      "low_risk", "costly_compute", "destructive", "safety_relevant",
      "external_release",
    ]],
    [runtime.adapter_exchange.retry_modes, [
      "never", "idempotent", "reconcile_before_retry",
    ]],
    [runtime.adapter_exchange.receipt_statuses, [
      "accepted", "running", "succeeded", "failed", "cancelled", "unknown",
    ]],
  ]) {
    if (!sameStringSet(value, expected)) return null;
  }
  return runtime;
}

function loadPolicy(runtime) {
  const candidate = path.join(
    pluginRoot(),
    "skills",
    "research",
    "references",
    "policy.yaml",
  );
  const policy = safeReadObject(candidate, MAX_POLICY_BYTES);
  if (!sameFieldSet(policy, [
    "schema_version", "workflow_version", "workflow_graph",
    "artifact_role_cardinality_default", "artifact_layout", "review_language",
    "workspace_lifecycle", "authority_boundary", "adapter_authority", "gates", "stages",
    "global_prohibited_actions", "semantic_audit",
  ]) || !plainObject(runtime)
    || policy.schema_version !== runtime.state_schema_version
    || typeof policy.workflow_version !== "string" || !policy.workflow_version.trim()) return null;
  const reviewLanguage = policy.review_language;
  if (!sameFieldSet(reviewLanguage, [
    "internal_review_default", "formal_output_default", "instruction",
  ]) || Object.values(reviewLanguage).some(
    (value) => typeof value !== "string" || !value.trim(),
  )) return null;
  const workspaceLifecycle = policy.workspace_lifecycle;
  if (!sameFieldSet(workspaceLifecycle, [
    "scope", "mainline_identity", "decision_review", "termination", "completion",
    "terminal_access", "reopen", "inactivity", "activation", "cross_workspace_reuse",
  ]) || Object.values(workspaceLifecycle).some(
    (value) => typeof value !== "string" || !value.trim(),
  )) return null;
  if (!uniqueStringList(policy.authority_boundary)
    || !uniqueStringList(policy.global_prohibited_actions)
    || !uniqueStringList(policy.semantic_audit)) return null;
  const graph = policy.workflow_graph;
  if (!plainObject(graph)
    || Object.keys(graph).sort().join("\0") !== [
      "stage_exit_requirements", "stage_order", "stage_transitions",
    ].join("\0")) return null;
  const stageIds = graph.stage_order;
  if (!Array.isArray(stageIds) || !stageIds.length
    || stageIds.some((stage) => typeof stage !== "string" || !stage)
    || new Set(stageIds).size !== stageIds.length) return null;
  if (!plainObject(graph.stage_exit_requirements)
    || Object.keys(graph.stage_exit_requirements).length !== stageIds.length
    || stageIds.some((stage) => !Object.prototype.hasOwnProperty.call(
      graph.stage_exit_requirements, stage,
    ))) return null;
  if (!plainObject(graph.stage_transitions)
    || Object.keys(graph.stage_transitions).length !== stageIds.length
    || stageIds.some((stage) => !Array.isArray(graph.stage_transitions[stage]))) return null;
  if (!plainObject(policy.stages)
    || Object.keys(policy.stages).length !== stageIds.length
    || stageIds.some((stage) => !plainObject(policy.stages[stage]))) return null;
  const stageFields = [
    "label", "reference", "required_inputs", "allowed_actions", "required_evidence",
    "exit_criteria", "prohibited_actions",
  ];
  const references = [];
  for (const stage of stageIds) {
    const spec = policy.stages[stage];
    if (!sameFieldSet(spec, stageFields)
      || typeof spec.label !== "string" || !spec.label.trim()
      || typeof spec.reference !== "string"
      || !/^\d{2}-[a-z0-9-]+\.md$/.test(spec.reference)
      || stageFields.slice(2).some((field) => !uniqueStringList(spec[field]))) {
      return null;
    }
    references.push(spec.reference);
  }
  if (new Set(references).size !== references.length) return null;
  if (!plainObject(policy.gates)) return null;

  const refs = [];
  const seenRefs = new Set();
  for (const stage of stageIds) {
    const requirement = graph.stage_exit_requirements[stage];
    if (requirement === null) continue;
    if (!plainObject(requirement)
      || !(["gate"].join("\0") === Object.keys(requirement).sort().join("\0")
        || ["gate", "target"].join("\0") === Object.keys(requirement).sort().join("\0"))
      || typeof requirement.gate !== "string" || !requirement.gate) return null;
    const target = Object.prototype.hasOwnProperty.call(requirement, "target")
      ? requirement.target : null;
    if (target !== null && (typeof target !== "string" || !target)) return null;
    const key = `${requirement.gate}\0${target ?? ""}`;
    if (seenRefs.has(key)) return null;
    seenRefs.add(key);
    refs.push({ gate: requirement.gate, target, stage });
  }
  if (!refs.length) return null;
  const gateIds = [...new Set(refs.map((reference) => reference.gate))];
  if (Object.keys(policy.gates).length !== gateIds.length
    || gateIds.some((gate) => !plainObject(policy.gates[gate]))) return null;
  let retrospectiveModeCount = 0;
  for (const gate of gateIds) {
    const spec = policy.gates[gate];
    if (!/^[a-z][a-z0-9_]*$/.test(gate)
      || typeof spec.label !== "string" || !spec.label.trim()
      || !uniqueStringList(spec.reopen_when_changed)) return null;
    const targets = spec.approval_targets;
    const modes = spec.approval_modes;
    const hasTargets = Object.prototype.hasOwnProperty.call(spec, "approval_targets");
    const hasModes = Object.prototype.hasOwnProperty.call(spec, "approval_modes");
    if (hasTargets && hasModes) return null;
    const ownedTargets = refs.filter((reference) => reference.gate === gate)
      .map((reference) => reference.target);
    let contracts;
    if (hasTargets) {
      if (!sameFieldSet(spec, [
        "label", "reopen_when_changed", "approval_targets", "approval_requires",
      ]) || !uniqueStringList(spec.approval_requires) || !plainObject(targets)) return null;
      if (ownedTargets.some((target) => target === null)
        || Object.keys(targets).length !== ownedTargets.length
        || ownedTargets.some((target) => !plainObject(targets[target]))) return null;
      for (const [target, contract] of Object.entries(targets)) {
        const fields = plainObject(contract) ? Object.keys(contract) : [];
        if (!/^[a-z][a-z0-9_]*$/.test(target)
          || !fields.includes("required_artifact_roles")
          || fields.some((field) => ![
            "required_artifact_roles", "mutable_after_approval_roles",
          ].includes(field))) return null;
      }
      contracts = Object.values(targets);
    } else if (hasModes) {
      if (!sameFieldSet(spec, [
        "label", "reopen_when_changed", "approval_modes", "default_approval_mode",
      ]) || !plainObject(modes)
        || typeof spec.default_approval_mode !== "string"
        || !plainObject(modes[spec.default_approval_mode])
        || ownedTargets.length !== 1 || ownedTargets[0] !== null) return null;
      contracts = Object.values(modes);
      for (const [mode, contract] of Object.entries(modes)) {
        if (!/^[a-z][a-z0-9_]*$/.test(mode) || !plainObject(contract)) return null;
        const markers = RETROSPECTIVE_MODE_MARKERS.filter((field) => (
          Object.prototype.hasOwnProperty.call(contract, field)
        ));
        const allowed = new Set([
          "required_artifact_roles", "mutable_after_approval_roles", "approval_requires",
          ...(markers.length ? RETROSPECTIVE_MODE_MARKERS : []),
        ]);
        if (!Object.prototype.hasOwnProperty.call(contract, "required_artifact_roles")
          || !Object.prototype.hasOwnProperty.call(contract, "approval_requires")
          || Object.keys(contract).some((field) => !allowed.has(field))
          || !uniqueStringList(contract.approval_requires)) return null;
        if (!markers.length) continue;
        if (markers.length !== RETROSPECTIVE_MODE_MARKERS.length
          || mode === spec.default_approval_mode
          || !/^--[a-z][a-z0-9-]*$/.test(contract.cli_flag)
          || RESERVED_GATE_CLI_FLAGS.has(contract.cli_flag)
          || typeof contract.eligibility !== "string" || !contract.eligibility.trim()
          || typeof contract.claim_scope !== "string" || !contract.claim_scope.trim()
          || !uniqueStringList(contract.required_artifact_roles)
          || !optionalUniqueStringList(contract.waivable_historical_roles)
          || contract.waivable_historical_roles.some(
            (role) => !artifactRoleReference(role, stageIds)
              || contract.required_artifact_roles.includes(role),
          )) return null;
        retrospectiveModeCount += 1;
      }
    } else {
      const expectedFields = [
        "label", "reopen_when_changed", "required_artifact_roles", "approval_requires",
        ...(Object.prototype.hasOwnProperty.call(spec, "selection_artifact_role")
          ? ["selection_artifact_role"] : []),
      ];
      if (!sameFieldSet(spec, expectedFields)
        || !uniqueStringList(spec.approval_requires)
        || ownedTargets.length !== 1 || ownedTargets[0] !== null) return null;
      contracts = [spec];
    }
    if (contracts.some((contract) => !validArtifactContract(contract, stageIds))) {
      return null;
    }
    const selection = spec.selection_artifact_role;
    if (selection !== undefined
      && (hasTargets || hasModes
        || !artifactRoleReference(selection, stageIds)
        || !spec.required_artifact_roles.includes(selection))) return null;
  }
  if (retrospectiveModeCount !== 1) return null;
  const adapterAuthority = policy.adapter_authority;
  if (!sameFieldSet(adapterAuthority, [
    "human_authorization_effect_classes", "operation_kinds",
  ])
    || !sameStringSet(
      adapterAuthority.human_authorization_effect_classes,
      runtime.adapter_exchange.effect_classes.filter((effect) => effect !== "low_risk"),
    )
    || !sameFieldSet(
      adapterAuthority.operation_kinds,
      runtime.adapter_exchange.operation_kinds,
    )) return null;
  for (const operationKind of runtime.adapter_exchange.operation_kinds) {
    const operation = adapterAuthority.operation_kinds[operationKind];
    if (!sameFieldSet(operation, [
      "allowed_stages", "required_gate_refs", "allowed_effect_classes",
    ])
      || !uniqueStringList(operation.allowed_stages)
      || operation.allowed_stages.some((stage) => !stageIds.includes(stage))
      || !uniqueStringList(operation.allowed_effect_classes)
      || operation.allowed_effect_classes.some(
        (effect) => !runtime.adapter_exchange.effect_classes.includes(effect),
      )
      || !Array.isArray(operation.required_gate_refs)) return null;
    const operationRefKeys = [];
    for (const gateRef of operation.required_gate_refs) {
      if (!plainObject(gateRef)
        || !(sameFieldSet(gateRef, ["gate"]) || sameFieldSet(gateRef, ["gate", "target"]))
        || typeof gateRef.gate !== "string" || !gateRef.gate.trim()
        || (Object.prototype.hasOwnProperty.call(gateRef, "target")
          && (typeof gateRef.target !== "string" || !gateRef.target.trim()))) return null;
      const key = `${gateRef.gate}\0${gateRef.target ?? ""}`;
      if (!seenRefs.has(key) || operationRefKeys.includes(key)) return null;
      operationRefKeys.push(key);
    }
    if (operation.allowed_effect_classes.some((effect) => effect !== "low_risk")
      && !operationRefKeys.length) return null;
    if (operation.allowed_effect_classes.includes("external_release")
      && (!sameStringSet(operation.allowed_effect_classes, ["external_release"])
        || operation.required_gate_refs.some(
          (gateRef) => !Object.prototype.hasOwnProperty.call(gateRef, "target"),
        ))) return null;
  }
  for (const source of stageIds) {
    const destinations = new Set();
    for (const candidate of graph.stage_transitions[source]) {
      if (!plainObject(candidate)
        || Object.keys(candidate).sort().join("\0") !== "to\0trigger"
        || !stageIds.includes(candidate.to) || destinations.has(candidate.to)
        || !plainObject(candidate.trigger)) return null;
      destinations.add(candidate.to);
      if (candidate.trigger.type === "checkpoint") {
        if (Object.keys(candidate.trigger).length !== 1) return null;
      } else if (candidate.trigger.type === "stage_exit") {
        if (Object.keys(candidate.trigger).sort().join("\0") !== "stage\0type"
          || !stageIds.includes(candidate.trigger.stage)
          || graph.stage_exit_requirements[candidate.trigger.stage] === null) return null;
      } else return null;
    }
  }
  const artifactLayout = policy.artifact_layout;
  if (!sameFieldSet(artifactLayout, [
    "generated_root", "stage_path_template", "snapshot_root",
    "snapshot_stage_path_template", "instruction",
  ])) {
    return null;
  }
  if (typeof artifactLayout.generated_root !== "string" || !artifactLayout.generated_root) {
    return null;
  }
  const artifactRootParts = artifactLayout.generated_root.split("/");
  if (artifactRootParts[0] !== ".research"
    || artifactRootParts.some((part) => !part || part === "." || part === "..")) {
    return null;
  }
  if (typeof artifactLayout.stage_path_template !== "string"
    || artifactLayout.stage_path_template !== `${artifactLayout.generated_root}/<stage-id>`) {
    return null;
  }
  if (typeof artifactLayout.instruction !== "string"
    || !artifactLayout.instruction.includes(artifactLayout.stage_path_template)) {
    return null;
  }
  if (typeof artifactLayout.snapshot_root !== "string" || !artifactLayout.snapshot_root) {
    return null;
  }
  const snapshotRootParts = artifactLayout.snapshot_root.split("/");
  if (snapshotRootParts[0] !== ".research"
    || snapshotRootParts.some((part) => !part || part === "." || part === "..")) {
    return null;
  }
  const generatedRoot = artifactLayout.generated_root.replace(/\/+$/, "");
  const configuredSnapshotRoot = artifactLayout.snapshot_root.replace(/\/+$/, "");
  if (generatedRoot === configuredSnapshotRoot
    || generatedRoot.startsWith(`${configuredSnapshotRoot}/`)
    || configuredSnapshotRoot.startsWith(`${generatedRoot}/`)) return null;
  if (typeof artifactLayout.snapshot_stage_path_template !== "string"
    || artifactLayout.snapshot_stage_path_template !== `${artifactLayout.snapshot_root}/<stage-id>`) {
    return null;
  }
  if (policy.artifact_role_cardinality_default !== "one") return null;
  const releaseGates = gateIds.filter((gate) => {
    const spec = policy.gates[gate];
    return plainObject(spec) && plainObject(spec.approval_targets);
  });
  if (releaseGates.length !== 1) return null;
  return policy;
}

function stageOrder(policy) {
  return policy.workflow_graph.stage_order;
}

function approvalSequence(policy) {
  return stageOrder(policy).flatMap((stage) => {
    const requirement = policy.workflow_graph.stage_exit_requirements[stage];
    return plainObject(requirement)
      ? [{ gate: requirement.gate, target: requirement.target ?? null, stage }]
      : [];
  });
}

function gateOrder(policy) {
  return [...new Set(approvalSequence(policy).map((reference) => reference.gate))];
}

function approvalTargets(policy, gate) {
  return approvalSequence(policy)
    .filter((reference) => reference.gate === gate && reference.target !== null)
    .map((reference) => reference.target);
}

function gateRefKey(gate, target = null) {
  return `${gate}\0${target ?? ""}`;
}

function gateRefObject(gate, target = null) {
  return target === null ? { gate } : { gate, target };
}

function parseGateRef(policy, runtime, value) {
  const keys = plainObject(value) ? Object.keys(value) : [];
  const required = runtime.gate.gate_ref_required_fields;
  const optional = runtime.gate.gate_ref_optional_fields;
  if (!plainObject(value)
    || required.some((field) => !keys.includes(field))
    || keys.some((field) => !required.includes(field) && !optional.includes(field))) return null;
  const target = Object.prototype.hasOwnProperty.call(value, "target") ? value.target : null;
  return approvalSequence(policy).find(
    (reference) => reference.gate === value.gate && reference.target === target,
  ) || null;
}

function stageExitRequirement(policy, stage) {
  const value = policy.workflow_graph.stage_exit_requirements[stage];
  return plainObject(value) ? { gate: value.gate, target: value.target ?? null, stage } : null;
}

function gateRefOwner(policy, gate, target = null) {
  return approvalSequence(policy).find(
    (reference) => reference.gate === gate && reference.target === target,
  ) || null;
}

function transitionRule(policy, fromStage, toStage) {
  const candidates = policy.workflow_graph.stage_transitions[fromStage];
  return Array.isArray(candidates)
    ? candidates.find((candidate) => candidate.to === toStage) || null
    : null;
}

function transitionGateRef(policy, fromStage, toStage) {
  const rule = transitionRule(policy, fromStage, toStage);
  if (!plainObject(rule) || !plainObject(rule.trigger)
    || rule.trigger.type !== "stage_exit") return null;
  return stageExitRequirement(policy, rule.trigger.stage);
}

function gateRecord(state, policy, gate, target = null) {
  const container = plainObject(state.gates) ? state.gates[gate] : null;
  const targets = approvalTargets(policy, gate);
  if (targets.length) {
    return plainObject(container) && plainObject(container.targets)
      ? container.targets[target] : null;
  }
  return target === null && plainObject(container) ? container : null;
}

function allGateRecords(state, policy) {
  return approvalSequence(policy).map((reference) => ({
    ...reference,
    record: gateRecord(state, policy, reference.gate, reference.target),
  }));
}

function activeProject(input) {
  const cwd = inputCwd(input);
  if (!cwd) return null;
  const root = findResearchRoot(cwd);
  if (!root) return null;
  const statePath = path.join(root, ".research", "state.json");
  const state = safeReadObject(statePath, MAX_STATE_BYTES);
  if (!state || state.enabled !== true) return null;
  const runtime = loadRuntimeContract();
  const policy = loadPolicy(runtime);
  return {
    root,
    statePath,
    memoryPath: path.join(root, ".research", "memory.md"),
    state,
    policy,
    runtime,
  };
}

function stageSpec(context) {
  const stage = context.state.current_stage;
  if (typeof stage !== "string") return null;
  const spec = context.policy.stages[stage];
  return spec && typeof spec === "object" && !Array.isArray(spec) ? spec : null;
}

function gateStatus(context, gateId, target = null) {
  const record = gateRecord(context.state, context.policy, gateId, target);
  return record && typeof record === "object" ? record.status : null;
}

function gateForRequirement(policy, requirement) {
  if (requirement === "external_submission") return releaseGateId(policy);
  const references = [];
  for (const source of stageOrder(policy)) {
    for (const candidate of policy.workflow_graph.stage_transitions[source]) {
      if (candidate.to !== requirement) continue;
      const reference = transitionGateRef(policy, source, candidate.to);
      if (reference) references.push(reference);
    }
  }
  const gates = [...new Set(references.map((reference) => reference.gate))];
  return gates.length === 1 ? gates[0] : null;
}

function releaseGateId(policy) {
  const matches = gateOrder(policy).filter((gate) => {
    const spec = policy.gates[gate];
    return plainObject(spec) && plainObject(spec.approval_targets);
  });
  return matches.length === 1 ? matches[0] : null;
}

function initialReleaseTarget(policy) {
  const gate = releaseGateId(policy);
  const targets = gate ? approvalTargets(policy, gate) : [];
  return targets.length ? targets[0] : null;
}

function isReleaseGate(policy, gate) {
  return gate === releaseGateId(policy);
}

function listLines(values, fallback = "- none declared") {
  if (!Array.isArray(values) || !values.length) return fallback;
  return values
    .filter((item) => typeof item === "string" && item.trim())
    .slice(0, 12)
    .map((item) => `- ${bounded(item, 360)}`)
    .join("\n") || fallback;
}

function sessionContext(context) {
  return bounded([
    "[SCIENTIFIC RESEARCH WORKFLOW — ACTIVE PROJECT]",
    `Project: ${scalar(context.state.project_name, path.basename(context.root))}`,
    `Project ID: ${scalar(context.state.project_id)}`,
    `Lifecycle: ${scalar(plainObject(context.state.lifecycle) ? context.state.lifecycle.status : null, "invalid")}`,
    "Workflow activation: .research/state.json exists and enabled=true.",
    ".research/state.json is authoritative for project state; policy.yaml governs workflow and Gates; runtime-contract.json governs machine structure.",
  ].join("\n"), MAX_SESSION_CONTEXT_CHARS);
}

function promptContext(context) {
  const spec = stageSpec(context);
  const stage = scalar(context.state.current_stage, "invalid");
  if (!spec) {
    return bounded([
      "[RESEARCH STAGE CONTRACT]",
      `Current stage ${stage} is not defined by the canonical policy. Run researchctl doctor before staged work.`,
    ].join("\n"), MAX_PROMPT_CONTEXT_CHARS);
  }
  const exit = stageExitRequirement(context.policy, stage);
  return bounded([
    "[RESEARCH WORKFLOW — CURRENT STATE]",
    `Lifecycle: ${scalar(plainObject(context.state.lifecycle) ? context.state.lifecycle.status : null, "invalid")}`,
    `Current stage: ${stage} — ${scalar(spec.label, "unlabeled")}`,
    `Gate to exit: ${exit ? `${exit.gate}${exit.target ? `/${exit.target}` : ""} (${scalar(gateStatus(context, exit.gate, exit.target), "missing")})` : "none"}`,
  ].join("\n"), MAX_PROMPT_CONTEXT_CHARS);
}

function hookContextOutput(event, additionalContext) {
  return {
    hookSpecificOutput: {
      hookEventName: event,
      additionalContext,
    },
  };
}

function getToolName(input) {
  const value = firstDefined(input, ["tool_name", "toolName", "name"]);
  return typeof value === "string" ? value : "";
}

function getToolInput(input) {
  const value = firstDefined(input, ["tool_input", "toolInput", "arguments", "input"]);
  return value === undefined ? {} : value;
}

function stringifyToolValue(value) {
  if (typeof value === "string") return value.slice(0, MAX_TOOL_TEXT_CHARS);
  try {
    return JSON.stringify(value).slice(0, MAX_TOOL_TEXT_CHARS);
  } catch (_error) {
    return String(value).slice(0, MAX_TOOL_TEXT_CHARS);
  }
}

function commandText(toolInput) {
  if (typeof toolInput === "string") return toolInput;
  if (!toolInput || typeof toolInput !== "object") return "";
  const candidate = firstDefined(toolInput, ["command", "cmd", "script", "shell_command"]);
  if (Array.isArray(candidate)) return candidate.join(" ");
  if (typeof candidate === "string") return candidate;
  return "";
}

function normalizedToolText(toolInput) {
  const command = commandText(toolInput);
  const source = command || stringifyToolValue(toolInput);
  return cleanText(source).replace(/\\/g, "/");
}

function shellCommandSegments(command) {
  const segments = [];
  let current = "";
  let quote = null;
  let escaped = false;
  for (let index = 0; index < command.length; index += 1) {
    const character = command[index];
    if (escaped) {
      current += character;
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      current += character;
      escaped = true;
      continue;
    }
    if (quote) {
      current += character;
      if (character === quote) quote = null;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      current += character;
      continue;
    }
    if (character === "\n" || character === ";" || character === "|" || character === "&") {
      if (current.trim()) segments.push(current.trim());
      current = "";
      while (index + 1 < command.length && command[index + 1] === character) index += 1;
      continue;
    }
    current += character;
  }
  if (current.trim()) segments.push(current.trim());
  return segments;
}

function shellTokens(segment) {
  const tokens = [];
  let current = "";
  let quote = null;
  let escaped = false;
  const flush = () => {
    if (current) tokens.push(current);
    current = "";
  };
  for (const character of segment) {
    if (escaped) {
      current += character;
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = null;
      else current += character;
      continue;
    }
    if (character === "'" || character === '"') quote = character;
    else if (/\s/.test(character)) flush();
    else current += character;
  }
  flush();
  return tokens;
}

function redirectionTargets(segment) {
  const targets = [];
  let quote = null;
  let escaped = false;
  for (let index = 0; index < segment.length; index += 1) {
    const character = segment[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\" && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = null;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    if (character !== ">") continue;
    while (segment[index + 1] === ">") index += 1;
    while (/\s/.test(segment[index + 1] || "")) index += 1;
    let target = "";
    let targetQuote = null;
    if (segment[index + 1] === "'" || segment[index + 1] === '"') {
      targetQuote = segment[index + 1];
      index += 1;
    }
    while (index + 1 < segment.length) {
      const next = segment[index + 1];
      if ((targetQuote && next === targetQuote)
        || (!targetQuote && /[\s;&|]/.test(next))) break;
      target += next;
      index += 1;
    }
    if (target) targets.push(target);
  }
  return targets;
}

function commandTokens(segment) {
  const tokens = shellTokens(segment);
  let changed = true;
  while (changed && tokens.length) {
    changed = false;
    if (/^(?:sudo|command|nohup)$/i.test(tokens[0])) {
      tokens.shift();
      changed = true;
    } else if (/^time$/i.test(tokens[0])) {
      tokens.shift();
      while (tokens[0] && /^-[afhopsv]+$/i.test(tokens[0])) tokens.shift();
      changed = true;
    } else if (/^env$/i.test(tokens[0])) {
      tokens.shift();
      while (tokens[0] && (/^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[0]) || /^-/.test(tokens[0]))) {
        const option = tokens.shift();
        if (["-u", "--unset", "-C", "--chdir"].includes(option) && tokens.length) tokens.shift();
      }
      changed = true;
    } else if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[0])) {
      tokens.shift();
      changed = true;
    }
  }
  return tokens;
}

function expandedShellSegments(command, depth = 0) {
  const results = [];
  for (const segment of shellCommandSegments(command)) {
    const tokens = commandTokens(segment);
    const executable = path.basename(tokens[0] || "").toLowerCase();
    if (depth < 3 && ["bash", "sh", "zsh"].includes(executable)) {
      const commandIndex = tokens.findIndex((token) => /^-[^-]*c/.test(token));
      const nested = commandIndex >= 0 ? tokens[commandIndex + 1] : null;
      if (typeof nested === "string") {
        results.push(...expandedShellSegments(nested, depth + 1));
        continue;
      }
    }
    results.push(segment);
  }
  return results;
}

function isShellTool(toolName) {
  return /(?:^|[:._-])(bash|shell|exec_command|unified_exec|run_command|terminal)(?:$|[:._-])/i.test(toolName)
    || /^(Bash|Shell)$/i.test(toolName);
}

function isPatchTool(toolName) {
  return /(?:^|[:._-])(apply_patch|patch)(?:$|[:._-])/i.test(toolName);
}

function isMutatingTool(toolName) {
  return isPatchTool(toolName)
    || /(?:^|[:._-])(write|edit|update|create|delete|remove|move|rename|copy|replace|append)(?:$|[:._-])/i.test(toolName);
}

function mentionsStateFile(text) {
  return /(?:^|[\s"'/:])\.research\/+state\.(?:json|lock)(?:$|[\s"':,}\]])/i.test(text)
    || /\.research\/+state\.(?:json|lock)/i.test(text);
}

function mentionsBareStateFile(text) {
  return /(?:^|[\s"'=/:])(?:\.\.\/|\.\/)*state\.(?:json|lock)(?:$|[\s"',;:&|}\]])/i.test(text);
}

function shellChangesIntoResearch(context, command) {
  const normalized = command.replace(/\\/g, "/");
  const directoryChange = /(?:^|[;&|]\s*|\(|["'])\s*(?:cd|pushd|set-location|sl)\s+(?:--\s+)?(?:"([^"]+)"|'([^']+)'|([^\s;&|"']+))/gi;
  let match;
  while ((match = directoryChange.exec(normalized)) !== null) {
    const candidate = match[1] || match[2] || match[3] || "";
    if (/(?:^|\/)\.research(?:\/|$)/i.test(candidate)) return true;
    try {
      const researchRoot = fs.realpathSync.native(path.join(context.root, ".research"));
      const resolved = fs.realpathSync.native(path.resolve(context.root, candidate));
      if (resolved === researchRoot || resolved.startsWith(`${researchRoot}${path.sep}`)) {
        return true;
      }
    } catch (_error) {
      // Nonexistent directory changes cannot resolve to the active control directory.
    }
  }
  return false;
}

function toolRunsInsideResearch(context, toolInput) {
  if (!toolInput || typeof toolInput !== "object" || Array.isArray(toolInput)) return false;
  const value = firstDefined(toolInput, [
    "workdir",
    "working_directory",
    "workingDirectory",
    "cwd",
  ]);
  if (typeof value !== "string" || !value.trim()) return false;
  const normalized = value.replace(/\\/g, "/");
  if (/(?:^|\/)\.research(?:\/|$)/i.test(normalized)) return true;
  try {
    const resolved = path.resolve(context.root, value);
    const researchRoot = fs.realpathSync.native(path.join(context.root, ".research"));
    const realResolved = fs.realpathSync.native(resolved);
    return realResolved === researchRoot || realResolved.startsWith(`${researchRoot}${path.sep}`);
  } catch (_error) {
    return false;
  }
}

function shellTargetsStateFile(context, toolInput, command) {
  const normalized = command.replace(/\\/g, "/");
  if (shellDestroysControlAuthority(context, toolInput, normalized)) return true;
  if (mentionsStateFile(normalized)) return true;
  const redirectedState = expandedShellSegments(normalized)
    .flatMap(redirectionTargets)
    .some(mentionsBareStateFile);
  if (redirectedState && (shellChangesIntoResearch(context, normalized)
    || toolRunsInsideResearch(context, toolInput))) return true;
  if (!mentionsBareStateFile(normalized)) return false;
  return /(?:^|[\s"'/:])\.research(?:[\s"'/]|$)/i.test(normalized)
    || shellChangesIntoResearch(context, normalized)
    || toolRunsInsideResearch(context, toolInput);
}

function shellDestroysControlAuthority(context, toolInput, command) {
  const researchRoot = path.resolve(context.root, ".research");
  const toolAtControlRoot = (() => {
    if (!toolInput || typeof toolInput !== "object" || Array.isArray(toolInput)) return false;
    const value = firstDefined(toolInput, ["workdir", "working_directory", "workingDirectory", "cwd"]);
    if (typeof value !== "string" || !value.trim()) return false;
    try {
      return path.resolve(context.root, value) === researchRoot;
    } catch (_error) {
      return false;
    }
  })();
  const commandChangesToControlRoot = expandedShellSegments(command).some((segment) => {
    const tokens = commandTokens(segment);
    if (!/^(?:cd|pushd|set-location|sl)$/i.test(tokens[0] || "") || !tokens[1]) return false;
    try {
      return path.resolve(context.root, tokens[1]) === researchRoot;
    } catch (_error) {
      return false;
    }
  });
  const atControlRoot = toolAtControlRoot || commandChangesToControlRoot;
  const authorityTarget = (value) => {
    if (typeof value !== "string") return false;
    const normalized = value.replace(/\\/g, "/").replace(/^['"]|['"]$/g, "");
    if (/(?:^|\/)\.research\/?$/i.test(normalized)) return true;
    if (/(?:^|\/)\.research\/+state\.(?:json|lock|\*[^/]*)$/i.test(normalized)) return true;
    if (atControlRoot && /^(?:\.|\.\/)?\*?$|^\.\/\*$/.test(normalized)) return true;
    return /^(?:\.\.\/|\.\/)*state\.(?:json|lock|\*[^/]*)$/i.test(normalized)
      && (shellChangesIntoResearch(context, command) || toolRunsInsideResearch(context, toolInput));
  };
  for (const segment of expandedShellSegments(command)) {
    const tokens = commandTokens(segment);
    const executable = path.basename(tokens[0] || "").toLowerCase();
    const args = tokens.slice(1);
    if ([
      "rm",
      "mv",
      "truncate",
      "remove-item",
      "move-item",
      "rename-item",
      "clear-content",
      "set-content",
      "chmod",
      "chown",
      "setfacl",
    ].includes(executable) && args.some(authorityTarget)) return true;
    if (executable === "find" && args.includes("-delete") && args.some(authorityTarget)) return true;
  }
  return false;
}

function patchTargetPaths(toolInput) {
  const explicitPatch = toolInput && typeof toolInput === "object"
    ? firstDefined(toolInput, ["patch", "diff"])
    : null;
  const patch = commandText(toolInput)
    || (typeof explicitPatch === "string" ? explicitPatch : stringifyToolValue(toolInput));
  const paths = [];
  const header = /^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+)$/gim;
  const move = /^\*\*\*\s+Move to:\s*(.+)$/gim;
  const unified = /^(?:---|\+\+\+)\s+(?:[ab]\/)?([^\t\n]+)(?:\t.*)?$/gm;
  for (const pattern of [header, move, unified]) {
    let match;
    while ((match = pattern.exec(patch)) !== null) paths.push(match[1].trim());
  }
  return paths.join("\n").replace(/\\/g, "/");
}

const PATH_FIELD_KEYS = new Set([
  "path",
  "file",
  "filename",
  "file_path",
  "filePath",
  "source",
  "src",
  "source_path",
  "sourcePath",
  "old_path",
  "oldPath",
  "new_path",
  "newPath",
  "target",
  "target_path",
  "targetPath",
  "destination",
  "destination_path",
  "destinationPath",
]);

const DESTINATION_PATH_FIELD_KEYS = new Set([
  "new_path",
  "newPath",
  "target",
  "target_path",
  "targetPath",
  "destination",
  "destination_path",
  "destinationPath",
]);

function selectedPathFields(value, pathKeys, result = []) {
  const stack = [value];
  const seen = new WeakSet();
  while (stack.length) {
    const current = stack.pop();
    if (!current || typeof current !== "object" || seen.has(current)) continue;
    seen.add(current);
    if (Array.isArray(current)) {
      for (const child of current) stack.push(child);
      continue;
    }
    for (const [key, child] of Object.entries(current)) {
      if (pathKeys.has(key) && typeof child === "string") result.push(child);
      else if (child && typeof child === "object") stack.push(child);
    }
  }
  return result;
}

function pathFields(value, result = []) {
  return selectedPathFields(value, PATH_FIELD_KEYS, result);
}

function sameExistingFile(left, right) {
  try {
    const leftStat = fs.statSync(left, { bigint: true });
    const rightStat = fs.statSync(right, { bigint: true });
    return leftStat.ino !== 0n
      && leftStat.dev === rightStat.dev
      && leftStat.ino === rightStat.ino;
  } catch (_error) {
    return false;
  }
}

function resolvesToAuthorityFile(context, candidate) {
  if (typeof candidate !== "string" || !candidate.trim()) return false;
  const normalized = candidate.replace(/^['"]|['"]$/g, "");
  const resolved = path.resolve(context.root, normalized);
  const authorityFiles = [
    path.join(context.root, ".research", "state.json"),
    path.join(context.root, ".research", "state.lock"),
  ];
  return authorityFiles.some((authority) => {
    if (resolved === authority || sameExistingFile(resolved, authority)) return true;
    try {
      return fs.realpathSync.native(resolved) === fs.realpathSync.native(authority);
    } catch (_error) {
      return false;
    }
  });
}

function resolvesToResearchRoot(context, candidate) {
  if (typeof candidate !== "string" || !candidate.trim()) return false;
  const normalized = candidate.replace(/^['"]|['"]$/g, "");
  const resolved = path.resolve(context.root, normalized);
  const researchRoot = path.join(context.root, ".research");
  if (resolved === researchRoot) return true;
  try {
    return fs.realpathSync.native(resolved) === fs.realpathSync.native(researchRoot);
  } catch (_error) {
    return false;
  }
}

function structuredTargetsControlAuthority(context, toolName, toolInput) {
  if (!isMutatingTool(toolName) || isPatchTool(toolName) || isShellTool(toolName)) return false;
  const copyOnly = /(?:^|[:._-])copy(?:$|[:._-])/i.test(toolName)
    && !/(?:^|[:._-])(?:move|rename)(?:$|[:._-])/i.test(toolName);
  const paths = copyOnly
    ? selectedPathFields(toolInput, DESTINATION_PATH_FIELD_KEYS)
    : pathFields(toolInput);
  if (paths.some((candidate) => resolvesToAuthorityFile(context, candidate))) return true;
  const changesDirectoryIdentity = copyOnly
    || /(?:^|[:._-])(?:delete|remove|move|rename)(?:$|[:._-])/i.test(toolName);
  return changesDirectoryIdentity
    && paths.some((candidate) => resolvesToResearchRoot(context, candidate));
}

function targetsStateFile(context, toolName, toolInput, command) {
  if (isPatchTool(toolName)) return mentionsStateFile(patchTargetPaths(toolInput));
  if (isShellTool(toolName)) return shellTargetsStateFile(context, toolInput, command);
  if (structuredTargetsControlAuthority(context, toolName, toolInput)) return true;
  const paths = pathFields(toolInput).join("\n").replace(/\\/g, "/");
  return mentionsStateFile(paths)
    || (mentionsBareStateFile(paths) && toolRunsInsideResearch(context, toolInput));
}

function recursiveForcedRemoval(command) {
  for (const segment of expandedShellSegments(command)) {
    const tokens = commandTokens(segment);
    const executable = path.basename(tokens[0] || "").toLowerCase();
    if (executable !== "rm" && executable !== "remove-item") continue;
    const args = tokens.slice(1);
    const recursive = args.some((token) => token === "--recursive" || token === "-Recurse" || /^-[^-]*[rR]/.test(token));
    const forced = args.some((token) => token === "--force" || token === "-Force" || /^-[^-]*[fF]/.test(token));
    if (recursive && forced) return true;
  }
  return false;
}

function gitCommandParts(tokens) {
  let index = 1;
  while (index < tokens.length) {
    const token = tokens[index];
    if (["-C", "-c", "--git-dir", "--work-tree", "--config-env"].includes(token)) index += 2;
    else if (/^--(?:git-dir|work-tree)=/.test(token)) index += 1;
    else if (/^--(?:no-pager|paginate|bare|literal-pathspecs|glob-pathspecs|noglob-pathspecs|icase-pathspecs)$/.test(token)) index += 1;
    else break;
  }
  return {
    subcommand: (tokens[index] || "").toLowerCase(),
    args: tokens.slice(index + 1),
  };
}

function destructiveGitCommand(command) {
  for (const segment of expandedShellSegments(command)) {
    const tokens = commandTokens(segment);
    if (path.basename(tokens[0] || "").toLowerCase() !== "git") continue;
    const { subcommand, args } = gitCommandParts(tokens);
    if (subcommand === "reset" && args.includes("--hard")) return "git reset --hard";
    if (subcommand === "checkout") {
      const destructiveOption = args.some((token) => ["--ours", "--theirs", "-f", "--force"].includes(token));
      const explicitPathspec = args.includes("--") || (args.length >= 2 && !["-b", "-B", "--orphan"].includes(args[0]));
      if (destructiveOption || explicitPathspec) return "destructive worktree restoration";
    }
    if (subcommand === "restore") {
      const stagedOnly = args.includes("--staged")
        && !args.includes("--worktree")
        && !args.includes("-W");
      if (!stagedOnly) return "destructive worktree restoration";
    }
    if (subcommand !== "clean") continue;
    const dryRun = args.some((token) => token === "-n" || token === "--dry-run" || /^-[^-]*n/.test(token));
    const force = args.some((token) => token === "-f" || token === "--force" || /^-[^-]*f/.test(token));
    if (!dryRun && force) return "destructive git clean";
  }
  return null;
}

function dangerousShellReason(command) {
  if (recursiveForcedRemoval(command)) return "recursive forced deletion";
  const gitReason = destructiveGitCommand(command);
  if (gitReason) return gitReason;
  const checks = [
    [/(?:^|[;&|]\s*|\s)(?:sudo\s+)?(?:mkfs(?:\.\w+)?|shutdown|reboot)\b/i, "system-destructive command"],
    [/\bdiskutil\s+(?:erase|partitionDisk)\b/i, "disk erase or repartition"],
    [/\bdd\b[\s\S]*\bof=\/dev\//i, "raw device overwrite"],
    [/\bchmod\s+-R\s+777\s+\/(?:\s|$)/i, "recursive permission change at filesystem root"],
  ];
  const effective = expandedShellSegments(command).join("\n");
  for (const [pattern, label] of checks) {
    if (pattern.test(effective)) return label;
  }
  return null;
}

function isResearchCtlCommand(command) {
  if (!/(?:^|[\s"'/])researchctl(?:\.py)?(?:["'\s]|$)/i.test(command)) return false;
  return /\b(?:init|status|enable|disable|artifact|gate|lifecycle|checkpoint|dashboard|adapter|doctor)\b/i.test(command);
}

function researchCtlArguments(segment) {
  const tokens = commandTokens(segment);
  const index = tokens.findIndex(
    (token) => /^(?:researchctl|researchctl\.py)$/i.test(path.basename(token)),
  );
  return index >= 0 ? tokens.slice(index + 1) : null;
}

function terminalResearchCtlAllowed(args) {
  if (!Array.isArray(args) || !args.length) return false;
  if (["status", "doctor", "dashboard", "disable"].includes(args[0])) return true;
  return args[0] === "lifecycle" && args[1] === "reopen";
}

function terminalShellAllowed(command) {
  if (/(?:\$\(|`|[<>=]\()/.test(command)) return false;
  const readOnly = new Set([
    "cat", "head", "tail", "less", "more", "stat", "ls", "rg", "grep",
    "wc", "pwd", "test", "true", "jq", "diff", "shasum", "sha256sum",
    "md5sum", "file", "du", "realpath", "readlink",
  ]);
  const segments = expandedShellSegments(command);
  return segments.length > 0 && segments.every((segment) => {
    if (/(?:>>?|\btee\b|\bsponge\b|\btruncate\b|\btouch\b|\brm\b|\bmv\b|\bcp\b)/i.test(segment)) {
      return false;
    }
    const researchArgs = researchCtlArguments(segment);
    if (researchArgs !== null) return terminalResearchCtlAllowed(researchArgs);
    const tokens = commandTokens(segment);
    const executable = path.basename(tokens[0] || "").toLowerCase();
    if (readOnly.has(executable)) {
      return executable !== "jq" || !tokens.includes("--in-place");
    }
    if (executable === "sed") {
      return tokens[1] === "-n"
        && /^(?:\d+|\$)(?:,(?:\d+|\$))?p$/.test(tokens[2] || "")
        && tokens.length >= 4;
    }
    if (executable === "find") {
      const mutatingActions = new Set([
        "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint",
        "-fprintf", "-fls",
      ]);
      return !tokens.some((token) => mutatingActions.has(token));
    }
    if (executable !== "git") return false;
    return ["status", "diff", "log", "show"].includes(
      gitCommandParts(tokens).subcommand,
    );
  });
}

function terminalResearchCtlToolAllowed(toolName, toolInput) {
  if (!/(?:^|[:._-])researchctl(?:$|[:._-])/i.test(toolName)) return null;
  const normalized = toolName.toLowerCase().replaceAll("_", "-");
  if (/(?:status|doctor|dashboard|disable)/.test(normalized)) return true;
  const action = plainObject(toolInput)
    ? firstDefined(toolInput, ["action", "lifecycle_action", "lifecycleAction"])
    : null;
  return /lifecycle/.test(normalized) && action === "reopen";
}

function terminalLifecyclePreToolUse(context, input) {
  const lifecycle = context.state.lifecycle;
  const status = plainObject(lifecycle) ? lifecycle.status : null;
  if (status === "active") return null;
  const toolName = getToolName(input);
  const toolInput = getToolInput(input);
  const command = cleanText(commandText(toolInput));
  if (isShellTool(toolName) && terminalShellAllowed(command)) return {};
  const researchCtlAllowed = terminalResearchCtlToolAllowed(toolName, toolInput);
  if (researchCtlAllowed === true) return {};
  const readOnlyRetrievalTool = /(?:^|[:._-])web(?:[:._-]+)run$/i.test(toolName);
  if (readOnlyRetrievalTool) return {};
  const readTool = /(?:^|[:._-])(read|get|list|search|view)(?:$|[:._-])/i.test(toolName);
  if (readTool && researchCtlAllowed === null) return {};
  return deny(
    `Project lifecycle is ${JSON.stringify(status)}. Terminal or invalid lifecycle state permits only read/audit tools, status --json or Dashboard export, researchctl disable, and explicit researchctl lifecycle reopen.`,
  );
}

function shellStateMutation(command) {
  let effective = command;
  const leadingDirectoryChange = /^\s*(?:cd|pushd)\s+(?:--\s+)?(?:"[^"]+"|'[^']+'|[^\s;&|]+)\s*(?:&&|;)\s*/i;
  while (leadingDirectoryChange.test(effective)) {
    effective = effective.replace(leadingDirectoryChange, "");
  }
  const readOnly = /^\s*(?:cat|head|tail|less|more|stat|ls|rg|grep|wc|shasum|sha256sum|md5sum|file|du|realpath|readlink|jq\b(?![\s\S]*(?:>|--in-place))|sed\s+-n\b|test\b)/i;
  return !readOnly.test(effective)
    || /(?:>>?|\btee\b|\bsponge\b|\btruncate\b|\brm\b|\bmv\b|\bcp\b|\btouch\b|\bsed\b[\s\S]*\s-i\b|\bwriteFile|\bwrite_text|\bjson\.dump\b)/i.test(effective);
}

function pythonLaunchesExperiment(command) {
  const tokens = commandTokens(command);
  if (!/^python(?:3(?:\.\d+)?)?$/i.test(path.basename(tokens[0] || ""))) return false;
  const script = tokens.slice(1).find((token) => !token.startsWith("-") && /\.py$/i.test(token));
  if (!script) return false;
  const basename = path.basename(script).toLowerCase();
  if (/^(?:test|tests)[_-]/.test(basename)
    || /(?:^|[_-])test\.py$/.test(basename)
    || /^(?:train|training)[_-](?:util|utils|helper|helpers|test|tests)\.py$/.test(basename)) {
    return false;
  }
  return /(?:^|[_-])(?:train|training|experiment|benchmark)(?:[_-]|\.)/.test(basename);
}

function pythonModuleLaunchesExperiment(segment) {
  const tokens = commandTokens(segment);
  if (!/^python(?:3(?:\.\d+)?)?$/i.test(path.basename(tokens[0] || ""))) return false;
  const moduleIndex = tokens.indexOf("-m");
  const moduleName = moduleIndex >= 0 ? tokens[moduleIndex + 1] : null;
  return typeof moduleName === "string"
    && /(?:^|\.)(?:train|training|experiment|benchmark)$|(?:^|\.)(?:train|training|experiment|benchmark)[_.-]/i.test(moduleName);
}

function runnerSegmentLaunches(segment) {
  const tokens = commandTokens(segment);
  const executable = path.basename(tokens[0] || "").toLowerCase();
  let args = tokens.slice(1);
  let runner = executable;
  if (executable === "accelerate" && (args[0] || "").toLowerCase() === "launch") {
    runner = "accelerate launch";
    args = args.slice(1);
  } else if (executable === "wandb" && (args[0] || "").toLowerCase() === "sweep") {
    runner = "wandb sweep";
    args = args.slice(1);
  } else if (executable === "ros2" && (args[0] || "").toLowerCase() === "launch") {
    runner = "ros2 launch";
    args = args.slice(1);
  }
  if (!new Set([
    "sbatch",
    "qsub",
    "srun",
    "mpirun",
    "mpiexec",
    "torchrun",
    "deepspeed",
    "accelerate launch",
    "wandb sweep",
    "roslaunch",
    "ros2 launch",
  ]).has(runner)) return false;

  const asksForHelp = args.some((token) => ["--help", "-h", "--version"].includes(token));
  if (!asksForHelp) return true;
  const hasTarget = args.some((token, index) => (
    /(?:^|[\\/])[^\\/]+\.(?:py|sh|yaml|yml|launch\.py)$/i.test(token)
    || /(?:^|[_.-])(?:train|training|experiment|benchmark)(?:$|[_.-])/i.test(token)
    || ((token === "-m" || token === "--module") && typeof args[index + 1] === "string")
  ));
  return hasTarget;
}

function segmentLaunchesExperiment(segment, depth = 0) {
  if (depth > 3) return false;
  const tokens = commandTokens(segment);
  const executable = path.basename(tokens[0] || "").toLowerCase();
  if (["bash", "sh", "zsh"].includes(executable)) {
    const commandIndex = tokens.findIndex((token) => /^-[^-]*c/.test(token));
    const nested = commandIndex >= 0 ? tokens[commandIndex + 1] : null;
    return typeof nested === "string"
      && shellCommandSegments(nested).some((child) => segmentLaunchesExperiment(child, depth + 1));
  }
  if (/^python(?:3(?:\.\d+)?)?$/i.test(executable)) {
    const moduleIndex = tokens.indexOf("-m");
    if (moduleIndex >= 0 && /^(?:pytest|unittest)$/i.test(tokens[moduleIndex + 1] || "")) return false;
  }
  if (executable === "make" && tokens.slice(1).some((token) => /^(?:train|training|experiment|benchmark)$/.test(token))) {
    return true;
  }
  return runnerSegmentLaunches(segment)
    || pythonLaunchesExperiment(segment)
    || pythonModuleLaunchesExperiment(segment);
}

function explicitExperimentLaunch(toolName, command, text) {
  if (/(?:run|launch|start)[_:-]?(?:experiment|training|benchmark)|takeoff|arm[_:-]?drone/i.test(toolName)) {
    return true;
  }
  if (!command) return false;
  for (const segment of shellCommandSegments(command)) {
    if (segmentLaunchesExperiment(segment)) return true;
  }
  return /\b(?:takeoff|arm_drone|arm-uav)\b/i.test(text);
}

function registeredManuscriptPaths(context) {
  const results = [];
  const artifacts = context.state && context.state.artifacts;
  const releaseId = releaseGateId(context.policy);
  const releaseSpec = releaseId && context.policy.gates[releaseId];
  const mapping = releaseSpec && releaseSpec.approval_targets;
  const roles = [...new Set(
    plainObject(mapping)
      ? Object.values(mapping).flatMap((contract) => (
        plainObject(contract) && Array.isArray(contract.required_artifact_roles)
          ? contract.required_artifact_roles : []
      )).filter((role) => (
        typeof role === "string" && /(?:^|\.)(?:[a-z0-9_]*manuscript|response_document)$/.test(role)
      ))
      : [],
  )].map((role) => role.split(".", 2));
  for (const [stage, role] of roles) {
    const stageBucket = artifacts && typeof artifacts === "object" ? artifacts[stage] : null;
    const roleBucket = stageBucket && typeof stageBucket === "object" ? stageBucket[role] : null;
    if (!roleBucket || typeof roleBucket !== "object") continue;
    for (const entry of Object.values(roleBucket)) {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)
        || !Array.isArray(entry.revisions)) continue;
      const revision = entry.revisions.find((item) => (
        item && typeof item === "object" && item.revision === entry.current_revision
      ));
      if (revision && typeof revision.source_path === "string") {
        results.push(revision.source_path.replace(/\\/g, "/"));
      }
    }
  }
  return results;
}

function mutationTargetsPaths(context, toolName, toolInput, text, registered, knownTarget = null) {
  if (!(isMutatingTool(toolName) || isShellTool(toolName))) return false;
  const registeredAbsolute = registered.map((stored) => path.resolve(context.root, stored));
  const isTarget = (candidate) => {
    if (typeof candidate !== "string" || !candidate.trim()) return false;
    const normalized = candidate.replace(/^['"]|['"]$/g, "").replace(/\\/g, "/");
    if (knownTarget && knownTarget.test(normalized)) return true;
    return registered.some((stored) => (
      normalized === stored
      || path.resolve(context.root, normalized) === path.resolve(context.root, stored)
      || sameExistingFile(
        path.resolve(context.root, normalized),
        path.resolve(context.root, stored),
      )
    ));
  };
  const isTargetOrContainer = (candidate) => {
    if (isTarget(candidate)) return true;
    if (typeof candidate !== "string" || !candidate.trim()) return false;
    const normalized = candidate.replace(/^['"]|['"]$/g, "").replace(/\\/g, "/");
    const resolved = path.resolve(context.root, normalized);
    return registeredAbsolute.some((stored) => stored.startsWith(`${resolved}${path.sep}`));
  };
  if (isPatchTool(toolName)) {
    return patchTargetPaths(toolInput).split("\n").some(isTarget);
  }
  if (!isShellTool(toolName)) {
    const paths = pathFields(toolInput);
    const copyOnly = /(?:^|[:._-])copy(?:$|[:._-])/i.test(toolName)
      && !/(?:^|[:._-])(?:move|rename)(?:$|[:._-])/i.test(toolName);
    if (copyOnly && toolInput && typeof toolInput === "object" && !Array.isArray(toolInput)) {
      const destination = firstDefined(toolInput, [
        "destination", "destination_path", "destinationPath", "target", "target_path", "targetPath", "new_path", "newPath",
      ]);
      return isTarget(destination);
    }
    if (/(?:^|[:._-])(?:delete|remove|move|rename)(?:$|[:._-])/i.test(toolName)
      && paths.some(isTargetOrContainer)) return true;
    return paths.length ? paths.some(isTarget) : Boolean(knownTarget && knownTarget.test(text));
  }
  for (const segment of expandedShellSegments(text)) {
    const tokens = commandTokens(segment);
    const executable = path.basename(tokens[0] || "").toLowerCase();
    const operands = tokens.slice(1).filter((token) => token && !token.startsWith("-"));
    if (["mv", "move-item", "rename-item"].includes(executable) && operands.some(isTargetOrContainer)) return true;
    if (["cp", "install", "rsync", "copy-item"].includes(executable)
      && operands.length && isTarget(operands[operands.length - 1])) return true;
    if (["rm", "remove-item"].includes(executable) && operands.some(isTargetOrContainer)) return true;
    if ([
      "touch", "truncate", "clear-content", "set-content", "add-content", "out-file",
    ].includes(executable) && operands.some(isTarget)) return true;
    if (["sed", "perl"].includes(executable)
      && tokens.slice(1).some((token) => /^-[^-]*i/.test(token))
      && operands.some(isTarget)) return true;
    if (["tee", "sponge"].includes(executable) && operands.some(isTarget)) return true;
    if (redirectionTargets(segment).some(isTarget)) return true;
    if (/\b(?:writeFile|write_text)\b/.test(segment) && registered.concat([]).some((item) => segment.includes(item))) {
      return true;
    }
  }
  return false;
}

function manuscriptMutation(context, toolName, toolInput, text) {
  const knownTarget = /(?:^|[\/])(?:main|paper|manuscript|appendix|supplement|respond|response|rebuttal|cover[_-]?letter)\.(?:tex|md|docx)$/i;
  return mutationTargetsPaths(
    context, toolName, toolInput, text, registeredManuscriptPaths(context), knownTarget,
  );
}

function approvedReleaseBindingMutation(context, releaseGate, toolName, toolInput, text) {
  if (!releaseGate) return null;
  for (const target of approvalTargets(context.policy, releaseGate)) {
    const record = gateRecord(context.state, context.policy, releaseGate, target);
    if (!plainObject(record) || record.status !== "approved") continue;
    const approval = latestApprovalDecision(record);
    if (!plainObject(approval) || !Array.isArray(approval.artifact_refs)) continue;
    const mutableRoles = mutableAfterApprovalRoles(
      context.policy, releaseGate, target, approval,
    );
    const boundPaths = approval.artifact_refs.filter((reference) => {
      const role = referenceRole(reference);
      return typeof role === "string"
        && !mutableRoles.has(role);
    }).map((reference) => reference.source_path).filter(
      (sourcePath) => typeof sourcePath === "string" && sourcePath.trim(),
    );
    if (boundPaths.length
      && mutationTargetsPaths(context, toolName, toolInput, text, boundPaths)) {
      return { gate: releaseGate, target };
    }
  }
  return null;
}

function submissionDestination(value) {
  return typeof value === "string"
    && /\b(?:openreview|softconf|submission|submissions|incoming)\b|(?:editor|chair)@/i.test(value);
}

function remoteTransferOperand(value) {
  return typeof value === "string"
    && (/^(?![A-Za-z]:[\\/])(?:[^/\s:]+@)?[^/\s:]+:/.test(value)
      || /^(?:s3|rsync|https?):\/\//i.test(value));
}

function transferUploadsSubmission(command) {
  for (const segment of expandedShellSegments(command)) {
    const tokens = commandTokens(segment);
    const executable = path.basename(tokens[0] || "").toLowerCase();
    if (["scp", "sftp", "rsync"].includes(executable)) {
      const operands = tokens.slice(1).filter((token) => token && !token.startsWith("-"));
      const destination = operands[operands.length - 1];
      if (operands.length >= 2 && remoteTransferOperand(destination) && submissionDestination(destination)) {
        return true;
      }
      continue;
    }
    if (executable === "aws" && (tokens[1] || "").toLowerCase() === "s3"
      && (tokens[2] || "").toLowerCase() === "cp") {
      const operands = tokens.slice(3).filter((token) => token && !token.startsWith("-"));
      const destination = operands[operands.length - 1];
      if (remoteTransferOperand(destination) && submissionDestination(destination)) return true;
      continue;
    }
    if (executable === "curl") {
      const uploadFlag = tokens.some((token) => (
        /^(?:-[FTd])(?:.+)?$/.test(token)
        || /^--(?:form|upload-file|data(?:-[a-z-]+)?|json)(?:=|$)/.test(token)
        || /^--request=(?:POST|PUT|PATCH)$/i.test(token)
      ));
      const requestIndex = tokens.findIndex((token) => token === "-X" || token === "--request");
      const writeRequest = requestIndex >= 0 && /^(?:POST|PUT|PATCH)$/i.test(tokens[requestIndex + 1] || "");
      const upload = uploadFlag || writeRequest;
      if (upload && tokens.some(submissionDestination)) return true;
    }
  }
  return false;
}

function externalReleaseAction(toolName, command, text) {
  const transferCommand = /\b(?:scp|sftp|rsync|curl|aws\s+s3\s+cp)\b/i.test(command);
  const action = /(?:^|[:._-])(send|submit|publish|upload|post|forward|release)(?:$|[:._-])/i.test(toolName)
    || (!transferCommand && /\b(?:submit|publish|upload|send|post|forward)\b/i.test(command));
  const submissionTransfer = transferUploadsSubmission(command);
  const subject = /\b(?:manuscript|paper|rebuttal|reviewer[_ -]?response|camera[_ -]?ready|openreview|softconf)\b/i.test(text)
    || /(?:论文|稿件|审稿回复|投稿|返修回复)/.test(text);
  return (action || submissionTransfer) && subject;
}

function mechanicallyRequestedReleaseTarget(policy, gate, text) {
  const references = gate ? approvalSequence(policy).filter(
    (reference) => reference.gate === gate && reference.target !== null,
  ) : [];
  const targets = references.map((reference) => reference.target);
  const namedTargets = targets.filter((target) => {
    const variants = [target, target.replaceAll("_", " "), target.replaceAll("_", "-")];
    return variants.some((variant) => text.toLowerCase().includes(variant.toLowerCase()));
  });
  if (namedTargets.length) {
    return { directed: true, target: namedTargets.length === 1 ? namedTargets[0] : null };
  }
  if (/\b(?:initial|first|new)[_ -]?(?:manuscript[_ -]?)?submission\b|(?:首次|初次)投稿/i.test(text)) {
    return { directed: true, target: targets[0] ?? null };
  }
  if (/\b(?:rebuttal|reviewer[_ -]?response|response[_ -]?document|revis(?:ed|ion))\b|(?:审稿回复|返修回复|修订稿)/i.test(text)) {
    const downstream = references.slice(1);
    return {
      directed: true,
      target: downstream.length === 1 ? downstream[0].target : null,
    };
  }
  return { directed: false, target: null };
}

function deny(reason) {
  return {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
  };
}

function preToolUse(context, input) {
  const toolName = getToolName(input);
  const toolInput = getToolInput(input);
  const text = normalizedToolText(toolInput);
  const command = cleanText(commandText(toolInput));

  if (isShellTool(toolName)) {
    const dangerous = dangerousShellReason(command);
    if (dangerous) {
      return deny(`Blocked mechanically detectable dangerous operation (${dangerous}). Use a narrower reversible command. Hook coverage is limited to this intercepted tool call.`);
    }
  }

  if (targetsStateFile(context, toolName, toolInput, command)) {
    const bypass = isPatchTool(toolName)
      || isMutatingTool(toolName)
      || (isShellTool(toolName) && shellStateMutation(command))
      || (!isShellTool(toolName) && !/(?:^|[:._-])(read|get|list|search|view)(?:$|[:._-])/i.test(toolName));
    if (bypass) {
      return deny("Direct mutation of .research/state.json or its transaction lock is blocked. Use researchctl artifact register, enable|disable, gate, or checkpoint so artifact and Gate state changes remain validated and traceable.");
    }
  }

  const lifecycleDecision = terminalLifecyclePreToolUse(context, input);
  if (lifecycleDecision !== null) return lifecycleDecision;

  const launchesExperiment = explicitExperimentLaunch(toolName, command, text);
  const mutatesManuscript = manuscriptMutation(context, toolName, toolInput, text);
  const releasesExternally = externalReleaseAction(toolName, command, text);
  const releaseGate = releaseGateId(context.policy);
  const releaseBinding = approvedReleaseBindingMutation(
    context, releaseGate, toolName, toolInput, text,
  );
  if (!(launchesExperiment || mutatesManuscript || releasesExternally || releaseBinding)) return {};

  const experimentGate = gateForRequirement(context.policy, "experiment_results");
  const manuscriptGate = gateForRequirement(context.policy, "paper");
  const requestedRelease = releasesExternally
    ? mechanicallyRequestedReleaseTarget(context.policy, releaseGate, text)
    : { directed: false, target: null };
  const currentExit = stageExitRequirement(
    context.policy, context.state.current_stage,
  );
  const activeReleaseTarget = currentExit && currentExit.gate === releaseGate
    ? currentExit.target : null;
  const releaseTarget = requestedRelease.directed
    ? requestedRelease.target : activeReleaseTarget;
  const integrityGates = [];
  if (launchesExperiment && experimentGate) integrityGates.push({ gate: experimentGate });
  if (mutatesManuscript && manuscriptGate) integrityGates.push({ gate: manuscriptGate });
  if (releasesExternally && releaseGate && releaseTarget) {
    integrityGates.push({ gate: releaseGate, target: releaseTarget });
  }
  if (releaseBinding) integrityGates.push(releaseBinding);
  const stateCheck = validateState(context, { integrityGates });
  const stateTrusted = stateCheck.errors.length === 0;
  const gateTrusted = (gate, target = null) => stateTrusted
    && gateStatus(context, gate, target) === "approved";
  const untrustedSuffix = (gate, target = null) => gateStatus(context, gate, target) === "approved" && !stateTrusted
    ? ` The recorded ${gate}${target ? `/${target}` : ""} approval is untrusted because state, revision, source, snapshot, or Gate bindings failed mechanical validation; run researchctl doctor.`
    : "";

  if (launchesExperiment && !gateTrusted(experimentGate)) {
    return deny(`This is an explicit experiment, training, cluster, or hardware launch, but ${experimentGate || "the policy experiment Gate"} is not mechanically trusted.${untrustedSuffix(experimentGate)} Prepare the method and experiment contract, then record human approval through researchctl.`);
  }

  if (launchesExperiment) {
    return deny("The experiment Gate is mechanically trusted, but a direct launch is not a conforming Adapter dispatch. Persist the Adapter Request, run researchctl adapter verify, register the attempt's first accepted receipt as a durable pre-side-effect journal, and let the external Adapter execute the bound immutable inputs.");
  }

  if (mutatesManuscript && !gateTrusted(manuscriptGate)) {
    return deny(`This tool call mechanically targets a manuscript or rebuttal artifact before ${manuscriptGate || "the policy manuscript Gate"} is mechanically trusted.${untrustedSuffix(manuscriptGate)} Freeze evidence-bounded claims through researchctl before entering paper production.`);
  }

  if (releaseBinding) {
    return deny(`The approved release binding ${releaseBinding.gate}/${releaseBinding.target} includes this artifact, so changing it would make that approval stale. Reopen that exact target through researchctl, make and verify the revision, then request fresh approval.`);
  }

  if (releasesExternally && (!releaseTarget || !gateTrusted(releaseGate, releaseTarget))) {
    const label = releaseTarget ? `${releaseGate}/${releaseTarget}` : (releaseGate || "the policy release Gate target");
    return deny(`This appears to send, submit, publish, or upload a manuscript/reviewer response while ${label} is not mechanically trusted.${untrustedSuffix(releaseGate, releaseTarget)} Record explicit human approval for the exact release target through researchctl first.`);
  }

  if (releasesExternally) {
    return deny("The exact release Gate is mechanically trusted, but it does not authorize this direct send. Use a conforming external Adapter bound to the approved release package, an action-specific human authorization declaration, and a durable accepted attempt journal.");
  }

  return {};
}

function invalidPolicyPreToolUse(context, input) {
  const toolName = getToolName(input);
  const toolInput = getToolInput(input);
  const text = normalizedToolText(toolInput);
  const command = cleanText(commandText(toolInput));
  const diagnosticResearchCtl = /(?:^|[\s"'/])researchctl(?:\.py)?["']?\s+(?:status|doctor|dashboard|disable)(?:\s|$)/i.test(command);
  const risky = isMutatingTool(toolName)
    || (isShellTool(toolName) && shellStateMutation(command) && !diagnosticResearchCtl)
    || explicitExperimentLaunch(toolName, command, text)
    || externalReleaseAction(toolName, command, text);
  if (!risky) return {};
  return deny(
    "This project is enabled, but the canonical research policy or runtime contract is missing or invalid. "
    + "Protected writes, experiment launches, and external release actions are blocked "
    + "until both plugin authorities validate; use read-only inspection or researchctl status, "
    + "doctor, or disable for diagnosis.",
  );
}

function stateWasTouched(context, input) {
  const toolName = getToolName(input);
  const toolInput = getToolInput(input);
  const text = normalizedToolText(toolInput);
  const command = cleanText(commandText(toolInput));
  return targetsStateFile(context, toolName, toolInput, command)
    || (isShellTool(toolName) && isResearchCtlCommand(command))
    || /(?:^|[:._-])researchctl(?:$|[:._-])/i.test(toolName);
}

function plainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function exactFields(value, fields, label, errors) {
  if (!plainObject(value)) {
    errors.push(`${label} must be an object`);
    return false;
  }
  const missing = fields.filter((field) => !Object.prototype.hasOwnProperty.call(value, field));
  const extra = Object.keys(value).filter((field) => !fields.includes(field));
  if (missing.length) errors.push(`${label} missing fields: ${missing.sort().join(", ")}`);
  if (extra.length) errors.push(`${label} has unknown fields: ${extra.sort().join(", ")}`);
  return !missing.length && !extra.length;
}

function resolvedStoredPath(root, value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    return path.isAbsolute(value) ? path.resolve(value) : path.resolve(root, value);
  } catch (_error) {
    return null;
  }
}

function pathWithin(candidate, parent) {
  const relative = path.relative(parent, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== "..");
}

function hashFileWithSize(candidate) {
  let descriptor;
  try {
    descriptor = fs.openSync(candidate, "r");
    const before = fs.fstatSync(descriptor);
    if (!before.isFile()) return { error: "not a regular file" };
    const digest = crypto.createHash("sha256");
    const buffer = Buffer.allocUnsafe(1024 * 1024);
    let size = 0;
    while (true) {
      const count = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (!count) break;
      digest.update(buffer.subarray(0, count));
      size += count;
    }
    const after = fs.fstatSync(descriptor);
    if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size
      || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) {
      return { error: "changed while being verified" };
    }
    return { contentHash: `sha256:${digest.digest("hex")}`, sizeBytes: size };
  } catch (error) {
    return { error: error && error.code === "ENOENT" ? "missing" : "cannot be read" };
  } finally {
    if (descriptor !== undefined) {
      try { fs.closeSync(descriptor); } catch (_error) { /* nothing to recover */ }
    }
  }
}

function revisionStructure(value, label, revisionFields, errors) {
  if (!exactFields(value, revisionFields, label, errors)) return false;
  if (!Number.isInteger(value.revision) || value.revision <= 0) {
    errors.push(`${label}.revision must be a positive integer`);
  }
  for (const field of ["source_path", "snapshot_path"]) {
    if (typeof value[field] !== "string" || !value[field].trim()) {
      errors.push(`${label}.${field} must be a non-empty string`);
    }
  }
  if (typeof value.content_hash !== "string" || !/^sha256:[0-9a-f]{64}$/.test(value.content_hash)) {
    errors.push(`${label}.content_hash must be sha256:<64 lowercase hex>`);
  }
  if (!Number.isInteger(value.size_bytes) || value.size_bytes < 0) {
    errors.push(`${label}.size_bytes must be a non-negative integer`);
  }
  if (utcTimestamp(value.registered_at) === null) {
    errors.push(`${label}.registered_at must be a timezone-explicit UTC timestamp`);
  }
  return true;
}

function artifactReference(label, artifactId, revision, revisionFields) {
  const reference = { label, artifact_id: artifactId };
  for (const field of revisionFields) reference[field] = revision[field];
  return reference;
}

function stableReference(reference) {
  if (!plainObject(reference)) return JSON.stringify(reference);
  return JSON.stringify(Object.fromEntries(
    Object.keys(reference).sort().map((key) => [key, reference[key]]),
  ));
}

function latestApprovalDecision(record) {
  if (!plainObject(record) || !Array.isArray(record.history)) return null;
  return [...record.history].reverse().find(
    (decision) => plainObject(decision) && decision.action === "approve",
  ) || null;
}

function mutableAfterApprovalRoles(policy, gate, target, approval) {
  if (!plainObject(approval)) return new Set();
  const spec = policy.gates[gate];
  const contract = plainObject(spec && spec.approval_modes)
    ? spec.approval_modes[approval.approval_mode]
    : plainObject(spec && spec.approval_targets)
      ? spec.approval_targets[target]
      : null;
  if (!plainObject(contract)) return new Set();
  return new Set(
    Array.isArray(contract.mutable_after_approval_roles)
      ? contract.mutable_after_approval_roles.filter((role) => typeof role === "string")
      : [],
  );
}

function verifyReferenceFile(context, reference, label, kind, errors) {
  const stored = reference && reference[`${kind}_path`];
  const candidate = resolvedStoredPath(context.root, stored);
  if (!candidate) {
    errors.push(`${label}.${kind}_path cannot be resolved`);
    return;
  }
  const snapshotRoot = path.resolve(context.root, context.policy.artifact_layout.snapshot_root);
  if (kind === "snapshot") {
    if (path.isAbsolute(stored) || !pathWithin(candidate, snapshotRoot)) {
      errors.push(`${label}.snapshot_path must be project-relative under policy snapshot_root`);
      return;
    }
    try {
      const physicalSnapshot = fs.realpathSync.native(candidate);
      const physicalRoot = fs.realpathSync.native(snapshotRoot);
      const physicalProject = fs.realpathSync.native(context.root);
      const physicalResearch = fs.realpathSync.native(path.join(context.root, ".research"));
      if (!pathWithin(physicalResearch, physicalProject)
        || !pathWithin(physicalRoot, physicalResearch)
        || !pathWithin(physicalSnapshot, physicalRoot)) {
        errors.push(`${label}.snapshot_path escapes policy snapshot_root through a symlink`);
        return;
      }
    } catch (_error) {
      // hashFileWithSize below reports a stable missing/unreadable diagnostic.
    }
  } else {
    const controls = [
      context.statePath,
      path.join(context.root, ".research", "state.lock"),
      context.memoryPath,
      path.join(context.root, ".research", "dashboard.html"),
      path.join(context.root, ".research", "project-state.yaml"),
    ].map((item) => path.resolve(item));
    if (pathWithin(candidate, snapshotRoot)
      || controls.some((control) => candidate === control || samePhysicalFile(candidate, control))) {
      errors.push(`${label}.source_path points to research control or snapshot data`);
      return;
    }
  }
  const actual = hashFileWithSize(candidate);
  if (actual.error) {
    errors.push(`${label} ${kind} is ${actual.error}: ${stored}`);
  } else if (actual.contentHash !== reference.content_hash || actual.sizeBytes !== reference.size_bytes) {
    errors.push(`${label} ${kind} mismatch: expected ${reference.content_hash} / ${reference.size_bytes} bytes`);
  }
}

function integrityGateScope(policy, requestedGates) {
  const sequence = approvalSequence(policy);
  const scoped = new Set();
  for (const requested of requestedGates) {
    const gate = typeof requested === "string" ? requested : requested && requested.gate;
    const target = typeof requested === "string" ? null : (requested && requested.target) ?? null;
    const index = sequence.findIndex(
      (reference) => reference.gate === gate && reference.target === target,
    );
    if (index < 0) continue;
    for (const reference of sequence.slice(0, index + 1)) {
      scoped.add(gateRefKey(reference.gate, reference.target));
    }
  }
  return sequence.filter((reference) => scoped.has(gateRefKey(reference.gate, reference.target)));
}

function verifyApprovedGateIntegrity(context, gateIds, errors) {
  if (!plainObject(context.state.gates)) return;
  for (const reference of integrityGateScope(context.policy, gateIds)) {
    const { gate, target } = reference;
    const record = gateRecord(context.state, context.policy, gate, target);
    if (!plainObject(record) || record.status !== "approved") continue;
    const approval = latestApprovalDecision(record);
    if (!plainObject(approval) || !Array.isArray(approval.artifact_refs)) continue;
    const mutableRoles = mutableAfterApprovalRoles(
      context.policy, gate, target, approval,
    );
    for (const [index, reference] of approval.artifact_refs.entries()) {
      if (!plainObject(reference)) continue;
      const label = `approved Gate ${gate}${target ? `/${target}` : ""} artifact_refs[${index}]`;
      verifyReferenceFile(context, reference, label, "snapshot", errors);
      if (!mutableRoles.has(referenceRole(reference))) {
        verifyReferenceFile(context, reference, label, "source", errors);
      }
    }
  }
}

function validateArtifactRegistry(context, errors) {
  const { policy, runtime, state } = context;
  const entryFields = runtime.artifact.entry_fields;
  const revisionFields = runtime.artifact.revision_fields;
  const current = new Map();
  const revisionsByKey = new Map();
  const snapshotOwners = new Map();
  const artifacts = state.artifacts;
  if (!plainObject(artifacts)) {
    errors.push("artifacts must be an object using the v2 revision registry");
    return { current, revisionsByKey };
  }
  for (const [stage, stageBucket] of Object.entries(artifacts)) {
    const stageLabel = `artifacts.${stage}`;
    if (!stageOrder(policy).includes(stage)) {
      errors.push(`${stageLabel} uses an unknown stage`);
      continue;
    }
    if (!plainObject(stageBucket)) {
      errors.push(`${stageLabel} must be a role mapping`);
      continue;
    }
    for (const [role, roleBucket] of Object.entries(stageBucket)) {
      const roleLabel = `${stageLabel}.${role}`;
      if (!/^[a-z][a-z0-9_]*$/.test(role)) {
        errors.push(`${roleLabel} role must use lower_snake_case`);
        continue;
      }
      if (!plainObject(roleBucket)) {
        errors.push(`${roleLabel} must be an artifact-ID mapping`);
        continue;
      }
      if (Object.keys(roleBucket).length !== 1) {
        errors.push(`${roleLabel} must contain exactly one canonical artifact ID`);
      }
      for (const [artifactId, entry] of Object.entries(roleBucket)) {
        const entryLabel = `${roleLabel}.${artifactId}`;
        if (!/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(artifactId)) {
          errors.push(`${entryLabel} has an invalid artifact ID`);
          continue;
        }
        if (!exactFields(entry, entryFields, entryLabel, errors)) continue;
        if (!Number.isInteger(entry.current_revision) || entry.current_revision <= 0) {
          errors.push(`${entryLabel}.current_revision must be a positive integer`);
        }
        if (!Array.isArray(entry.revisions) || !entry.revisions.length) {
          errors.push(`${entryLabel}.revisions must be a non-empty list`);
          continue;
        }
        if (entry.current_revision !== entry.revisions.length) {
          errors.push(`${entryLabel}.current_revision must identify the final revision`);
        }
        let priorRegisteredAt = null;
        for (const [index, revision] of entry.revisions.entries()) {
          const revisionLabel = `${entryLabel}.revisions[${index}]`;
          if (!revisionStructure(revision, revisionLabel, revisionFields, errors)) continue;
          if (revision.revision !== index + 1) {
            errors.push(`${entryLabel}.revisions must be contiguous and ordered from 1`);
          }
          const registeredAt = utcTimestamp(revision.registered_at);
          if (registeredAt !== null && priorRegisteredAt !== null && registeredAt <= priorRegisteredAt) {
            errors.push(`${revisionLabel}.registered_at must be later than the prior revision`);
          }
          if (registeredAt !== null) priorRegisteredAt = registeredAt;
          const reference = artifactReference(entryLabel, artifactId, revision, revisionFields);
          revisionsByKey.set(`${entryLabel}@${revision.revision}`, reference);
          if (typeof revision.snapshot_path === "string") {
            const owner = snapshotOwners.get(revision.snapshot_path);
            if (owner) errors.push(`${revisionLabel}.snapshot_path duplicates immutable snapshot owned by ${owner}`);
            else snapshotOwners.set(revision.snapshot_path, revisionLabel);
          }
          if (revision.revision === entry.current_revision) current.set(entryLabel, reference);
        }
      }
    }
  }
  return { current, revisionsByKey };
}

function referenceRole(reference) {
  if (!plainObject(reference) || typeof reference.label !== "string") return null;
  const match = /^artifacts\.([^.]+)\.([^.]+)\.([A-Za-z0-9][A-Za-z0-9._:-]*)$/.exec(reference.label);
  if (!match || reference.artifact_id !== match[3]) return null;
  return `${match[1]}.${match[2]}`;
}

function gateRoleContract(policy, gate, target, decision) {
  const spec = policy.gates[gate];
  if (plainObject(spec.approval_targets)) {
    const contract = spec.approval_targets[target];
    const roles = plainObject(contract) ? contract.required_artifact_roles : null;
    return { required: Array.isArray(roles) ? roles : [], optional: [] };
  }
  if (plainObject(spec.approval_modes)) {
    const contract = spec.approval_modes[decision.approval_mode];
    return {
      required: plainObject(contract) && Array.isArray(contract.required_artifact_roles)
        ? contract.required_artifact_roles : [],
      optional: plainObject(contract) && Array.isArray(contract.waivable_historical_roles)
        ? contract.waivable_historical_roles : [],
    };
  }
  return {
    required: Array.isArray(spec.required_artifact_roles) ? spec.required_artifact_roles : [],
    optional: [],
  };
}

function validateReference(context, reference, label, registry, errors) {
  const revisionFields = context.runtime.artifact.revision_fields;
  const fields = [
    ...context.runtime.artifact.reference_prefix_fields,
    ...revisionFields,
  ];
  if (!exactFields(reference, fields, label, errors)) return null;
  if (!referenceRole(reference)) errors.push(`${label}.label or artifact_id is invalid`);
  revisionStructure(
    Object.fromEntries(revisionFields.map((field) => [field, reference[field]])),
    label,
    revisionFields,
    errors,
  );
  const registered = registry.revisionsByKey.get(`${reference.label}@${reference.revision}`);
  if (!registered || stableReference(registered) !== stableReference(reference)) {
    errors.push(`${label} does not match a registered immutable artifact revision`);
  }
  return referenceRole(reference);
}

function equalReferenceLists(left, right) {
  if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
  return left.every((reference, index) => stableReference(reference) === stableReference(right[index]));
}

function validateDecisionDefense(decision, prefix, errors) {
  for (const [field, required] of [
    ["supporting_evidence_ids", true],
    ["opposing_evidence_ids", false],
    ["unresolved_risks", false],
    ["decision_conditions", true],
  ]) {
    const values = decision[field];
    if (!Array.isArray(values)
      || values.some((value) => typeof value !== "string" || !value.trim())) {
      errors.push(`${prefix}.${field} must be a string list`);
    } else if (required && !values.length) {
      errors.push(`${prefix}.${field} must not be empty`);
    } else if (new Set(values).size !== values.length) {
      errors.push(`${prefix}.${field} must not contain duplicates`);
    }
  }
}

function approvalBefore(record, timestamp) {
  if (!plainObject(record) || !Array.isArray(record.history)) return false;
  const prior = record.history.filter((decision) => (
    plainObject(decision) && utcTimestamp(decision.decided_at) !== null
      && utcTimestamp(decision.decided_at) < timestamp
  ));
  return Boolean(prior.length) && prior[prior.length - 1].new_status === "approved";
}

function validateCascadeContractV2(policy, decisionsById, errors) {
  const sequence = approvalSequence(policy);
  const events = [...decisionsById.entries()]
    .map(([id, item]) => ({
      id,
      ...item,
      key: gateRefKey(item.gate, item.target),
      at: utcTimestamp(item.decision.decided_at),
    }))
    .filter((event) => event.at !== null)
    .sort((left, right) => (left.at < right.at ? -1
      : left.at > right.at ? 1 : left.id.localeCompare(right.id)));
  const statusBefore = (key, timestamp) => {
    const prior = events.filter((event) => event.key === key && event.at < timestamp);
    return prior.length ? prior[prior.length - 1].decision.new_status : null;
  };
  const roots = events.filter((event) => event.decision.action === "reopen"
    && !Object.prototype.hasOwnProperty.call(event.decision, "cascade"));
  const linkedIds = new Set();
  for (const root of roots) {
    const linked = events.filter((event) => plainObject(event.decision.cascade)
      && event.decision.cascade.upstream_decision_id === root.id)
      .sort((left, right) => (left.at < right.at ? -1
        : left.at > right.at ? 1 : left.id.localeCompare(right.id)));
    linked.forEach((event) => linkedIds.add(event.id));
    const boundary = linked.length ? linked[0].at : root.at;
    const upstreamIndex = sequence.findIndex(
      (reference) => gateRefKey(reference.gate, reference.target) === root.key,
    );
    const expected = [...sequence.slice(upstreamIndex + 1)].reverse()
      .filter((reference) => statusBefore(
        gateRefKey(reference.gate, reference.target), boundary,
      ) === "approved")
      .map((reference) => gateRefKey(reference.gate, reference.target));
    if (linked.map((event) => event.key).join("\0") !== expected.join("\0")) {
      errors.push(`cascade for ${root.id} must reopen exactly the approved downstream GateRefs in reverse approval sequence`);
    }
    let priorAt = null;
    for (const event of linked) {
      const cascade = event.decision.cascade;
      if (priorAt !== null && event.at <= priorAt) {
        errors.push(`cascade for ${root.id} timestamps must be strictly increasing`);
      }
      priorAt = event.at;
      if (event.decision.action !== "reopen") {
        errors.push(`cascade decision ${event.id} must be a downstream reopen`);
      }
      if (stableReference(cascade.upstream_gate_ref)
          !== stableReference(gateRefObject(root.gate, root.target))
        || cascade.upstream_reason !== root.decision.reason) {
        errors.push(`cascade decision ${event.id} provenance does not match its upstream GateRef`);
      }
      for (const field of [
        "supporting_evidence_ids", "opposing_evidence_ids",
        "unresolved_risks", "decision_conditions",
      ]) {
        if (stableReference(event.decision[field])
          !== stableReference(root.decision[field])) {
          errors.push(`cascade decision ${event.id} ${field} does not match its upstream decision`);
        }
      }
      if (event.at >= root.at) {
        errors.push(`cascade decision ${event.id} must precede its upstream reopen decision`);
      }
    }
    const linkedSet = new Set(linked.map((event) => event.id));
    if (events.some((event) => event.at >= boundary && event.at < root.at
      && !linkedSet.has(event.id))) {
      errors.push(`cascade for ${root.id} has interleaved Gate decisions`);
    }
  }
  for (const event of events) {
    if (plainObject(event.decision.cascade) && !linkedIds.has(event.id)) {
      errors.push(`cascade decision ${event.id} references an unknown or invalid upstream reopen`);
    }
  }
}

function validateGateRecordV2(
  context, registry, gate, target, record, decisionsById, errors,
) {
  const { policy, runtime } = context;
  const label = `${gate}${target ? `/${target}` : ""}`;
  if (!exactFields(record, runtime.gate.record_fields, `Gate ${label}`, errors)) return;
  const statuses = new Set(runtime.gate.statuses);
  const actions = new Set(runtime.gate.actions);
  if (!statuses.has(record.status)) errors.push(`Gate ${label} has invalid status`);
  if (!Array.isArray(record.history)) {
    errors.push(`Gate ${label} history must be an array`);
    return;
  }
  if (!record.history.length) {
    if (record.status !== "pending" || record.latest_decision_id !== null) {
      errors.push(`Gate ${label} without history must be pending with null latest_decision_id`);
    }
    return;
  }
  let expectedStatus = "pending";
  let priorDecisionAt = null;
  let latestApproval = null;
  let latestApprovedRefs = null;
  for (const [index, decision] of record.history.entries()) {
    const prefix = `Gate ${label} history[${index}]`;
    if (!plainObject(decision)) {
      errors.push(`${prefix} must be an object`);
      continue;
    }
    const requiredFields = runtime.decision.required_fields;
    const optionalFields = runtime.gate.decision_optional_fields;
    const missing = requiredFields.filter(
      (field) => !Object.prototype.hasOwnProperty.call(decision, field),
    );
    const unknown = Object.keys(decision).filter(
      (field) => !requiredFields.includes(field) && !optionalFields.includes(field),
    );
    if (missing.length) errors.push(`${prefix} missing fields: ${missing.sort().join(", ")}`);
    if (unknown.length) errors.push(`${prefix} has unknown fields: ${unknown.sort().join(", ")}`);
    if (typeof decision.decision_id !== "string" || !decision.decision_id.trim()) {
      errors.push(`${prefix} needs a decision_id`);
    } else if (decisionsById.has(decision.decision_id)) {
      errors.push(`${prefix} duplicates a decision_id`);
    } else decisionsById.set(decision.decision_id, { gate, target, decision });
    if (!actions.has(decision.action)) errors.push(`${prefix} has an invalid action`);
    if (Object.prototype.hasOwnProperty.call(decision, "cascade")) {
      if (decision.action !== "reopen") errors.push(`${prefix} cascade is valid only for reopen`);
      if (exactFields(
        decision.cascade,
        runtime.gate.cascade_fields,
        `${prefix}.cascade`,
        errors,
      )) {
        if (!parseGateRef(policy, runtime, decision.cascade.upstream_gate_ref)) {
          errors.push(`${prefix}.cascade upstream_gate_ref is invalid`);
        }
        for (const field of ["upstream_decision_id", "upstream_reason"]) {
          if (typeof decision.cascade[field] !== "string"
            || !decision.cascade[field].trim()) {
            errors.push(`${prefix}.cascade ${field} must be non-empty`);
          }
        }
      }
    }
    if (!statuses.has(decision.previous_status)
      || decision.previous_status !== expectedStatus) {
      errors.push(`${prefix} does not continue the Gate status chain`);
    }
    if (!statuses.has(decision.new_status)) errors.push(`${prefix} has invalid new_status`);
    if (decision.action === "approve"
      && (decision.previous_status === "approved" || decision.new_status !== "approved")) {
      errors.push(`${prefix} has an invalid approve transition`);
    }
    if (decision.action === "reopen"
      && (decision.previous_status !== "approved" || decision.new_status !== "reopened")) {
      errors.push(`${prefix} has an invalid reopen transition`);
    }
    if (statuses.has(decision.new_status)) expectedStatus = decision.new_status;
    for (const field of ["reason", "actor"]) {
      if (typeof decision[field] !== "string" || !decision[field].trim()) {
        errors.push(`${prefix} needs a ${field}`);
      }
    }
    validateDecisionDefense(decision, prefix, errors);
    const decidedAt = utcTimestamp(decision.decided_at);
    if (decidedAt === null) errors.push(`${prefix} needs a UTC decided_at`);
    else if (priorDecisionAt !== null && decidedAt <= priorDecisionAt) {
      errors.push(`${prefix} must be later than the prior decision`);
    } else priorDecisionAt = decidedAt;
    const artifactRefs = Array.isArray(decision.artifact_refs) ? decision.artifact_refs : [];
    if (!Array.isArray(decision.artifact_refs)) {
      errors.push(`${prefix}.artifact_refs must be an array`);
    }
    const roles = [];
    const labels = new Set();
    for (const [refIndex, reference] of artifactRefs.entries()) {
      const refPrefix = `${prefix}.artifact_refs[${refIndex}]`;
      const role = validateReference(context, reference, refPrefix, registry, errors);
      if (role) roles.push(role);
      if (plainObject(reference) && typeof reference.label === "string") {
        if (labels.has(reference.label)) errors.push(`${refPrefix} duplicates label ${reference.label}`);
        labels.add(reference.label);
        const registeredAt = utcTimestamp(reference.registered_at);
        if (decision.action === "approve" && decidedAt !== null
          && registeredAt !== null && registeredAt >= decidedAt) {
          errors.push(`${refPrefix}.registered_at must be earlier than the approval decision`);
        }
      }
    }

    if (decision.action === "approve") {
      const modeSpecs = policy.gates[gate].approval_modes;
      if (plainObject(modeSpecs)) {
        if (typeof decision.approval_mode !== "string"
          || !plainObject(modeSpecs[decision.approval_mode])) {
          errors.push(`${prefix} must name a configured approval_mode`);
        }
      } else if (Object.prototype.hasOwnProperty.call(decision, "approval_mode")) {
        errors.push(`${prefix} must not define approval_mode`);
      }
      const contract = gateRoleContract(policy, gate, target, decision);
      const allowed = new Set([...contract.required, ...contract.optional]);
      for (const role of contract.required) {
        if (roles.filter((candidate) => candidate === role).length !== 1) {
          errors.push(`${prefix} must bind exactly one current artifact for ${role}`);
        }
      }
      if (new Set(roles).size !== roles.length) {
        errors.push(`${prefix} must bind exactly one canonical artifact per role`);
      }
      for (const role of roles) {
        if (!allowed.has(role)) errors.push(`${prefix} binds unexpected artifact role ${role}`);
      }
      latestApproval = decision;
      latestApprovedRefs = artifactRefs;

      const selectionRole = policy.gates[gate].selection_artifact_role;
      if (typeof selectionRole === "string") {
        if (!plainObject(decision.selection)
          || !exactFields(
            decision.selection, runtime.gate.selection_fields, `${prefix}.selection`, errors,
          )) {
          errors.push(`${prefix} requires a structured selection`);
        } else {
          if (typeof decision.selection.selected_id !== "string"
            || !decision.selection.selected_id.trim()) {
            errors.push(`${prefix}.selection.selected_id must be a non-empty candidate ID`);
          }
          const selectedRole = validateReference(
            context, decision.selection.artifact_ref,
            `${prefix}.selection.artifact_ref`, registry, errors,
          );
          if (selectedRole !== selectionRole) {
            errors.push(`${prefix}.selection must bind ${selectionRole}`);
          }
          if (!artifactRefs.some((reference) => stableReference(reference)
              === stableReference(decision.selection.artifact_ref))) {
            errors.push(`${prefix}.selection.artifact_ref must equal the Gate-bound portfolio revision`);
          }
        }
      } else if (Object.prototype.hasOwnProperty.call(decision, "selection")) {
        errors.push(`${prefix} must not define selection`);
      }

      if (decision.approval_mode === retrospectiveApprovalMode(policy.gates[gate])) {
        const expectedWaived = contract.optional.filter((role) => !roles.includes(role));
        if (!expectedWaived.length) {
          errors.push(`${prefix} retrospective mode must waive unavailable historical roles`);
        }
        if (!Array.isArray(decision.waived_artifact_roles)
          || decision.waived_artifact_roles.join("\0") !== expectedWaived.join("\0")) {
          errors.push(`${prefix}.waived_artifact_roles do not match absent optional roles`);
        }
      } else if (Object.prototype.hasOwnProperty.call(decision, "waived_artifact_roles")) {
        errors.push(`${prefix} waived_artifact_roles are valid only for retrospective mode`);
      }
    } else {
      if (latestApprovedRefs !== null
        && !equalReferenceLists(artifactRefs, latestApprovedRefs)) {
        errors.push(`${prefix} reopen artifact_refs must match the latest approval`);
      }
      for (const field of ["selection", "approval_mode", "waived_artifact_roles"]) {
        if (Object.prototype.hasOwnProperty.call(decision, field)) {
          errors.push(`${prefix}.${field} is valid only for approval`);
        }
      }
    }
  }
  const last = record.history[record.history.length - 1];
  if (!plainObject(last) || last.decision_id !== record.latest_decision_id) {
    errors.push(`Gate ${label} latest_decision_id does not match history`);
  }
  if (!plainObject(last) || last.new_status !== record.status) {
    errors.push(`Gate ${label} status does not match history`);
  }
  if (record.status === "approved" && plainObject(latestApproval)) {
    const mutableRoles = mutableAfterApprovalRoles(policy, gate, target, latestApproval);
    for (const reference of latestApproval.artifact_refs || []) {
      const current = registry.current.get(reference.label);
      if (mutableRoles.has(referenceRole(reference))) {
        if (!current || current.artifact_id !== reference.artifact_id) {
          errors.push(`approved Gate ${label} mutable artifact identity changed for ${reference.label}`);
        }
      } else if (!current || stableReference(current) !== stableReference(reference)) {
        errors.push(`approved Gate ${label} does not bind the current artifact revision for ${reference.label}`);
      }
    }
  }
}

function validateGateRecordsV2(context, registry, errors) {
  const { state, policy, runtime } = context;
  const gates = state.gates;
  const decisionsById = new Map();
  if (!plainObject(gates)) {
    errors.push("gates must be an object");
    return decisionsById;
  }
  const orderedGates = gateOrder(policy);
  for (const gate of orderedGates) {
    if (!Object.prototype.hasOwnProperty.call(gates, gate)) errors.push(`missing Gate: ${gate}`);
  }
  for (const gate of Object.keys(gates)) {
    if (!orderedGates.includes(gate)) errors.push(`unknown Gate: ${gate}`);
  }
  for (const gate of orderedGates) {
    const targets = approvalTargets(policy, gate);
    const container = gates[gate];
    if (targets.length) {
      if (!exactFields(
        container, runtime.gate.target_container_fields, `targeted Gate ${gate}`, errors,
      )
        || !plainObject(container.targets)) continue;
      const actualTargets = Object.keys(container.targets);
      if (actualTargets.length !== targets.length
        || targets.some((target) => !actualTargets.includes(target))) {
        errors.push(`Gate ${gate}.targets must define exactly ${targets.join(", ")}`);
        continue;
      }
      for (const target of targets) {
        validateGateRecordV2(
          context, registry, gate, target, container.targets[target], decisionsById, errors,
        );
      }
    } else {
      validateGateRecordV2(context, registry, gate, null, container, decisionsById, errors);
    }
  }

  validateCascadeContractV2(policy, decisionsById, errors);
  const sequence = approvalSequence(policy);
  for (const [index, reference] of sequence.entries()) {
    const record = gateRecord(state, policy, reference.gate, reference.target);
    const prerequisites = sequence.slice(0, index);
    const label = `${reference.gate}${reference.target ? `/${reference.target}` : ""}`;
    if (plainObject(record) && record.status === "approved") {
      for (const prerequisite of prerequisites) {
        const prerequisiteRecord = gateRecord(
          state, policy, prerequisite.gate, prerequisite.target,
        );
        if (!plainObject(prerequisiteRecord) || prerequisiteRecord.status !== "approved") {
          errors.push(`approved Gate ${label} requires approved Gate ${prerequisite.gate}${prerequisite.target ? `/${prerequisite.target}` : ""}`);
        }
      }
    }
    if (!plainObject(record) || !Array.isArray(record.history)) continue;
    for (const [historyIndex, decision] of record.history.entries()) {
      if (!plainObject(decision) || decision.action !== "approve") continue;
      const decidedAt = utcTimestamp(decision.decided_at);
      if (decidedAt === null) continue;
      for (const prerequisite of prerequisites) {
        const prerequisiteRecord = gateRecord(
          state, policy, prerequisite.gate, prerequisite.target,
        );
        if (!approvalBefore(prerequisiteRecord, decidedAt)) {
          errors.push(`Gate ${label} history[${historyIndex}] approval lacks prior approval of prerequisite Gate ${prerequisite.gate}${prerequisite.target ? `/${prerequisite.target}` : ""}`);
        }
      }
    }
  }
  return decisionsById;
}

function validateLifecycleV2(context, registry, gateDecisions, errors) {
  const { lifecycle } = context.state;
  const { runtime, policy } = context;
  if (!exactFields(
    lifecycle, runtime.lifecycle.record_fields, "lifecycle", errors,
  )) return;
  if (!runtime.lifecycle.statuses.includes(lifecycle.status)) {
    errors.push("lifecycle has an invalid status");
  }
  if (!Array.isArray(lifecycle.history)) {
    errors.push("lifecycle.history must be an array");
    return;
  }
  if (!lifecycle.history.length) {
    if (lifecycle.status !== "active" || lifecycle.latest_decision_id !== null) {
      errors.push("lifecycle without history must be active with null latest_decision_id");
    }
    return;
  }

  let expectedStatus = "active";
  let priorDecisionAt = null;
  const ids = new Set(gateDecisions.keys());
  const requiredFields = [
    ...runtime.decision.required_fields,
    ...runtime.lifecycle.decision_fields,
  ];
  for (const [index, decision] of lifecycle.history.entries()) {
    const prefix = `lifecycle history[${index}]`;
    if (!plainObject(decision)) {
      errors.push(`${prefix} must be an object`);
      continue;
    }
    const missing = requiredFields.filter(
      (field) => !Object.prototype.hasOwnProperty.call(decision, field),
    );
    const unknown = Object.keys(decision).filter(
      (field) => !requiredFields.includes(field)
        && !runtime.lifecycle.decision_optional_fields.includes(field),
    );
    if (missing.length) errors.push(`${prefix} missing fields: ${missing.sort().join(", ")}`);
    if (unknown.length) errors.push(`${prefix} has unknown fields: ${unknown.sort().join(", ")}`);
    if (typeof decision.decision_id !== "string" || !decision.decision_id.trim()) {
      errors.push(`${prefix} needs a decision_id`);
    } else if (ids.has(decision.decision_id)) {
      errors.push(`${prefix} duplicates a decision_id`);
    } else ids.add(decision.decision_id);

    const transition = new Map([
      ["active\0terminate", "terminated"],
      ["active\0complete", "completed"],
      ["terminated\0reopen", "active"],
      ["completed\0reopen", "active"],
    ]).get(`${decision.previous_status}\0${decision.action}`);
    if (decision.previous_status !== expectedStatus
      || decision.new_status !== transition) {
      errors.push(`${prefix} has an invalid lifecycle transition`);
    }
    if (runtime.lifecycle.statuses.includes(decision.new_status)) {
      expectedStatus = decision.new_status;
    }
    for (const field of ["reason", "actor"]) {
      if (typeof decision[field] !== "string" || !decision[field].trim()) {
        errors.push(`${prefix} needs a ${field}`);
      }
    }
    validateDecisionDefense(decision, prefix, errors);
    const decidedAt = utcTimestamp(decision.decided_at);
    if (decidedAt === null) errors.push(`${prefix} needs a UTC decided_at`);
    else if (priorDecisionAt !== null && decidedAt <= priorDecisionAt) {
      errors.push(`${prefix} must be later than the prior decision`);
    } else priorDecisionAt = decidedAt;
    if (decision.action === "complete" && decidedAt !== null) {
      const releaseGate = releaseGateId(policy);
      const initialTarget = initialReleaseTarget(policy);
      const release = gateRecord(
        context.state, policy, releaseGate, initialTarget,
      );
      const priorRelease = plainObject(release) && Array.isArray(release.history)
        ? release.history.filter((item) => plainObject(item)
          && utcTimestamp(item.decided_at) !== null
          && utcTimestamp(item.decided_at) < decidedAt)
        : [];
      if (!priorRelease.length || priorRelease.at(-1).new_status !== "approved") {
        const label = releaseGate && initialTarget
          ? `${releaseGate}/${initialTarget}` : "the initial release Gate target";
        errors.push(`${prefix} complete requires prior approved Gate ${label}`);
      }
    }
    if (!stageOrder(policy).includes(decision.stage)) {
      errors.push(`${prefix} has an unknown stage`);
    }

    if (!Array.isArray(decision.artifact_refs)) {
      errors.push(`${prefix}.artifact_refs must be an array`);
    } else if (!decision.artifact_refs.length) {
      if (decidedAt === null
        || artifactRevisionExistedBefore(context.state, decidedAt)) {
        errors.push(`${prefix}.artifact_refs must be non-empty when registered artifacts existed at decision time`);
      }
    } else {
      const labels = new Set();
      for (const [refIndex, reference] of decision.artifact_refs.entries()) {
        validateReference(
          context, reference, `${prefix}.artifact_refs[${refIndex}]`, registry, errors,
        );
        if (plainObject(reference) && typeof reference.label === "string") {
          if (labels.has(reference.label)) {
            errors.push(`${prefix}.artifact_refs[${refIndex}] duplicates a label`);
          }
          labels.add(reference.label);
        }
      }
    }

    const hasGateRef = Object.prototype.hasOwnProperty.call(decision, "gate_ref");
    const hasGateDecision = Object.prototype.hasOwnProperty.call(
      decision, "gate_decision_id",
    );
    if (hasGateRef !== hasGateDecision) {
      errors.push(`${prefix} gate_ref and gate_decision_id must appear together`);
    } else if (hasGateRef) {
      const parsed = parseGateRef(policy, runtime, decision.gate_ref);
      const linked = gateDecisions.get(decision.gate_decision_id);
      if (decision.action !== "reopen" || !parsed || !linked
        || linked.decision.action !== "reopen"
        || parsed.gate !== linked.gate || parsed.target !== linked.target) {
        errors.push(`${prefix} has an invalid affected Gate linkage`);
      }
    } else if (decision.previous_status === "completed"
      && decision.action === "reopen") {
      errors.push(`${prefix} completed reopen must link an affected Gate`);
    }
  }
  const last = plainObject(lifecycle.history.at(-1)) ? lifecycle.history.at(-1) : {};
  if (lifecycle.latest_decision_id !== last.decision_id) {
    errors.push("lifecycle latest_decision_id does not match its history");
  }
  if (lifecycle.status !== last.new_status) {
    errors.push("lifecycle status does not match its history");
  }
}

function artifactRevisionExistedBefore(state, decidedAt) {
  if (decidedAt === null || !plainObject(state.artifacts)) return false;
  for (const stageBucket of Object.values(state.artifacts)) {
    if (!plainObject(stageBucket)) continue;
    for (const roleBucket of Object.values(stageBucket)) {
      if (!plainObject(roleBucket)) continue;
      for (const entry of Object.values(roleBucket)) {
        if (!plainObject(entry) || !Array.isArray(entry.revisions)) continue;
        for (const revision of entry.revisions) {
          const registeredAt = plainObject(revision)
            ? utcTimestamp(revision.registered_at) : null;
          if (registeredAt !== null && registeredAt < decidedAt) return true;
        }
      }
    }
  }
  return false;
}

function validateActivationV2(context, errors) {
  const { activation_history: history } = context.state;
  const { runtime } = context;
  if (!Array.isArray(history)) {
    errors.push("activation_history must be an array");
    return;
  }
  let expectedEnabled = true;
  let priorAt = null;
  for (const [index, event] of history.entries()) {
    const prefix = `activation_history[${index}]`;
    if (!exactFields(event, runtime.activation.event_fields, prefix, errors)) continue;
    const expectedNew = event.action === "enable" ? true
      : event.action === "disable" ? false : null;
    if (!runtime.activation.actions.includes(event.action)) {
      errors.push(`${prefix} has an invalid action`);
    }
    if (event.previous_enabled !== expectedEnabled || event.new_enabled !== expectedNew) {
      errors.push(`${prefix} does not continue activation state`);
    }
    if (typeof event.new_enabled === "boolean") expectedEnabled = event.new_enabled;
    for (const field of ["reason", "actor"]) {
      if (typeof event[field] !== "string" || !event[field].trim()) {
        errors.push(`${prefix} needs a ${field}`);
      }
    }
    const at = utcTimestamp(event.decided_at);
    if (at === null) errors.push(`${prefix} needs a UTC decided_at`);
    else if (priorAt !== null && at <= priorAt) {
      errors.push(`${prefix} must be later than the prior event`);
    } else priorAt = at;
  }
  if (context.state.enabled !== expectedEnabled) {
    errors.push("enabled does not match activation_history");
  }
}

function utcTimestamp(value) {
  if (typeof value !== "string") return null;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(?:Z|\+00:00)$/.exec(value);
  if (!match) return null;
  const milliseconds = Date.parse(`${match[1]}Z`);
  if (Number.isNaN(milliseconds)
    || new Date(milliseconds).toISOString().slice(0, 19) !== match[1]) return null;
  const nanoseconds = BigInt((match[2] || "").padEnd(9, "0"));
  return BigInt(milliseconds) * 1000000n + nanoseconds;
}

function validateState(context, { integrityGates = [] } = {}) {
  const { state, policy, runtime } = context;
  const errors = [];
  const warnings = [];
  exactFields(state, runtime.state.required_fields, "state", errors);
  if (state.schema_version !== policy.schema_version) {
    errors.push(`schema_version ${JSON.stringify(state.schema_version)} does not match policy ${JSON.stringify(policy.schema_version)}`);
  }
  if (state.workflow_version !== policy.workflow_version) {
    errors.push(`workflow_version ${JSON.stringify(state.workflow_version)} does not match policy ${JSON.stringify(policy.workflow_version)}`);
  }
  if (typeof state.enabled !== "boolean") errors.push("enabled must be a boolean");
  if (typeof state.project_id !== "string" || !state.project_id.trim()) errors.push("project_id must be a non-empty string");
  if (typeof state.project_name !== "string" || !state.project_name.trim()) errors.push("project_name must be a non-empty string");
  const stages = stageOrder(policy);
  if (!stages.includes(state.current_stage)) {
    errors.push(`unknown current_stage: ${JSON.stringify(state.current_stage)}`);
  }
  const createdAt = utcTimestamp(state.created_at);
  const updatedAt = utcTimestamp(state.updated_at);
  if (createdAt === null) errors.push("created_at must be a timezone-explicit UTC timestamp");
  if (updatedAt === null) errors.push("updated_at must be a timezone-explicit UTC timestamp");
  if (createdAt !== null && updatedAt !== null && updatedAt < createdAt) {
    errors.push("updated_at must not be earlier than created_at");
  }

  const registry = validateArtifactRegistry(context, errors);
  const decisionsById = validateGateRecordsV2(context, registry, errors);
  validateLifecycleV2(context, registry, decisionsById, errors);
  validateActivationV2(context, errors);
  verifyApprovedGateIntegrity(context, integrityGates, errors);

  if (plainObject(state.gates) && stages.includes(state.current_stage)) {
    const currentIndex = stages.indexOf(state.current_stage);
    for (const reference of approvalSequence(policy)) {
      const destinations = [];
      for (const source of stages) {
        for (const candidate of policy.workflow_graph.stage_transitions[source]) {
          const required = transitionGateRef(policy, source, candidate.to);
          if (required && required.gate === reference.gate
            && required.target === reference.target) destinations.push(candidate.to);
        }
      }
      if (!destinations.length
        || Math.min(...destinations.map((stage) => stages.indexOf(stage))) > currentIndex) continue;
      const record = gateRecord(state, policy, reference.gate, reference.target);
      if (!plainObject(record) || record.status !== "approved") {
        errors.push(`current_stage ${state.current_stage} requires approved Gate ${reference.gate}${reference.target ? `/${reference.target}` : ""}`);
      }
    }
  }
  if (state.last_checkpoint !== null && state.last_checkpoint !== undefined) {
    const checkpoint = state.last_checkpoint;
    if (!checkpoint || typeof checkpoint !== "object" || Array.isArray(checkpoint)) {
      errors.push("last_checkpoint must be null or an object");
    } else {
      exactFields(checkpoint, runtime.checkpoint.fields, "last_checkpoint", errors);
      if (typeof checkpoint.summary !== "string" || !checkpoint.summary.trim()) {
        errors.push("last_checkpoint.summary must be non-empty");
      }
      if (typeof checkpoint.timestamp !== "string" || !checkpoint.timestamp.trim()) {
        errors.push("last_checkpoint.timestamp must be non-empty");
      } else if (utcTimestamp(checkpoint.timestamp) === null) {
        errors.push("last_checkpoint.timestamp must be a timezone-explicit UTC timestamp");
      }
    }
  }
  if (Object.prototype.hasOwnProperty.call(state, "stage_history") && !Array.isArray(state.stage_history)) {
    errors.push("stage_history must be an array");
  } else if (Array.isArray(state.stage_history)) {
    let expectedStage = stages[0];
    let priorTransitionAt = null;
    for (const [index, transition] of state.stage_history.entries()) {
      const prefix = `stage_history[${index}]`;
      if (!exactFields(
        transition, runtime.stage_transition.fields, prefix, errors,
      )) continue;
      if (transition.from_stage !== expectedStage) errors.push(`${prefix} breaks stage continuity`);
      if (!stages.includes(transition.from_stage)
        || !stages.includes(transition.to_stage)) {
        errors.push(`${prefix} contains an unknown stage`);
      } else {
        expectedStage = transition.to_stage;
      }
      if (typeof transition.trigger !== "string" || !transition.trigger.trim()) {
        errors.push(`${prefix} needs a trigger`);
      } else {
        const gatePrefixes = runtime.stage_transition.trigger_prefixes
          .filter((candidate) => candidate.startsWith("gate-"));
        const match = new RegExp(`^(${gatePrefixes.join("|")}):(.+)$`).exec(
          transition.trigger,
        );
        if (match) {
          const expectedAction = match[1].slice("gate-".length);
          const linked = decisionsById.get(match[2]);
          if (!linked) errors.push(`${prefix} references an unknown Gate decision`);
          else {
            if (linked.decision.action !== expectedAction) errors.push(`${prefix} trigger action does not match its decision`);
            if (Object.prototype.hasOwnProperty.call(linked.decision, "cascade")) errors.push(`${prefix} cascade decisions must not drive stage transitions`);
            if (expectedAction === "reopen") {
              if (linked.decision.decided_at !== transition.timestamp) errors.push(`${prefix} timestamp does not match its Gate decision`);
              const owner = gateRefOwner(policy, linked.gate, linked.target);
              if (!owner || transition.to_stage !== owner.stage) {
                errors.push(`${prefix} target does not match its GateRef owner stage`);
              }
            } else {
              const transitionAt = utcTimestamp(transition.timestamp);
              const decisionAt = utcTimestamp(linked.decision.decided_at);
              const linkedRecord = gateRecord(
                state, policy, linked.gate, linked.target,
              );
              const linkedHistory = plainObject(linkedRecord)
                && Array.isArray(linkedRecord.history) ? linkedRecord.history : [];
              const decisionsAtTransition = linkedHistory.filter((decision) => {
                const decidedAt = plainObject(decision)
                  ? utcTimestamp(decision.decided_at) : null;
                return decidedAt !== null
                  && transitionAt !== null
                  && decidedAt <= transitionAt;
              });
              const activeDecision = decisionsAtTransition.at(-1);
              if (decisionAt === null
                || transitionAt === null
                || decisionAt > transitionAt
                || !plainObject(activeDecision)
                || activeDecision.decision_id !== match[2]
                || activeDecision.new_status !== "approved") {
                errors.push(`${prefix} must use the active Gate approval at the transition timestamp`);
              }
              const required = transitionGateRef(
                policy, transition.from_stage, transition.to_stage,
              );
              if (!required || required.gate !== linked.gate
                || required.target !== linked.target) {
                errors.push(`${prefix} target does not match its GateRef transition`);
              }
            }
          }
        } else if (!runtime.stage_transition.trigger_prefixes.includes(
          transition.trigger,
        )) errors.push(`${prefix} has unsupported trigger`);
        else {
          const rule = transitionRule(policy, transition.from_stage, transition.to_stage);
          if (!rule || rule.trigger.type !== "checkpoint") {
            errors.push(`${prefix} checkpoint cannot drive a stage-exit transition`);
          }
        }
      }
      const transitionAt = utcTimestamp(transition.timestamp);
      if (transitionAt === null) errors.push(`${prefix} needs a UTC timestamp`);
      else if (priorTransitionAt !== null && transitionAt <= priorTransitionAt) {
        errors.push(`${prefix} must be later than the prior transition`);
      } else priorTransitionAt = transitionAt;
    }
    if (expectedStage !== state.current_stage) {
      errors.push("current_stage does not match stage_history");
    }
  }
  if (!isFile(context.memoryPath)) errors.push("missing .research/memory.md");
  return { errors: [...new Set(errors)], warnings: [...new Set(warnings)] };
}

function postToolUse(context, input) {
  if (!stateWasTouched(context, input)) return {};
  const result = validateState(context, { integrityGates: approvalSequence(context.policy) });
  const lines = [
    "[POST-TOOL RESEARCH STATE QUICK CHECK]",
    result.errors.length
      ? `Detected mechanical state, revision, snapshot, or Gate errors (${result.errors.length}):\n${listLines(result.errors)}`
      : "Mechanical state, current-source, immutable-snapshot, and Gate checks found no issue.",
    result.warnings.length
      ? `Warnings (${result.warnings.length}):\n${listLines(result.warnings)}`
      : "researchctl doctor remains the authoritative full CLI diagnostic.",
  ];
  if (result.errors.length) {
    lines.push("Do not treat the state as authoritative until researchctl doctor passes. Repair it through researchctl; never hand-edit Gate fields.");
  }
  return hookContextOutput("PostToolUse", bounded(lines.join("\n"), MAX_POST_CONTEXT_CHARS));
}

function getStopHookActive(input) {
  return firstDefined(input, ["stop_hook_active", "stopHookActive"]) === true;
}

function structuredWorkflowAssertion(input) {
  const direct = firstDefined(input, [
    "workflow_assertions",
    "workflowAssertions",
    "assistant_workflow_state",
    "assistantWorkflowState",
  ]);
  return plainObject(direct) ? direct : null;
}

function structuredStateContradictions(context, assertion) {
  const issues = [];
  if (Object.prototype.hasOwnProperty.call(assertion, "current_stage")
    && assertion.current_stage !== context.state.current_stage) {
    issues.push(`structured current_stage=${JSON.stringify(assertion.current_stage)}; state says ${JSON.stringify(context.state.current_stage)}`);
  }
  const exit = stageExitRequirement(context.policy, context.state.current_stage);
  const expectedExit = exit ? gateRefObject(exit.gate, exit.target) : null;
  if (Object.prototype.hasOwnProperty.call(assertion, "stage_exit_requirement")
    && stableReference(assertion.stage_exit_requirement) !== stableReference(expectedExit)) {
    issues.push(`structured stage_exit_requirement=${JSON.stringify(assertion.stage_exit_requirement)}; policy says ${JSON.stringify(expectedExit)}`);
  }
  if (Object.prototype.hasOwnProperty.call(assertion, "gate_to_exit")) {
    issues.push("structured gate_to_exit is obsolete; use stage_exit_requirement");
  }
  if (Object.prototype.hasOwnProperty.call(assertion, "gates")) {
    if (!plainObject(assertion.gates)) {
      issues.push("structured gates must be an object");
    } else {
      for (const [label, status] of Object.entries(assertion.gates)) {
        const [gate, targetValue, ...extra] = label.split("/");
        const target = targetValue || null;
        if (extra.length || !gateRefOwner(context.policy, gate, target)) {
          issues.push(`structured gates contains unknown GateRef ${label}`);
          continue;
        }
        const actual = gateStatus(context, gate, target);
        if (status !== actual) {
          issues.push(`structured ${label}=${JSON.stringify(status)}; state says ${JSON.stringify(actual)}`);
        }
      }
    }
  }
  return issues;
}

function stopAudit(context, input) {
  if (getStopHookActive(input)) return {};
  const stateCheck = validateState(context);
  if (stateCheck.errors.length) {
    return {
      continue: true,
      systemMessage: bounded([
        "[RESEARCH STOP OBSERVER] Active research state failed mechanical validation.",
        listLines(stateCheck.errors.slice(0, 4)),
        "Run researchctl doctor before relying on stage or Gate metadata. The assistant response was not parsed or replaced.",
      ].join("\n"), MAX_STOP_REASON_CHARS),
    };
  }
  const assertion = structuredWorkflowAssertion(input);
  if (!assertion) return {};
  const contradictions = structuredStateContradictions(context, assertion);
  if (!contradictions.length) return {};
  return {
    decision: "block",
    reason: bounded([
      "Explicit structured workflow metadata contradicts .research/state.json or policy.yaml:",
      listLines(contradictions),
      "Return one self-contained corrected answer. stop_hook_active prevents a repeat loop.",
    ].join("\n"), MAX_STOP_REASON_CHARS),
  };
}

function handleEvent(event, context, input) {
  if (!context.policy) {
    return event === "PreToolUse" ? invalidPolicyPreToolUse(context, input) : {};
  }
  switch (event) {
    case "SessionStart":
      return hookContextOutput("SessionStart", sessionContext(context));
    case "UserPromptSubmit":
      return hookContextOutput("UserPromptSubmit", promptContext(context));
    case "PreToolUse":
      return preToolUse(context, input);
    case "PostToolUse":
      return postToolUse(context, input);
    case "Stop":
      return stopAudit(context, input);
    default:
      return {};
  }
}

async function main() {
  const raw = await readStdin();
  const input = parseObject(raw);
  if (!input) {
    process.stdout.write("{}");
    return;
  }
  const event = eventName(input);
  if (!event) {
    process.stdout.write("{}");
    return;
  }
  const context = activeProject(input);
  if (!context) {
    process.stdout.write("{}");
    return;
  }
  process.stdout.write(JSON.stringify(handleEvent(event, context, input)));
}

main().catch(() => {
  process.stdout.write("{}");
});
