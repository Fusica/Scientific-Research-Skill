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
const MAX_INPUT_CHARS = 8 * 1024 * 1024;
const MAX_POLICY_CHARS = 256 * 1024;
const MAX_STATE_CHARS = 8 * 1024 * 1024;
const MAX_EVENT_TEXT_CHARS = 256 * 1024;
const MAX_SESSION_CONTEXT_CHARS = 800;
const MAX_PROMPT_CONTEXT_CHARS = 1200;
const MAX_POST_CONTEXT_CHARS = 4200;
const MAX_STOP_REASON_CHARS = 1800;
const MAX_TOOL_TEXT_CHARS = 64 * 1024;
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
    let input = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      if (input.length <= MAX_INPUT_CHARS) input += chunk;
    });
    process.stdin.on("end", () => resolve(input.slice(0, MAX_INPUT_CHARS)));
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

function safeReadText(candidate, maxChars) {
  try {
    const stats = fs.statSync(candidate);
    if (!stats.isFile() || stats.size > maxChars) return null;
    return fs.readFileSync(candidate, "utf8").slice(0, maxChars);
  } catch (_error) {
    return null;
  }
}

function safeReadObject(candidate, maxChars = MAX_INPUT_CHARS) {
  const text = safeReadText(candidate, maxChars);
  return text === null ? null : parseObject(text);
}

