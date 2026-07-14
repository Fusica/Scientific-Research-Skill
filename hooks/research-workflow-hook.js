#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

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
// Event fields are already bounded by the complete stdin envelope. Keep their
// contents position-invariant below that envelope cap instead of silently
// ignoring a mechanically relevant statement placed near the end.
const MAX_EVENT_TEXT_CHARS = MAX_INPUT_BYTES;
const MAX_SESSION_CONTEXT_CHARS = 800;
const MAX_PROMPT_CONTEXT_CHARS = 2600;
const MAX_POST_CONTEXT_CHARS = 4200;
const MAX_STOP_REASON_CHARS = 1800;
const MAX_TOOL_TEXT_CHARS = MAX_INPUT_BYTES;
const REQUIRED_STATE_FIELDS = [
  "schema_version",
  "workflow_version",
  "enabled",
  "project_id",
  "project_name",
  "current_stage",
  "gates",
  "artifacts",
  "last_checkpoint",
  "stage_history",
  "created_at",
  "updated_at",
];

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

function parseObject(raw) {
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    const value = JSON.parse(raw.replace(/^\uFEFF/, ""));
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

function inputText(input, keys) {
  const value = firstDefined(input, keys);
  return typeof value === "string" ? cleanText(value).slice(0, MAX_EVENT_TEXT_CHARS) : "";
}

function userPrompt(input) {
  return inputText(input, ["prompt", "user_prompt", "userPrompt"]);
}

function lastAssistantMessage(input) {
  return inputText(input, [
    "last_assistant_message",
    "lastAssistantMessage",
    "assistant_message",
    "assistantMessage",
  ]);
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

function loadPolicy() {
  const candidate = path.join(
    pluginRoot(),
    "skills",
    "research",
    "references",
    "policy.yaml",
  );
  const policy = safeReadObject(candidate, MAX_POLICY_BYTES);
  if (!policy) return null;
  if (!Array.isArray(policy.stage_order) || !policy.stage_order.length) return null;
  if (!Array.isArray(policy.gate_order) || !policy.gate_order.length) return null;
  if (!policy.stages || typeof policy.stages !== "object" || Array.isArray(policy.stages)) {
    return null;
  }
  if (!policy.gates || typeof policy.gates !== "object" || Array.isArray(policy.gates)) {
    return null;
  }
  const artifactLayout = policy.artifact_layout;
  if (!artifactLayout || typeof artifactLayout !== "object" || Array.isArray(artifactLayout)) {
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
  const stageIds = policy.stage_order.filter((item) => typeof item === "string" && item);
  const gateIds = policy.gate_order.filter((item) => typeof item === "string" && item);
  if (stageIds.length !== policy.stage_order.length || new Set(stageIds).size !== stageIds.length) {
    return null;
  }
  if (gateIds.length !== policy.gate_order.length || new Set(gateIds).size !== gateIds.length) {
    return null;
  }
  if (stageIds.some((stage) => !policy.stages[stage])) return null;
  if (gateIds.some((gate) => !policy.gates[gate])) return null;
  const contract = policy.state_contract;
  if (!contract || typeof contract !== "object" || Array.isArray(contract)) return null;
  const exactArray = (value, expected) => Array.isArray(value)
    && value.length === expected.length
    && value.every((item, index) => item === expected[index]);
  if (!exactArray(contract.required_fields, REQUIRED_STATE_FIELDS)) return null;
  if (!exactArray(contract.stage_ids, stageIds)) return null;
  if (!exactArray(contract.gate_ids, gateIds)) return null;
  if (!exactArray(contract.gate_statuses, ["pending", "approved", "reopened"])) return null;
  if (!exactArray(contract.gate_actions, ["approve", "reopen"])) return null;
  if (!exactArray(
    contract.artifact_pointer_fields,
    ["path", "artifact_id", "version", "content_hash", "status"],
  )) return null;
  return policy;
}

function activeProject(input) {
  const cwd = inputCwd(input);
  if (!cwd) return null;
  const root = findResearchRoot(cwd);
  if (!root) return null;
  const statePath = path.join(root, ".research", "state.json");
  const state = safeReadObject(statePath, MAX_STATE_BYTES);
  if (!state || state.enabled !== true) return null;
  const policy = loadPolicy();
  if (!policy) return null;
  return {
    root,
    statePath,
    memoryPath: path.join(root, ".research", "memory.md"),
    state,
    policy,
  };
}

function stageSpec(context) {
  const stage = context.state.current_stage;
  if (typeof stage !== "string") return null;
  const spec = context.policy.stages[stage];
  return spec && typeof spec === "object" && !Array.isArray(spec) ? spec : null;
}

function gateStatus(context, gateId) {
  const gates = context.state.gates;
  const record = gates && typeof gates === "object" ? gates[gateId] : null;
  return record && typeof record === "object" ? record.status : null;
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
  const spec = stageSpec(context);
  const stage = scalar(context.state.current_stage, "invalid");
  const label = spec ? scalar(spec.label, "unlabeled") : "unknown stage";
  const gate = spec && typeof spec.gate_to_exit === "string" ? spec.gate_to_exit : null;
  return bounded([
    "[SCIENTIFIC RESEARCH WORKFLOW — ACTIVE PROJECT]",
    `Project: ${scalar(context.state.project_name, path.basename(context.root))}`,
    `Project ID: ${scalar(context.state.project_id)}`,
    `Current stage: ${stage} — ${label}`,
    `Gate to exit: ${gate ? `${gate} (${scalar(gateStatus(context, gate), "missing")})` : "none"}`,
    ".research/state.json is the project state authority. Never infer Gate approval or edit Gate state directly.",
    "Research-relevant prompts receive the current-stage boundary separately. Mechanical Hook checks remain active for supported tool calls, but cannot guarantee scientific correctness or universal interception.",
  ].join("\n"), MAX_SESSION_CONTEXT_CHARS);
}

const CODE_PROMPT_PATTERNS = [
  /\b(?:refactor|debug|troubleshoot|fix|simplify|clean\s+up|format|rename|optimi[sz]e|improve|explain|inspect|review|understand|walk\s+through|analy[sz]e)\b[\s\S]{0,100}\b(?:bug|error|code|function|class|method|module|api|loop|variable|script|parser|loader|reader|processor|calculation|implementation|runtime|control\s+flow|stack\s+trace|[\w.-]+\.(?:py|js|jsx|ts|tsx|java|c|cc|cpp|h|hpp|rs|go|rb|php|swift|kt|sh))\b/i,
  /\b(?:bug|error|code|function|class|method|module|api|loop|variable|script|parser|loader|reader|processor|calculation|implementation|runtime|control\s+flow|stack\s+trace|[\w.-]+\.(?:py|js|jsx|ts|tsx|java|c|cc|cpp|h|hpp|rs|go|rb|php|swift|kt|sh))\b[\s\S]{0,100}\b(?:refactor|debug|troubleshoot|fix|simplify|clean\s+up|format|rename|optimi[sz]e|improve|explain|inspect|review|understand|walk\s+through|analy[sz]e|work|mean|detail|why|how|problem)\b/i,
  /\b(?:run|add|write|fix|update)\b[\s\S]{0,40}\b(?:unit|integration)?\s*tests?\b/i,
  /\b(?:why\s+does|investigate|debug|fix|explain)\b[\s\S]{0,60}\b(?:test|ci|build)\b[\s\S]{0,30}\b(?:fail|failing|failure|break)/i,
  /\b(?:investigate|debug|fix|explain)?[\s\S]{0,20}\b(?:failing|failed|broken)\b[\s\S]{0,30}\b(?:test|ci|build)\b/i,
  /\b(?:run|fix|check)\b[\s\S]{0,40}\b(?:lint|typecheck|type-check|formatter|static\s+analysis)\b/i,
  /\bwhat\s+(?:does|is)\b[\s\S]{0,30}\b(?:function|class|method|module)\b[\s\S]{0,20}\b(?:do|for)\b/i,
  /\b(?:review|explain|inspect)\b[\s\S]{0,30}\b(?:git\s+diff|patch|regex|regular\s+expression)\b/i,
  /\b(?:simplify|optimi[sz]e|explain|review)\b[\s\S]{0,40}\b(?:sql\s+query|query)\b/i,
  /\b(?:why\s+(?:does|did)|fix|debug|explain)\b[\s\S]{0,30}\b(?:pytest|unit\s*tests?|ci)\b[\s\S]{0,20}\b(?:fail|failing|failed|error)?/i,
  /(?:重构|调试|排查|修复|简化|整理|格式化|重命名|优化|解释|分析|讲解|检查|理解|看看)[^。！？\n]{0,60}(?:代码|函数|类|接口|模块|循环|变量|脚本|算法实现|代码实现|代码逻辑|控制流|报错|错误|单元测试|[\w.-]+\.(?:py|js|jsx|ts|tsx|java|c|cc|cpp|h|hpp|rs|go|rb|php|swift|kt|sh))/,
  /(?:代码|函数|类|接口|模块|循环|变量|脚本|算法实现|代码实现|代码逻辑|控制流|报错|错误|单元测试|[\w.-]+\.(?:py|js|jsx|ts|tsx|java|c|cc|cpp|h|hpp|rs|go|rb|php|swift|kt|sh))[^。！？\n]{0,60}(?:重构|调试|排查|修复|简化|整理|格式化|重命名|优化|解释|分析|讲解|检查|理解|为什么|怎么|如何|细节|问题|作用)/,
  /(?:代码优化|优化代码|代码细节|代码逻辑|单元测试|测试代码|运行测试|补充测试)/,
  /(?:为什么|为何|怎么|如何)[^。！？\n]{0,30}(?:测试|CI|构建)[^。！？\n]{0,20}(?:失败|报错|不通过)/,
];

const WORKFLOW_PROMPT_PATTERNS = [
  /\b(?:release\s+gate|researchctl|artifact\s+(?:register|registry)|research\s+(?:stage|gate)|current\s+stage)\b|\.research[\\/](?:state\.json|memory\.md)/i,
  /\b(?:idea[_ -]?freeze|method[_ -]?experiment[_ -]?approval|claim[_ -]?freeze)\b/i,
  /(?:科研|研究)(?:阶段|门禁)|(?:想法|方法|主张|声明)(?:冻结|批准)|(?:门禁|闸门)(?:状态|批准|重开)|(?:登记|注册)科研产物/,
];

const RESEARCH_DELIVERABLE_PATTERN = /\b(?:write|draft|revise|edit|verify|check|audit|prepare|submit|publish|send|respond|address|search|cite|compare|summari[sz]e|synthesi[sz]e|judge|evaluate)\b[\s\S]{0,100}\b(?:literature|prior\s+work|citation|manuscript|paper|rebuttal|reviewer|submission|research\s+claim|scientific\s+evidence|finding|conclusion)\b/i;
const CHINESE_RESEARCH_DELIVERABLE_PATTERN = /(?:撰写|起草|修改|润色|核验|检查|审计|准备|投稿|发布|发送|回复|回应|检索|引用|比较|总结|梳理|判断|评价)[^。！？\n]{0,60}(?:文献|相关工作|引用|论文|稿件|返修|审稿|回复信|投稿|科研主张|科研证据|研究发现|研究结论)/;
const RESEARCH_ACTION_PATTERN = /\b(?:verify|validate|analy[sz]e|interpret|compare|report|assess|prove|support|conclude|audit|freeze|summari[sz]e|synthesi[sz]e|judge|evaluate|improve|increase|reduce|maximi[sz]e)\b/i;
const RESEARCH_OBJECT_PATTERN = /\b(?:experiment|evaluation|training)\s+(?:result|output|finding)s?\b|\b(?:metric|accuracy|precision|recall|baseline|ablation|claim|novelty|scientific\s+evidence|hypoth(?:esis|eses))\b/i;
const PERFORMANCE_ACTION_PATTERN = /\b(?:verify|validate|compare|report|assess|improve|increase|reduce|maximi[sz]e)\b/i;
const PERFORMANCE_METRIC_PATTERN = /\b(?:F1|f1|mAP|AUC|IoU|Dice)\b/;
const STATISTICAL_RESEARCH_PATTERN = /\b(?:hypothesis|statistical|significance)\s+tests?\b|\bp[- ]?values?\b|\bconfidence\s+intervals?\b/i;
const RESEARCH_EXECUTION_PATTERN = /\b(?:run|launch|execute|start)\s+(?:the\s+)?(?:(?:approved|registered|full|new|next)\s+)?(?:experiment|training|benchmark|evaluation)s?\b(?!\s+(?:script|code|runner|pipeline|test))/i;
const CHINESE_RESEARCH_ACTION_PATTERN = /(?:验证|核验|分析|解释结果|解读|比较|汇报|评估|论证|证明|支持|下结论|审计|冻结|总结|梳理|判断|评价|提升|提高|降低|减少)/;
const CHINESE_RESEARCH_OBJECT_PATTERN = /(?:实验结果|实验输出|训练结果|评估结果|结果指标|准确率|精确率|召回率|基线|消融|科研主张|创新性|新颖性|科研证据|研究假设)/;
const CHINESE_RESEARCH_EXECUTION_PATTERN = /(?:运行|启动|执行|开始)(?:已批准的|已登记的|完整的|新的|下一轮)?(?:实验|训练|基准测试|评估)(?!脚本|代码|程序|测试)/;

const RESEARCH_OBJECT_CODE_PATTERN = /\b(?:experiment|evaluation|training)\s+(?:result|output|finding)s?\s+(?:parser|loader|reader|processor|function|class|module|script|code|implementation)\b/i;
const CHINESE_RESEARCH_OBJECT_CODE_PATTERN = /(?:实验结果|实验输出|训练结果|评估结果)(?:解析|处理|读取|加载)(?:器|函数|类|模块|脚本|代码|实现)?/;
const METRIC_CODE_PATTERN = /\b(?:accuracy|precision|recall|F1|f1|mAP|AUC|IoU|Dice|loss|metric)\s+(?:calculation|calculator|function|parser|code|implementation)\b/i;

function hasStrongResearchIntent(prompt, codeIntent) {
  if (WORKFLOW_PROMPT_PATTERNS.some((pattern) => pattern.test(prompt))) return true;
  if (STATISTICAL_RESEARCH_PATTERN.test(prompt)) return true;
  if (RESEARCH_DELIVERABLE_PATTERN.test(prompt) || CHINESE_RESEARCH_DELIVERABLE_PATTERN.test(prompt)) {
    return true;
  }
  if (RESEARCH_EXECUTION_PATTERN.test(prompt) || CHINESE_RESEARCH_EXECUTION_PATTERN.test(prompt)) {
    return true;
  }
  if (codeIntent && (
    RESEARCH_OBJECT_CODE_PATTERN.test(prompt)
    || CHINESE_RESEARCH_OBJECT_CODE_PATTERN.test(prompt)
    || METRIC_CODE_PATTERN.test(prompt)
  )) {
    const remainder = prompt
      .replace(RESEARCH_OBJECT_CODE_PATTERN, " ")
      .replace(CHINESE_RESEARCH_OBJECT_CODE_PATTERN, " ")
      .replace(METRIC_CODE_PATTERN, " ");
    return (RESEARCH_ACTION_PATTERN.test(remainder) && RESEARCH_OBJECT_PATTERN.test(remainder))
      || (PERFORMANCE_ACTION_PATTERN.test(remainder) && PERFORMANCE_METRIC_PATTERN.test(remainder))
      || (CHINESE_RESEARCH_ACTION_PATTERN.test(remainder) && CHINESE_RESEARCH_OBJECT_PATTERN.test(remainder));
  }
  return (RESEARCH_ACTION_PATTERN.test(prompt) && RESEARCH_OBJECT_PATTERN.test(prompt))
    || (PERFORMANCE_ACTION_PATTERN.test(prompt) && PERFORMANCE_METRIC_PATTERN.test(prompt))
    || (CHINESE_RESEARCH_ACTION_PATTERN.test(prompt) && CHINESE_RESEARCH_OBJECT_PATTERN.test(prompt));
}

function clearlyCodeOnlyPrompt(input) {
  const prompt = userPrompt(input).trim();
  if (!prompt) return false;
  const codeIntent = CODE_PROMPT_PATTERNS.some((pattern) => pattern.test(prompt));
  if (!codeIntent) return false;
  return !hasStrongResearchIntent(prompt, codeIntent);
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
  const gate = typeof spec.gate_to_exit === "string" ? spec.gate_to_exit : null;
  const auditItems = Array.isArray(context.policy.semantic_audit)
    ? context.policy.semantic_audit
    : [];
  return bounded([
    "[RESEARCH WORKFLOW — PROMPT RELEVANT]",
    `Current stage: ${stage} — ${scalar(spec.label, "unlabeled")}`,
    `Gate to exit: ${gate ? `${gate} (${scalar(gateStatus(context, gate), "missing")})` : "none"}`,
    "Current-stage prohibited actions:",
    listLines(spec.prohibited_actions),
    "",
    `Artifact layout: ${scalar(context.policy.artifact_layout.instruction)}`,
    "Use the $research Skill, follow policy.yaml review_language, and load only the current-stage reference. policy.yaml is authoritative for evidence, Gate, and exit criteria.",
    "",
    "Before the first user-facing final answer, silently apply the canonical semantic audit below. Integrate any necessary correction into one complete, self-contained answer. Do not emit a standalone audit addendum, `[Stop Hook Review]`, or private chain-of-thought.",
    "Canonical semantic audit:",
    listLines(auditItems),
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

function destructiveGitCommand(command) {
  for (const segment of expandedShellSegments(command)) {
    const tokens = commandTokens(segment);
    if (path.basename(tokens[0] || "").toLowerCase() !== "git") continue;
    let index = 1;
    while (index < tokens.length) {
      const token = tokens[index];
      if (["-C", "-c", "--git-dir", "--work-tree", "--config-env"].includes(token)) index += 2;
      else if (/^--(?:git-dir|work-tree)=/.test(token)) index += 1;
      else if (/^--(?:no-pager|paginate|bare|literal-pathspecs|glob-pathspecs|noglob-pathspecs|icase-pathspecs)$/.test(token)) index += 1;
      else break;
    }
    const subcommand = (tokens[index] || "").toLowerCase();
    const args = tokens.slice(index + 1);
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
  return /\b(?:init|status|enable|disable|artifact|gate|checkpoint|doctor)\b/i.test(command);
}

function shellStateMutation(command) {
  let effective = command;
  const leadingDirectoryChange = /^\s*(?:cd|pushd)\s+(?:--\s+)?(?:"[^"]+"|'[^']+'|[^\s;&|]+)\s*(?:&&|;)\s*/i;
  while (leadingDirectoryChange.test(effective)) {
    effective = effective.replace(leadingDirectoryChange, "");
  }
  const readOnly = /^\s*(?:cat|head|tail|less|more|stat|ls|rg|grep|jq\b(?![\s\S]*(?:>|--in-place))|sed\s+-n\b|test\b)/i;
  return !readOnly.test(effective)
    || /(?:>>?|\btee\b|\bsponge\b|\btruncate\b|\brm\b|\bmv\b|\bcp\b|\btouch\b|\bsed\b[\s\S]*\s-i\b|\bwriteFile|\bwrite_text|\bjson\.dump\b)/i.test(effective);
}

function gateBlocked(context, gate) {
  return context.policy.gate_order.includes(gate) && gateStatus(context, gate) !== "approved";
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
  const roles = [
    ["paper", "manuscript"],
    ["revision", "revised_manuscript"],
    ["revision", "response_document"],
  ];
  for (const [stage, role] of roles) {
    const stageBucket = artifacts && typeof artifacts === "object" ? artifacts[stage] : null;
    const roleBucket = stageBucket && typeof stageBucket === "object" ? stageBucket[role] : null;
    if (!roleBucket || typeof roleBucket !== "object") continue;
    for (const pointer of Object.values(roleBucket)) {
      if (pointer && typeof pointer === "object" && typeof pointer.path === "string") {
        results.push(pointer.path.replace(/\\/g, "/"));
      }
    }
  }
  return results;
}

function manuscriptMutation(context, toolName, toolInput, text) {
  if (!(isMutatingTool(toolName) || isShellTool(toolName))) return false;
  const knownTarget = /(?:^|[\/])(?:main|paper|manuscript|appendix|supplement|respond|response|rebuttal|cover[_-]?letter)\.(?:tex|md|docx)$/i;
  const registered = registeredManuscriptPaths(context);
  const registeredAbsolute = registered.map((stored) => path.resolve(context.root, stored));
  const isTarget = (candidate) => {
    if (typeof candidate !== "string" || !candidate.trim()) return false;
    const normalized = candidate.replace(/^['"]|['"]$/g, "").replace(/\\/g, "/");
    if (knownTarget.test(normalized)) return true;
    return registered.some((stored) => (
      normalized === stored
      || path.resolve(context.root, normalized) === path.resolve(context.root, stored)
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
    return paths.length ? paths.some(isTarget) : knownTarget.test(text);
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
      return deny(`Blocked mechanically detectable dangerous operation (${dangerous}). Use a narrower reversible command or obtain explicit authority. Hook coverage is limited to this intercepted tool call.`);
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

  if (
    gateBlocked(context, "method_experiment_approval")
    && explicitExperimentLaunch(toolName, command, text)
  ) {
    return deny("This is an explicit experiment, training, cluster, or hardware launch, but method_experiment_approval is not approved. Prepare the method and experiment contract, then record human approval through researchctl.");
  }

  if (gateBlocked(context, "claim_freeze") && manuscriptMutation(context, toolName, toolInput, text)) {
    return deny("This tool call mechanically targets a manuscript or rebuttal artifact before claim_freeze is approved. Freeze evidence-bounded claims through researchctl before entering paper production.");
  }

  if (gateStatus(context, "release") === "approved" && manuscriptMutation(context, toolName, toolInput, text)) {
    return deny("The release Gate is still approved, so changing a manuscript or rebuttal would make that approval stale. Reopen release through researchctl, make and verify the revision, then request approval for the new release target.");
  }

  if (gateBlocked(context, "release") && externalReleaseAction(toolName, command, text)) {
    return deny("This appears to send, submit, publish, or upload a manuscript/reviewer response while the release Gate is not approved. Record explicit human release approval through researchctl first.");
  }

  return {};
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

function validateArtifactPointers(
  root,
  value,
  label,
  errors,
  warnings,
  pointerFields,
  stageIds,
) {
  const pointerMetadata = pointerFields.filter((field) => field !== "path");
  const reservedIds = new Set(pointerFields);
  const checkPointer = (pointer, pointerLabel, canonical, mappingKey = null) => {
    if (!pointer || typeof pointer !== "object" || Array.isArray(pointer)) {
      const message = `${pointerLabel} must be a structured artifact pointer`;
      (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
      return;
    }
    const missing = pointerFields.filter((field) => !Object.prototype.hasOwnProperty.call(pointer, field));
    if (missing.length) {
      const message = `${pointerLabel} missing fields: ${missing.join(", ")}`;
      (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
    }
    const pathValue = pointer.path;
    if (typeof pathValue !== "string" || !pathValue.trim()) {
      const message = `${pointerLabel}.path must be a non-empty string`;
      (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
      return;
    }
    let candidate;
    try {
      candidate = path.isAbsolute(pathValue) ? pathValue : path.resolve(root, pathValue);
    } catch (_error) {
      const message = `${pointerLabel}.path cannot be resolved`;
      (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
      return;
    }
    const controlFiles = new Set([
      path.resolve(root, ".research", "state.json"),
      path.resolve(root, ".research", "state.lock"),
      path.resolve(root, ".research", "memory.md"),
      path.resolve(root, ".research", "project-state.yaml"),
    ]);
    let realCandidate = path.resolve(candidate);
    try {
      realCandidate = fs.realpathSync.native(candidate);
    } catch (_error) {
      // Missing paths are reported below.
    }
    const realControlFiles = new Set([...controlFiles].map((control) => {
      try {
        return fs.realpathSync.native(control);
      } catch (_error) {
        return control;
      }
    }));
    const aliasesControlFile = [...controlFiles].some((control) => samePhysicalFile(candidate, control));
    if (controlFiles.has(path.resolve(candidate))
      || realControlFiles.has(realCandidate)
      || aliasesControlFile) {
      const message = `${pointerLabel} points to research control metadata, which cannot be evidence: ${pathValue}`;
      (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
      return;
    }
    if (!fs.existsSync(candidate)) {
      const message = `${pointerLabel} points to a missing artifact: ${pathValue}`;
      (canonical ? errors : warnings).push(message);
      return;
    }
    if (canonical && !isFile(candidate)) {
      errors.push(`${pointerLabel} must point to a regular file: ${pathValue}`);
    }
    if (canonical) {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(mappingKey) || reservedIds.has(mappingKey)) {
        errors.push(`${pointerLabel} has an invalid or reserved artifact-ID mapping key`);
      } else if (pointer.artifact_id !== mappingKey) {
        errors.push(`${pointerLabel}.artifact_id must match its artifact-ID mapping key`);
      }
      if (typeof pointer.artifact_id !== "string"
        || !/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(pointer.artifact_id)) {
        errors.push(`${pointerLabel}.artifact_id has an invalid format`);
      }
      if ((typeof pointer.version !== "string" && typeof pointer.version !== "number")
        || typeof pointer.version === "boolean" || !String(pointer.version).trim()) {
        errors.push(`${pointerLabel}.version must be a non-empty string or integer`);
      }
      if (typeof pointer.content_hash !== "string"
        || !/^sha256:[0-9a-f]{64}$/.test(pointer.content_hash)) {
        errors.push(`${pointerLabel}.content_hash must be sha256:<64 lowercase hex>`);
      }
      if (typeof pointer.status !== "string" || !pointer.status.trim()) {
        errors.push(`${pointerLabel}.status must be a non-empty string`);
      }
      const extra = Object.keys(pointer).filter((field) => !pointerFields.includes(field));
      if (extra.length) {
        errors.push(`${pointerLabel} has unknown fields: ${extra.sort().join(", ")}`);
      }
    }
  };

  const validateLegacy = (legacyValue, legacyLabel) => {
    const stack = [[legacyValue, legacyLabel]];
    while (stack.length) {
      const [current, currentLabel] = stack.pop();
      if (current === null || current === undefined) continue;
      if (typeof current === "string") {
        if (!current.trim()) warnings.push(`legacy artifact pointer: ${currentLabel} is an empty artifact path`);
        else {
          let candidate;
          try {
            candidate = path.isAbsolute(current) ? current : path.resolve(root, current);
          } catch (_error) {
            warnings.push(`artifact pointer cannot be resolved: ${currentLabel}`);
            continue;
          }
          if (!fs.existsSync(candidate)) warnings.push(`${currentLabel} points to a missing artifact: ${current}`);
        }
        continue;
      }
      if (Array.isArray(current)) {
        current.forEach((child, index) => stack.push([child, `${currentLabel}[${index}]`]));
        continue;
      }
      if (!current || typeof current !== "object") {
        warnings.push(`legacy artifact pointer: ${currentLabel} is not a valid artifact path`);
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(current, "path")) {
        checkPointer(current, currentLabel, false);
        continue;
      }
      if (pointerMetadata.some((key) => Object.prototype.hasOwnProperty.call(current, key))) {
        warnings.push(`legacy artifact pointer: ${currentLabel} is an artifact pointer but has no path`);
        continue;
      }
      for (const [key, child] of Object.entries(current)) stack.push([child, `${currentLabel}.${key}`]);
    }
  };

  if (!value || typeof value !== "object" || Array.isArray(value)) {
    validateLegacy(value, label);
    return;
  }
  for (const [stage, stageBucket] of Object.entries(value)) {
    const stageLabel = `${label}.${stage}`;
    if (!stageIds.has(stage)) {
      validateLegacy(stageBucket, stageLabel);
      continue;
    }
    if (!stageBucket || typeof stageBucket !== "object" || Array.isArray(stageBucket)
      || Object.prototype.hasOwnProperty.call(stageBucket, "path")) {
      errors.push(`${stageLabel} must be a role mapping`);
      continue;
    }
    for (const [role, roleBucket] of Object.entries(stageBucket)) {
      const roleLabel = `${stageLabel}.${role}`;
      if (!/^[a-z][a-z0-9_]*$/.test(role)) {
        errors.push(`${roleLabel} role must use lower_snake_case`);
        continue;
      }
      if (!roleBucket || typeof roleBucket !== "object" || Array.isArray(roleBucket)) {
        errors.push(`${roleLabel} must be an artifact-ID mapping`);
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(roleBucket, "path")) {
        checkPointer(roleBucket, roleLabel, false);
        warnings.push(`legacy artifact pointer: ${roleLabel} should be re-registered under an artifact-ID mapping`);
        continue;
      }
      for (const [artifactId, pointer] of Object.entries(roleBucket)) {
        checkPointer(pointer, `${roleLabel}.${artifactId}`, true, artifactId);
      }
    }
  }
}

function utcTimestamp(value) {
  if (typeof value !== "string"
    || !/(?:Z|\+00:00)$/.test(value)
    || Number.isNaN(Date.parse(value))) return null;
  return Date.parse(value);
}

function validateState(context) {
  const { state, policy, root } = context;
  const errors = [];
  const warnings = [];
  const contract = policy.state_contract && typeof policy.state_contract === "object"
    ? policy.state_contract
    : {};
  const pointerFields = Array.isArray(contract.artifact_pointer_fields)
    ? contract.artifact_pointer_fields
    : ["path", "artifact_id", "version", "content_hash", "status"];
  const required = Array.isArray(contract.required_fields) ? contract.required_fields : [];
  for (const field of required) {
    if (!Object.prototype.hasOwnProperty.call(state, field)) errors.push(`missing state field: ${field}`);
  }
  if (state.schema_version !== policy.schema_version) {
    errors.push(`schema_version ${JSON.stringify(state.schema_version)} does not match policy ${JSON.stringify(policy.schema_version)}`);
  }
  if (state.workflow_version !== policy.workflow_version) {
    errors.push(`workflow_version ${JSON.stringify(state.workflow_version)} does not match policy ${JSON.stringify(policy.workflow_version)}`);
  }
  if (state.enabled !== true) errors.push("enabled must be true for an active research project");
  if (typeof state.project_id !== "string" || !state.project_id.trim()) errors.push("project_id must be a non-empty string");
  if (Object.prototype.hasOwnProperty.call(state, "project_name")
    && (typeof state.project_name !== "string" || !state.project_name.trim())) {
    errors.push("project_name must be a non-empty string when present");
  }
  if (!policy.stage_order.includes(state.current_stage)) {
    errors.push(`unknown current_stage: ${JSON.stringify(state.current_stage)}`);
  }
  const createdAt = utcTimestamp(state.created_at);
  const updatedAt = utcTimestamp(state.updated_at);
  if (createdAt === null) errors.push("created_at must be a timezone-explicit UTC timestamp");
  if (updatedAt === null) errors.push("updated_at must be a timezone-explicit UTC timestamp");
  if (createdAt !== null && updatedAt !== null && updatedAt < createdAt) {
    errors.push("updated_at must not be earlier than created_at");
  }

  const statuses = Array.isArray(contract.gate_statuses)
    ? new Set(contract.gate_statuses)
    : new Set(["pending", "approved", "reopened"]);
  if (!state.gates || typeof state.gates !== "object" || Array.isArray(state.gates)) {
    errors.push("gates must be an object");
  } else {
    const actualGates = Object.keys(state.gates);
    for (const gate of policy.gate_order) {
      const record = state.gates[gate];
      if (!record || typeof record !== "object" || Array.isArray(record)) {
        errors.push(`Gate ${gate} must be an object`);
        continue;
      }
      if (!statuses.has(record.status)) errors.push(`Gate ${gate} has invalid status ${JSON.stringify(record.status)}`);
      if (!Array.isArray(record.history)) errors.push(`Gate ${gate} history must be an array`);
      if (record.latest_decision_id !== null && typeof record.latest_decision_id !== "string") {
        errors.push(`Gate ${gate} latest_decision_id must be null or a string`);
      }
      if (Array.isArray(record.history) && record.history.length === 0) {
        if (record.status !== "pending") errors.push(`Gate ${gate} without history must be pending`);
        if (record.latest_decision_id !== null) errors.push(`Gate ${gate} without history must not have a decision ID`);
      }
      if (Array.isArray(record.history) && record.history.length > 0) {
        let expectedStatus = "pending";
        let priorDecisionAt = null;
        for (const [index, decision] of record.history.entries()) {
          const prefix = `Gate ${gate} history[${index}]`;
          if (!decision || typeof decision !== "object" || Array.isArray(decision)) {
            errors.push(`${prefix} must be an object`);
            continue;
          }
          if (decision.previous_status !== expectedStatus) {
            errors.push(`${prefix} does not continue the Gate status chain`);
          }
          if (decision.action === "approve") {
            if (decision.previous_status === "approved" || decision.new_status !== "approved") {
              errors.push(`${prefix} has an invalid approve transition`);
            }
          } else if (decision.action === "reopen") {
            if (decision.previous_status !== "approved" || decision.new_status !== "reopened") {
              errors.push(`${prefix} has an invalid reopen transition`);
            }
          } else {
            errors.push(`${prefix} has an invalid action`);
          }
          if (statuses.has(decision.new_status)) expectedStatus = decision.new_status;
          if (typeof decision.decision_id !== "string" || !decision.decision_id.trim()) {
            errors.push(`${prefix} needs a decision_id`);
          }
          if (typeof decision.reason !== "string" || !decision.reason.trim()) {
            errors.push(`${prefix} needs a reason`);
          }
          if (typeof decision.actor !== "string" || !decision.actor.trim()) {
            errors.push(`${prefix} needs an actor`);
          }
          if (!Array.isArray(decision.artifact_refs)) {
            errors.push(`${prefix}.artifact_refs must be an array`);
          }
          const decisionAt = utcTimestamp(decision.decided_at);
          if (decisionAt === null) errors.push(`${prefix} needs a UTC decided_at`);
          else if (priorDecisionAt !== null && decisionAt < priorDecisionAt) {
            errors.push(`${prefix} is earlier than the prior decision`);
          } else priorDecisionAt = decisionAt;
          if (gate === "release") {
            const targets = context.policy.gates.release.release_targets;
            if (!Array.isArray(targets) || !targets.includes(decision.release_target)) {
              errors.push(`${prefix} has an invalid release_target`);
            }
          } else if (Object.prototype.hasOwnProperty.call(decision, "release_target")) {
            errors.push(`${prefix} must not define release_target`);
          }
        }
        const last = record.history[record.history.length - 1];
        if (!last || typeof last !== "object") {
          errors.push(`Gate ${gate} last history entry must be an object`);
        } else {
          if (last.decision_id !== record.latest_decision_id) errors.push(`Gate ${gate} latest_decision_id does not match history`);
          if (last.new_status !== record.status) errors.push(`Gate ${gate} status does not match history`);
        }
      }
    }
    for (const gate of actualGates) {
      if (!policy.gate_order.includes(gate)) errors.push(`unknown Gate: ${gate}`);
    }

    if (policy.stage_order.includes(state.current_stage)) {
      const currentIndex = policy.stage_order.indexOf(state.current_stage);
      for (const gate of policy.gate_order) {
        const spec = policy.gates[gate];
        const target = spec && spec.advance_to;
        const record = state.gates[gate];
        let gateSatisfied = Boolean(record && record.status === "approved");
        if (gate === "release" && state.current_stage === "revision" && record && Array.isArray(record.history)) {
          gateSatisfied = record.history.some((decision) => (
            decision
            && typeof decision === "object"
            && decision.action === "approve"
            && decision.release_target === "initial_submission"
          ));
        }
        if (
          policy.stage_order.includes(target)
          && currentIndex >= policy.stage_order.indexOf(target)
          && !gateSatisfied
        ) {
          errors.push(`current_stage ${state.current_stage} requires approved Gate ${gate}`);
        }
      }
    }
  }

  if (!state.artifacts || (typeof state.artifacts !== "object")) {
    errors.push("artifacts must be an object or array");
  } else {
    validateArtifactPointers(
      root,
      state.artifacts,
      "artifacts",
      errors,
      warnings,
      pointerFields,
      new Set(policy.stage_order),
    );
  }
  if (state.last_checkpoint !== null && state.last_checkpoint !== undefined) {
    const checkpoint = state.last_checkpoint;
    if (!checkpoint || typeof checkpoint !== "object" || Array.isArray(checkpoint)) {
      errors.push("last_checkpoint must be null or an object");
    } else {
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
    let expectedStage = policy.stage_order[0];
    let priorTransitionAt = null;
    for (const [index, transition] of state.stage_history.entries()) {
      const prefix = `stage_history[${index}]`;
      if (!transition || typeof transition !== "object" || Array.isArray(transition)) {
        errors.push(`${prefix} must be an object`);
        continue;
      }
      if (transition.from_stage !== expectedStage) errors.push(`${prefix} breaks stage continuity`);
      if (!policy.stage_order.includes(transition.from_stage)
        || !policy.stage_order.includes(transition.to_stage)) {
        errors.push(`${prefix} contains an unknown stage`);
      } else {
        expectedStage = transition.to_stage;
      }
      if (typeof transition.trigger !== "string" || !transition.trigger.trim()) {
        errors.push(`${prefix} needs a trigger`);
      }
      const transitionAt = utcTimestamp(transition.timestamp);
      if (transitionAt === null) errors.push(`${prefix} needs a UTC timestamp`);
      else if (priorTransitionAt !== null && transitionAt < priorTransitionAt) {
        errors.push(`${prefix} is earlier than the prior transition`);
      } else priorTransitionAt = transitionAt;
    }
    if (expectedStage !== state.current_stage) {
      errors.push("current_stage does not match stage_history");
    }
  }
  if (!isFile(context.memoryPath)) errors.push("missing .research/memory.md");
  return { errors, warnings };
}

function postToolUse(context, input) {
  if (!stateWasTouched(context, input)) return {};
  const result = validateState(context);
  const lines = [
    "[POST-TOOL RESEARCH STATE QUICK CHECK]",
    result.errors.length
      ? `Detected structural state/Gate errors (${result.errors.length}):\n${listLines(result.errors)}`
      : "Quick structural state/Gate checks found no issue.",
    result.warnings.length
      ? `Artifact warnings (${result.warnings.length}):\n${listLines(result.warnings)}`
      : "Registered artifact pointers are structurally valid and currently resolvable; use researchctl doctor for authoritative hash verification.",
  ];
  if (result.errors.length) {
    lines.push("Do not treat the state as authoritative until researchctl doctor passes. Repair it through researchctl; never hand-edit Gate fields.");
  }
  return hookContextOutput("PostToolUse", bounded(lines.join("\n"), MAX_POST_CONTEXT_CHARS));
}

function getStopHookActive(input) {
  return firstDefined(input, ["stop_hook_active", "stopHookActive"]) === true;
}

function regexAlternation(values) {
  return values
    .filter((value) => typeof value === "string" && value)
    .map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
}

function assertionLine(rawLine) {
  const normalized = cleanText(rawLine);
  if (/^(?: {4,}|\t)/.test(normalized)) return "";
  const line = normalized.trim();
  if (!line || /^>/.test(line)) return "";
  const unwrapped = line
    .replace(/^#{1,6}\s+/, "")
    .replace(/^(?:[-*+]\s+|\d+[.)]\s+)/, "")
    .trim();
  if (/^(`+)[\s\S]*\1$/.test(unwrapped)) return "";
  const deEmphasized = unwrapped
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|[^\w])__([^\n]+?)__(?=$|[^\w])/g, "$1$2")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .replace(/(^|[^\w])_([^\n]+?)_(?=$|[^\w])/g, "$1$2")
    .trim();
  if (/^(`+)[\s\S]*\1$/.test(deEmphasized)) return "";
  return deEmphasized.replace(/`+/g, "").trim();
}

function assertionIsQualified(line, match) {
  if (!match) return true;
  const rawSuffix = line.slice(match.index + match[0].length).trim();
  if (/^[?？]/.test(rawSuffix)) {
    const answer = rawSuffix.replace(/^[?？]\s*/, "");
    return !/^(?:yes|yep|correct|true|exactly|indeed|affirmative|是(?:的)?|对(?:的)?|正确|没错|确实)(?:\b|[。.!！]|$)/i.test(answer);
  }
  const suffix = rawSuffix
    .replace(/^[\s`"'()[\]{}（）—–,:;.!。！？，；：-]+/, "")
    .trim();
  if (!suffix) return false;
  return /^(?:if|unless|provided(?:\s+that)?|assuming|subject\s+to|only\s+(?:if|when|whenever|once|after|before|until)|would|could|should|will|may|might)\b/i.test(suffix)
    || /^(?:(?:is|was)\s+)?(?:not\s+(?:(?:the\s+)?current|correct|true)|incorrect|wrong|invalid|false|hypothetical|outdated)\b/i.test(suffix)
    || /^(?:isn['’]t|wasn['’]t)\s+(?:(?:the\s+)?current|correct|true|right)\b/i.test(suffix)
    || /^(?:(?:is|was)\s+)?(?:(?:an?|the)\s+)?(?:(?:incorrect|wrong|invalid|hypothetical)\s+)?(?:example|illustration)\b/i.test(suffix)
    || /^(?:for\s+(?:example|instance)|as\s+an?\s+example)\b/i.test(suffix)
    || /^(?:does|do|did)\s+not\s+(?:mean|indicate|state)\b/i.test(suffix)
    || /^(?:this|that|which)\s+(?:is|was)\s+(?:incorrect|wrong|invalid|false|not\s+(?:correct|true))\b/i.test(suffix)
    || /^not\s+yet\b/i.test(suffix)
    || /^(?:must|should)\s+not\b/i.test(suffix)
    || /^(?:如果|若|假如|一旦|仅在|只有|前提是|才会|将会|可能|并不(?:正确|真实|表示|意味着)|并非(?:正确|真实|当前|如此)|不是(?:正确|真实|当前)|不(?:正确|对|代表|表示|意味着|是真的)|尚未|还未|不应|不能|例如|比如|是(?:一个|该|此)?(?:错|错误|不正确|不对|无效|假设)(?:的)?|是(?:一个|该|此)?(?:错误|不正确|无效|假设)?(?:示例|例子|反例))/.test(suffix);
}

function startsExampleScope(rawLine) {
  const line = cleanText(rawLine)
    .trim()
    .replace(/^#{1,6}\s+/, "")
    .replace(/[*_]/g, "")
    .trim();
  return /^(?:for\s+(?:example|instance)|e\.g\.[,:]?|hypothetical|expected\s+output|(?:(?:incorrect|wrong|invalid|hypothetical)\s+)?examples?(?:\s+(?:output|section|table|below))?(?:\s*\((?:incorrect|wrong|invalid|hypothetical)\))?)\s*[:：]?$/i.test(line)
    || /^(?:例如|比如|举例|譬如|示例输出|错误输出|假设(?:如下)?|以下(?:为|是)?\s*(?:错误|不正确|不对|无效|假设)?\s*(?:示例|例子|反例)|(?:(?:错误|不正确|不对|无效|假设)\s*)?(?:示例|例子|反例))\s*[:：]?$/.test(line);
}

function startsActualScope(rawLine) {
  const line = cleanText(rawLine)
    .trim()
    .replace(/^#{1,6}\s+/, "")
    .replace(/[*_]/g, "")
    .trim();
  return /^(?:(?:actual|current|correct|corrected)\s+(?:status|state|answer|result)|conclusion)\s*[:：]?$/i.test(line)
    || /^(?:(?:实际|当前|正确|更正后)(?:状态|阶段|答案|结果)|结论)\s*[:：]?$/.test(line);
}

function markdownTableCells(rawLine) {
  const line = cleanText(rawLine).trim();
  if (!line.includes("|")) return null;
  const tableLine = line.replace(/^\|/, "").replace(/\|$/, "");
  if (!tableLine.includes("|")) return null;
  return tableLine.split("|").map((cell) => cell
    .trim()
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|[^\w])__([^\n]+?)__(?=$|[^\w])/g, "$1$2")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .replace(/(^|[^\w])_([^\n]+?)_(?=$|[^\w])/g, "$1$2")
    .replace(/`+/g, "")
    .trim());
}

function markdownTableDivider(cells) {
  return Array.isArray(cells)
    && cells.length > 0
    && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function assertionContextKind(line) {
  if (/^(?:(?:incorrect|wrong|invalid|hypothetical)\s+(?:example|literal)|the\s+following\s+is\s+(?:incorrect|wrong|invalid|hypothetical)|examples?|example|for\s+(?:example|instance)|e\.g\.|expected\s+output)\s*[:：;,；，]?/i.test(line)
    || /^(?:以下(?:内容|说法)?(?:是|为)?(?:错误|不正确|无效|假设))/.test(line)) {
    return "example";
  }
  if (/^(?:(?:do\s+not|don't|never)\s+(?:claim|state|write|say|report)|if|unless|provided(?:\s+that)?|assuming|subject\s+to)\b/i.test(line)
    || /^(?:不要|不得|切勿)(?:声称|写成|写为|表述|报告)?|^(?:如果|若|假如|一旦|仅在|只有|前提是)/.test(line)) {
    return "qualified";
  }
  return null;
}

function maskedAssertionText(text) {
  const characters = text.split("");
  const masked = [...characters];
  const bracketStack = [];
  const bracketPairs = new Map([["(", ")"], ["[", "]"], ["{", "}"], ["（", "）"]]);
  const quotePairs = new Map([["\"", "\""], ["“", "”"], ["‘", "’"], ["「", "」"], ["『", "』"]]);
  let quoteEnd = null;
  let backtickLength = 0;
  const escapedAt = (index) => {
    let slashes = 0;
    for (let cursor = index - 1; cursor >= 0 && characters[cursor] === "\\"; cursor -= 1) {
      slashes += 1;
    }
    return slashes % 2 === 1;
  };

  for (let index = 0; index < characters.length; index += 1) {
    const character = characters[index];
    if (backtickLength) {
      masked[index] = " ";
      if (character === "`") {
        let run = 1;
        while (characters[index + run] === "`") run += 1;
        for (let offset = 1; offset < run; offset += 1) masked[index + offset] = " ";
        if (run >= backtickLength) backtickLength = 0;
        index += run - 1;
      }
      continue;
    }
    if (quoteEnd) {
      masked[index] = " ";
      const wordApostrophe = quoteEnd === "'"
        && /[\p{L}\p{N}]/u.test(characters[index - 1] || "")
        && /[\p{L}\p{N}]/u.test(characters[index + 1] || "");
      if (character === quoteEnd && !escapedAt(index) && !wordApostrophe) quoteEnd = null;
      continue;
    }
    if (bracketStack.length) {
      masked[index] = " ";
      if (bracketPairs.has(character)) bracketStack.push(bracketPairs.get(character));
      else if (character === bracketStack[bracketStack.length - 1]) bracketStack.pop();
      continue;
    }
    if (character === "`") {
      let run = 1;
      while (characters[index + run] === "`") run += 1;
      for (let offset = 0; offset < run; offset += 1) masked[index + offset] = " ";
      backtickLength = run;
      index += run - 1;
      continue;
    }
    if (quotePairs.has(character)) {
      masked[index] = " ";
      quoteEnd = quotePairs.get(character);
      continue;
    }
    if (character === "'"
      && !/[\p{L}\p{N}]/u.test(characters[index - 1] || "")) {
      masked[index] = " ";
      quoteEnd = "'";
      continue;
    }
    if (bracketPairs.has(character)) {
      masked[index] = " ";
      bracketStack.push(bracketPairs.get(character));
    }
  }
  return masked.join("");
}

function splitAssertionSegments(line, boundaryPattern) {
  const masked = maskedAssertionText(line);
  const segments = [];
  let start = 0;
  boundaryPattern.lastIndex = 0;
  for (let match = boundaryPattern.exec(masked); match; match = boundaryPattern.exec(masked)) {
    segments.push(line.slice(start, match.index));
    start = boundaryPattern.lastIndex;
  }
  segments.push(line.slice(start));
  return segments;
}

function explicitStateContradictions(context, message) {
  const stageIds = context.policy.stage_order;
  const gateIds = context.policy.gate_order;
  const statuses = context.policy.state_contract.gate_statuses;
  const stagePattern = regexAlternation(stageIds);
  const gatePattern = regexAlternation(gateIds);
  const statusAliases = new Map([
    ...statuses.map((status) => [status.toLowerCase(), status.toLowerCase()]),
    ["待审批", "pending"],
    ["已批准", "approved"],
    ["已通过", "approved"],
    ["已重开", "reopened"],
  ]);
  const statusPattern = regexAlternation([...statusAliases.keys()]);
  const englishStatusPattern = regexAlternation(statuses);
  const chineseStatusPattern = regexAlternation(["待审批", "已批准", "已通过", "已重开"]);
  const stageDeclaration = new RegExp(
    `^(?:(?:the\\s+)?(?:current|active)(?:[-_\\s]+(?:(?:research|workflow)[-_\\s]+)?stage)|当前(?:研究|科研|工作流)?阶段)\\s*(?:[:：=]|\\bis\\b|为|是|[—–-])\\s*(${stagePattern})(?:\\b|\\s|[—–-]|[。.!]|$)`,
    "i",
  );
  const explicitGateDeclaration = new RegExp(
    `^(${gatePattern})(?:\\s+gate)?(?:\\s+status)?\\s*(?:[:：=]|\\bis\\b|为|是)\\s*[（(]?(${statusPattern})[）)]?(?=$|\\s|[。.!?？,，;；—–-])`,
    "i",
  );
  const labeledEnglishGateDeclaration = new RegExp(
    `^(${gatePattern})\\s+(?:gate(?:\\s+status)?|status)\\s+\\(?(${englishStatusPattern})\\)?(?=$|\\s|[.!?，,;；—–-])`,
    "i",
  );
  const bareEnglishGateDeclaration = new RegExp(
    `^(${gatePattern})\\s+(${englishStatusPattern})(?=$|[.!?，,;；—–-]|\\s+(?!(?:artifacts?|files?|outputs?|work|items?|content|manuscripts?|submissions?)\\b))`,
    "i",
  );
  const bareChineseGateDeclaration = new RegExp(
    `^(${gatePattern})(?:\\s+gate)?\\s+\\(?(${chineseStatusPattern})\\)?(?=$|[。！？，,;；—–-])`,
    "i",
  );
  const parenthesizedGateDeclaration = new RegExp(
    `^(${gatePattern})\\s*[（(](${statusPattern})[）)](?=$|\\s|[。.!?？,，;；—–-])`,
    "i",
  );
  const gateToExitDeclaration = new RegExp(
    `^(?:(?:the\\s+)?(?:gate\\s+to\\s+exit|next\\s+gate)|(?:下一道|退出)\\s*gate)\\s*(?:[:：=]|\\bis\\b|为|是|[—–-])\\s*(${gatePattern}|none|无)(?=$|\\s|[（(,，。.!—–-])\\s*(?:[（(](${statusPattern})[）)]|[,，]\\s*(?:currently|目前)?\\s*(?:is|为|是)?\\s*(${statusPattern})|[—–-]\\s*(${statusPattern}))?`,
    "i",
  );
  const declarationStart = `(?:[*_]{1,2})?(?:current|active|the\\s+(?:current|active|next)|当前|下一道|退出|${gatePattern})(?:\\b|[-_\\s:：=（(—–])`;
  const declarationBoundary = new RegExp(
    `(?:[;；]+|[。.,，—–]\\s*)\\s*(?=${declarationStart})`,
    "gi",
  );
  const independentSentenceBoundary = new RegExp(
    `[.!?。！？]\\s*(?=${declarationStart})`,
    "i",
  );
  const issues = new Set();
  const actualStage = scalar(context.state.current_stage, "missing");
  const spec = stageSpec(context);
  const expectedExitGate = spec && typeof spec.gate_to_exit === "string"
    ? spec.gate_to_exit
    : "none";
  let fenceMarker = null;
  let exampleScope = false;
  let exampleListMode = null;
  let tableSpec = null;

  const compareStage = (asserted) => {
    if (asserted !== actualStage) {
      issues.add(`The answer states current_stage=${asserted}; .research/state.json says ${actualStage}.`);
    }
  };
  const compareGate = (gate, asserted) => {
    const actual = scalar(gateStatus(context, gate), "missing");
    if (asserted !== actual) {
      issues.add(`The answer states ${gate}=${asserted}; .research/state.json says ${actual}.`);
    }
  };
  const compareExitGate = (gate) => {
    if (gate !== expectedExitGate) {
      issues.add(`The answer states gate_to_exit=${gate}; policy and current_stage require ${expectedExitGate}.`);
    }
  };
  const normalizeStatus = (status) => statusAliases.get(status.toLowerCase()) || "";
  const normalizeGate = (gate) => gate.toLowerCase() === "无" ? "none" : gate.toLowerCase();

  for (const rawLine of message.split("\n")) {
    const normalizedRawLine = cleanText(rawLine);
    if (/^(?: {4,}|\t)/.test(normalizedRawLine)) continue;
    const fenceMatch = normalizedRawLine.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (fenceMatch) {
      const marker = fenceMatch[1];
      if (!fenceMarker) {
        fenceMarker = { character: marker[0], length: marker.length };
      } else if (fenceMarker.character === marker[0]
        && marker.length >= fenceMarker.length
        && !fenceMatch[2].trim()) {
        fenceMarker = null;
      }
      continue;
    }
    if (fenceMarker) continue;
    const rawTrimmed = normalizedRawLine.trim();
    if (startsExampleScope(rawLine)) {
      exampleScope = true;
      exampleListMode = null;
      continue;
    }
    if (exampleScope) {
      if (!rawTrimmed) continue;
      if (startsActualScope(rawLine)) {
        exampleScope = false;
        exampleListMode = null;
        continue;
      }
      const isHeading = /^\s*#{1,6}\s+/.test(cleanText(rawLine));
      const isListItem = /^(?:[-*+]\s+|\d+[.)]\s+)/.test(rawTrimmed);
      if (isHeading) {
        exampleScope = false;
        exampleListMode = null;
      } else if (exampleListMode === null) {
        exampleListMode = isListItem;
        continue;
      } else if (exampleListMode === true && !isListItem) {
        exampleScope = false;
        exampleListMode = null;
      } else {
        continue;
      }
    }

    const line = assertionLine(rawLine);
    if (!line) continue;
    const tableCells = markdownTableCells(rawLine);
    if (tableCells) {
      if (markdownTableDivider(tableCells)) continue;
      const normalizedCells = tableCells.map((cell) => cell.toLowerCase().replace(/\s+/g, " "));
      const gateIndex = normalizedCells.findIndex((cell) => /^(?:gate(?: id)?|门禁)$/.test(cell));
      const statusIndex = normalizedCells.findIndex((cell) => /^(?:status|(?:current|actual)(?: gate)? status|(?:当前|实际)(?:gate)?状态)$/.test(cell));
      const fieldIndex = normalizedCells.findIndex((cell) => /^(?:field|item|字段|项目)$/.test(cell));
      const valueIndex = normalizedCells.findIndex((cell) => /^(?:(?:current|actual) (?:value|status|state)|(?:当前|实际)(?:值|状态))$/.test(cell));
      if (gateIndex >= 0 && statusIndex >= 0) {
        tableSpec = { kind: "gate", keyIndex: gateIndex, valueIndex: statusIndex };
        continue;
      }
      if (fieldIndex >= 0 && valueIndex >= 0) {
        tableSpec = { kind: "field", keyIndex: fieldIndex, valueIndex };
        continue;
      }
      if (gateIndex >= 0 || fieldIndex >= 0) {
        tableSpec = null;
        continue;
      }
      if (tableSpec
        && tableSpec.keyIndex < tableCells.length
        && tableSpec.valueIndex < tableCells.length) {
        const key = tableCells[tableSpec.keyIndex].trim();
        const value = tableCells[tableSpec.valueIndex].trim();
        if (tableSpec.kind === "gate") {
          const gate = normalizeGate(key);
          const statusMatch = value.match(new RegExp(`^[（(]?(${statusPattern})[）)]?$`, "i"));
          if (gateIds.includes(gate) && statusMatch) compareGate(gate, normalizeStatus(statusMatch[1]));
        } else if (/^(?:current[-_\s]+stage|当前(?:研究|科研|工作流)?阶段)$/i.test(key)) {
          const asserted = value.toLowerCase();
          if (stageIds.includes(asserted)) compareStage(asserted);
        } else {
          const gate = normalizeGate(key);
          const statusMatch = value.match(new RegExp(`^[（(]?(${statusPattern})[）)]?$`, "i"));
          if (gateIds.includes(gate) && statusMatch) compareGate(gate, normalizeStatus(statusMatch[1]));
        }
      }
      continue;
    }
    tableSpec = null;

    const contextKind = assertionContextKind(line);
    if (contextKind === "example"
      || (contextKind && !independentSentenceBoundary.test(maskedAssertionText(rawLine)))) continue;
    for (const rawSegment of splitAssertionSegments(rawLine, declarationBoundary)) {
      const segment = assertionLine(rawSegment);
      if (!segment) continue;
      const stageMatch = segment.match(stageDeclaration);
      if (stageMatch && !assertionIsQualified(segment, stageMatch)) {
        compareStage(stageMatch[1].toLowerCase());
      }

      const exitMatch = segment.match(gateToExitDeclaration);
      if (exitMatch && !assertionIsQualified(segment, exitMatch)) {
        const gate = normalizeGate(exitMatch[1]);
        const assertedStatus = normalizeStatus(exitMatch[2] || exitMatch[3] || exitMatch[4] || "");
        compareExitGate(gate);
        if (assertedStatus && gateIds.includes(gate)) compareGate(gate, assertedStatus);
        continue;
      }

      const gateMatch = segment.match(explicitGateDeclaration)
        || segment.match(labeledEnglishGateDeclaration)
        || segment.match(bareEnglishGateDeclaration)
        || segment.match(bareChineseGateDeclaration)
        || segment.match(parenthesizedGateDeclaration);
      if (gateMatch && !assertionIsQualified(segment, gateMatch)) {
        compareGate(gateMatch[1].toLowerCase(), normalizeStatus(gateMatch[2]));
      }
    }
  }
  return [...issues];
}

function stopAudit(context, input) {
  if (getStopHookActive(input)) return {};
  const message = lastAssistantMessage(input);
  if (!message.trim()) return {};
  const stateCheck = validateState(context);
  if (stateCheck.errors.length) {
    return {
      continue: true,
      systemMessage: bounded([
        "[RESEARCH STOP OBSERVER] The active .research state failed mechanical validation, so this non-blocking observer did not compare workflow claims.",
        listLines(stateCheck.errors.slice(0, 4)),
        "Run researchctl doctor before relying on stage or Gate statements. No additional model turn was requested.",
      ].join("\n"), MAX_STOP_REASON_CHARS),
    };
  }
  const contradictions = explicitStateContradictions(context, message);
  if (contradictions.length) {
    const reason = bounded([
      "The Stop observer found an explicit mechanical contradiction with .research/state.json or the canonical policy:",
      listLines(contradictions),
      "Treat the preceding response as a draft and return one complete, self-contained corrected answer. Preserve supported content, correct only the listed contradiction, and do not return a standalone audit addendum or private chain-of-thought.",
      "This Hook requests one necessary continuation; stop_hook_active prevents another Stop loop.",
    ].join("\n"), MAX_STOP_REASON_CHARS);
    return { decision: "block", reason };
  }
  const firstAssertion = message.split("\n").map(assertionLine).find(Boolean) || "";
  if (/^\[Stop Hook Review\]/i.test(firstAssertion)) {
    return {
      continue: true,
      systemMessage: "[RESEARCH STOP OBSERVER] The response begins with the legacy standalone [Stop Hook Review] marker. This non-blocking observer did not request another model turn; if the main answer is missing, regenerate it in full.",
    };
  }
  return {};
}

function handleEvent(event, context, input) {
  switch (event) {
    case "SessionStart":
      return hookContextOutput("SessionStart", sessionContext(context));
    case "UserPromptSubmit":
      return clearlyCodeOnlyPrompt(input)
        ? {}
        : hookContextOutput("UserPromptSubmit", promptContext(context));
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