function cleanText(value) {
  return String(value ?? "")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "?")
    .replace(/\r\n?/g, "\n");
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
  const policy = safeReadObject(candidate, MAX_POLICY_CHARS);
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
  const state = safeReadObject(statePath, MAX_STATE_CHARS);
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
  return bounded([
    "[RESEARCH WORKFLOW — PROMPT RELEVANT]",
    `Current stage: ${stage} — ${scalar(spec.label, "unlabeled")}`,
    `Gate to exit: ${gate ? `${gate} (${scalar(gateStatus(context, gate), "missing")})` : "none"}`,
    "Current-stage prohibited actions:",
    listLines(spec.prohibited_actions),
    "",
    `Artifact layout: ${scalar(context.policy.artifact_layout.instruction)}`,
    "Use the $research Skill, follow policy.yaml review_language, and load only the current-stage reference. policy.yaml is authoritative for evidence, Gate, and exit criteria.",
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

function hasMaterialResearchContent(input) {
  const message = lastAssistantMessage(input).trim();
  if (!message) return false;

  const metricToken = String.raw`(?:accuracy|precision|recall|loss|top[- ]?[15]|F1|f1|mAP|AUC|IoU|Dice|RMSE|MSE|MAE|R(?:2|²)|PSNR|SSIM|BLEU|ROUGE|准确率|精确率|召回率|平均精度|交并比|损失|均方根误差)`;
  const disclaimedMetricBug = /\b(?:incorrectly|wrongly|erroneously|falsely)\s+(?:reported|showed|displayed)\b[^.\n]{0,100}\d+(?:\.\d+)?\s*%?/i.test(message)
    || /(?:错误|不正确|误)(?:报告|显示|输出)[^。！？\n]{0,80}\d+(?:\.\d+)?\s*%?/.test(message);
  const quantitativeMetric = !disclaimedMetricBug && new RegExp(
    `(?:${metricToken})[^。！？\\n]{0,60}\\d+(?:\\.\\d+)?\\s*%?|\\d+(?:\\.\\d+)?\\s*%?[^。！？\\n]{0,60}(?:${metricToken})`,
    "i",
  ).test(message);
  const statisticalFinding = /\b(?:statistically\s+)?significant\b[^.\n]{0,100}(?:p\s*[<=>]|confidence\s+interval|\bCI\b)|\bp\s*[<=>]\s*0?\.\d+|\b95\s*%\s*(?:confidence\s+interval|CI)\b/i.test(message)
    || /(?:显著|统计显著)[^。！？\n]{0,80}(?:p\s*[<=>]|置信区间)/.test(message);
  const excludedOrNullFinding = /\b(?:failed|invalid|outlier)\s+runs?\b[^.\n]{0,50}\bexcluded\b|\bno\s+(?:statistically\s+)?significant\s+difference\b|\b(?:null|negative)\s+(?:result|finding)s?\b/i.test(message)
    || /(?:失败|无效|异常)(?:运行|实验)[^。！？\n]{0,40}(?:已)?排除|(?:未发现|没有)显著差异|(?:空|阴性|负面)结果/.test(message);
  const codeRepair = /\b(?:fixed|debugged|refactored|updated|repaired)\b[\s\S]{0,100}\b(?:parser|loader|reader|function|class|module|script|code|bug|unit\s+tests?)\b/i.test(message)
    && /\b(?:tests?\s+(?:pass|passed)|lint\s+(?:passes|passed)|bug|code|parser|loader)\b/i.test(message)
    && !/\b(?:manuscript|paper|rebuttal|reviewer|experiment\s+result|evaluation\s+result|research\s+claim)\b/i.test(message);
  const materialClaimAlongsideCode = /\b(?:central|scientific|research)\s+claim\b/i.test(message)
    || /\b(?:supports?|proves?|demonstrates?|establishes?)\b[\s\S]{0,50}\bclaim\b/i.test(message)
    || /\b(?:outperforms?|beats?|exceeds?)\b[\s\S]{0,60}\b(?:baseline|prior\s+work|sota)\b/i.test(message)
    || quantitativeMetric || statisticalFinding || excludedOrNullFinding;
  if (codeRepair && !materialClaimAlongsideCode) return false;
  if (quantitativeMetric || statisticalFinding || excludedOrNullFinding) return true;

  const gateOrWorkflowState = /\b(?:idea[_ -]?freeze|method[_ -]?experiment[_ -]?approval|claim[_ -]?freeze|release\s+gate|researchctl\s+(?:gate|artifact|checkpoint|enable|disable|init)|gate\s+(?:is\s+)?(?:pending|approved|reopened|frozen))\b/i.test(message)
    || /(?:门禁|闸门|冻结|批准|研究阶段)[^。！？\n]{0,50}(?:待定|通过|批准|重开|完成|进入|切换)/.test(message);
  if (gateOrWorkflowState) return true;

  const researchResultSubject = /\b(?:experiment|evaluation|benchmark|training)\s+(?:result|output|finding)s?\b/i.test(message)
    || /(?:实验结果|实验输出|实验发现|评估结果|基准结果|训练结果)/.test(message);
  const researchResultAssertion = /\b(?:completed?|finished|verified|validated|checked|measured|achieved|improved?|increased?|decreased?|reduced?|outperformed?|failed|excluded|supports?|shows?)\b/i.test(message)
    || /(?:已完成|完成了|已验证|验证通过|已检查|测得|达到|提升|提高|增加|下降|降低|优于|失败|排除|支持|表明|显示)/.test(message);
  if (researchResultSubject && researchResultAssertion) return true;

  const metricSubject = /\b(?:accuracy|precision|recall|loss|metric|sample\s+size|top[- ]?[15]|bleu|rouge|RMSE|MSE|MAE|R2|PSNR|SSIM)\b/i.test(message)
    || PERFORMANCE_METRIC_PATTERN.test(message)
    || /(?:准确率|精确率|召回率|平均精度|交并比|损失|指标|样本量)/.test(message);
  const metricAssertion = /\b(?:measured|achieved|improved?|increased?|decreased?|reduced?|outperformed?|failed|excluded)\b/i.test(message)
    || /(?:测得|达到|提升|提高|增加|下降|降低|优于|失败|排除)/.test(message)
    || /(?:\b(?:accuracy|precision|recall|loss|metric|sample\s+size|top[- ]?[15]|bleu|rouge)\b|\b(?:F1|f1|mAP|AUC|IoU|Dice)\b|(?:准确率|精确率|召回率|平均精度|交并比|损失|指标|样本量))[^。！？\n]{0,50}\d+(?:\.\d+)?\s*%?/.test(message);
  if (metricSubject && metricAssertion) return true;

  const experimentCompletion = /\b(?:experiment|evaluation|benchmark|training)\b(?!\s+(?:script|code|runner|pipeline|test))[\s\S]{0,60}\b(?:completed?|finished|executed|launched)\b/i.test(message)
    || /\b(?:completed?|finished|executed|launched)\b[\s\S]{0,40}\b(?:experiment|evaluation|benchmark|training)\b(?!\s+(?:script|code|runner|pipeline|test))/i.test(message)
    || /(?:实验|评估|基准测试|训练)(?!脚本|代码|程序|测试)[^。！？\n]{0,40}(?:已完成|完成了|已执行|已启动)/.test(message)
    || /(?:已完成|完成了|已执行|已启动)[^。！？\n]{0,30}(?:实验|评估|基准测试|训练)(?!脚本|代码|程序|测试)/.test(message);
  if (experimentCompletion) return true;

  const claimSubject = /\b(?:claim|novel|novelty|citation|evidence|hypothesis|prior\s+work|literature\s+(?:search|review))\b/i.test(message)
    || /(?:主张|论点|创新性|新颖性|引用|证据|假设|相关工作|文献(?:检索|综述))/.test(message);
  const claimAssertion = /\b(?:proves?|supports?|shows?|demonstrates?|establishes?|verified|validated|checked|complete|completed|falsified|confirmed|novel)\b/i.test(message)
    || /(?:证明|支持|表明|显示|建立|已验证|已核查|已检查|完成|证伪|确认|创新|新颖)/.test(message);
  if (claimSubject && claimAssertion) return true;

  const comparativeClaim = /\b(?:method|model|approach|system|algorithm)\b[\s\S]{0,80}\b(?:outperforms?|beats?|exceeds?|is\s+(?:better|superior))\b[\s\S]{0,80}\b(?:baseline|prior\s+work|state[- ]of[- ]the[- ]art|sota)\b/i.test(message)
    || /\b(?:benchmark|evaluation|experiment)\b[\s\S]{0,80}\b(?:shows?|demonstrates?|indicates?)\b[\s\S]{0,100}\b(?:outperforms?|better|superior|stronger)\b/i.test(message)
    || /(?:方法|模型|方案|系统|算法)[^。！？\n]{0,60}(?:优于|超过|胜过)[^。！？\n]{0,40}(?:基线|现有方法|已有工作|最先进方法)/.test(message)
    || /(?:基准|评估|实验)[^。！？\n]{0,60}(?:表明|显示|证明)[^。！？\n]{0,80}(?:更好|更强|更优|优越)/.test(message);
  if (comparativeClaim) return true;

  const deliveryText = message
    .replace(/\b(?:(?:manuscript|paper|researchctl|experiment|training|literature|citation)\s+(?:parser|generator|script|runner|pipeline|code|cli|tool|module|function|class|implementation)|(?:parser|generator|script|runner|pipeline|code|cli|tool|module|function|class)\s+(?:for|of)\s+(?:the\s+)?(?:manuscript|paper|researchctl|experiment|training|literature|citation))\b/gi, "")
    .replace(/(?:论文|稿件|实验|训练|文献|引用)(?:生成|解析|处理|读取|加载)(?:脚本|代码|工具|模块|函数|类|实现)?/g, "");
  const deliverySubject = /\b(?:idea\s+card|evidence\s+matrix|method\s+contract|experiment\s+registry|run\s+registry|claim\s+ledger|manuscript|paper|rebuttal|reviewer\s+response|release\s+checklist|submission)\b/i.test(deliveryText)
    || /(?:想法卡|证据矩阵|方法合同|实验登记|运行登记|主张台账|论文|稿件|返修|审稿回复|回复信|发布清单|投稿)/.test(deliveryText);
  const deliveryAssertion = /\b(?:created|prepared|updated|edited|changed|revised|rewritten|completed|verified|ready|compiled|rendered|submitted|sent)\b/i.test(deliveryText)
    || /(?:(?:已|已经)(?:创建|准备|更新|编辑|修订|重写|改写|修改|完成|验证|编译|渲染|提交|发送)|就绪)/.test(deliveryText);
  return deliverySubject && deliveryAssertion;
}

function stopAudit(context, input) {
  if (getStopHookActive(input) || !hasMaterialResearchContent(input)) return {};
  const spec = stageSpec(context);
  const gate = spec && typeof spec.gate_to_exit === "string" ? spec.gate_to_exit : null;
  const auditItems = Array.isArray(context.policy.semantic_audit)
    ? context.policy.semantic_audit
    : [];
  const reason = bounded([
    "Run the single stop-time semantic audit with the current session model. The preceding assistant answer must remain unchanged: do not reproduce, rewrite, replace, or silently correct it.",
    "Return only a concise, evidence-bounded audit addendum in the same language as the preceding answer, beginning with `[Stop Hook Review]`. State that the review passed when no material issue is found; otherwise state the issue and the bounded correction the user should apply. Do not reveal private chain-of-thought.",
    `Active stage: ${scalar(context.state.current_stage, "invalid")}`,
    `Gate to exit: ${gate ? `${gate} (${scalar(gateStatus(context, gate), "missing")})` : "none"}`,
    "Check the applicable policy invariants:",
    listLines(auditItems),
    "Finish after the audit addendum. This Hook requests exactly one continuation; stop_hook_active prevents another audit loop.",
  ].join("\n"), MAX_STOP_REASON_CHARS);
  return { decision: "block", reason };
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
