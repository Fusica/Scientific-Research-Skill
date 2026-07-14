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
const MAX_INPUT_CHARS = 256 * 1024;
const MAX_SESSION_CONTEXT_CHARS = 800;
const MAX_PROMPT_CONTEXT_CHARS = 1200;
const MAX_POST_CONTEXT_CHARS = 4200;
const MAX_STOP_REASON_CHARS = 1800;
const MAX_TOOL_TEXT_CHARS = 64 * 1024;

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

function safeReadText(candidate, maxChars) {
  if (!isFile(candidate)) return null;
  try {
    return fs.readFileSync(candidate, "utf8").slice(0, maxChars);
  } catch (_error) {
    return null;
  }
}

function safeReadObject(candidate) {
  const text = safeReadText(candidate, MAX_INPUT_CHARS);
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
  return typeof value === "string" ? cleanText(value).slice(0, MAX_INPUT_CHARS) : "";
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
  if (typeof fromInput === "string" && SUPPORTED_EVENTS.has(fromInput)) return fromInput;
  const fromArg = process.argv[2];
  return SUPPORTED_EVENTS.has(fromArg) ? fromArg : null;
}

function findResearchRoot(start) {
  if (typeof start !== "string" || !start.trim()) return null;
  let current;
  try {
    current = path.resolve(start);
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
  const policy = safeReadObject(candidate);
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
  return policy;
}

function activeProject(input) {
  const cwd = inputCwd(input);
  if (!cwd) return null;
  const root = findResearchRoot(cwd);
  if (!root) return null;
  const statePath = path.join(root, ".research", "state.json");
  const state = safeReadObject(statePath);
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
  /(?:重构|调试|排查|修复|简化|整理|格式化|重命名|优化|解释|分析|讲解|检查|理解|看看)[^。！？\n]{0,60}(?:代码|函数|类|接口|模块|循环|变量|脚本|算法实现|代码实现|代码逻辑|控制流|报错|错误|单元测试|[\w.-]+\.(?:py|js|jsx|ts|tsx|java|c|cc|cpp|h|hpp|rs|go|rb|php|swift|kt|sh))/,
  /(?:代码|函数|类|接口|模块|循环|变量|脚本|算法实现|代码实现|代码逻辑|控制流|报错|错误|单元测试|[\w.-]+\.(?:py|js|jsx|ts|tsx|java|c|cc|cpp|h|hpp|rs|go|rb|php|swift|kt|sh))[^。！？\n]{0,60}(?:重构|调试|排查|修复|简化|整理|格式化|重命名|优化|解释|分析|讲解|检查|理解|为什么|怎么|如何|细节|问题|作用)/,
  /(?:代码优化|优化代码|代码细节|代码逻辑|单元测试|测试代码|运行测试|补充测试)/,
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
const RESEARCH_EXECUTION_PATTERN = /\b(?:run|launch|execute|start)\s+(?:the\s+)?(?:(?:approved|registered|full|new|next)\s+)?(?:experiment|training|benchmark|evaluation)s?\b(?!\s+(?:script|code|runner|pipeline|test))/i;
const CHINESE_RESEARCH_ACTION_PATTERN = /(?:验证|核验|分析|解释结果|解读|比较|汇报|评估|论证|证明|支持|下结论|审计|冻结|总结|梳理|判断|评价|提升|提高|降低|减少)/;
const CHINESE_RESEARCH_OBJECT_PATTERN = /(?:实验结果|实验输出|训练结果|评估结果|结果指标|准确率|精确率|召回率|基线|消融|科研主张|创新性|新颖性|科研证据|研究假设)/;
const CHINESE_RESEARCH_EXECUTION_PATTERN = /(?:运行|启动|执行|开始)(?:已批准的|已登记的|完整的|新的|下一轮)?(?:实验|训练|基准测试|评估)(?!脚本|代码|程序|测试)/;

const RESEARCH_OBJECT_CODE_PATTERN = /\b(?:experiment|evaluation|training)\s+(?:result|output|finding)s?\s+(?:parser|loader|reader|processor|function|class|module|script|code|implementation)\b/i;
const CHINESE_RESEARCH_OBJECT_CODE_PATTERN = /(?:实验结果|实验输出|训练结果|评估结果)(?:解析|处理|读取|加载)(?:器|函数|类|模块|脚本|代码|实现)?/;
const METRIC_CODE_PATTERN = /\b(?:accuracy|precision|recall|F1|f1|mAP|AUC|IoU|Dice|loss|metric)\s+(?:calculation|calculator|function|parser|code|implementation)\b/i;

function hasStrongResearchIntent(prompt, codeIntent) {
  if (WORKFLOW_PROMPT_PATTERNS.some((pattern) => pattern.test(prompt))) return true;
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
    return false;
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
    "Use the $research Skill and load only the current-stage reference. policy.yaml is authoritative for evidence, Gate, and exit criteria.",
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
  return /(?:^|[\s"'/:])\.research\/+state\.json(?:$|[\s"':,}\]])/i.test(text)
    || /\.research\/+state\.json/i.test(text);
}

function mentionsBareStateFile(text) {
  return /(?:^|[\s"'=/:])(?:\.\.\/|\.\/)*state\.json(?:$|[\s"',;:&|}\]])/i.test(text);
}

function shellChangesIntoResearch(command) {
  const normalized = command.replace(/\\/g, "/");
  const directoryChange = /(?:^|[;&|]\s*|\()\s*(?:cd|pushd)\s+(?:--\s+)?(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))/gi;
  let match;
  while ((match = directoryChange.exec(normalized)) !== null) {
    const candidate = match[1] || match[2] || match[3] || "";
    if (/(?:^|\/)\.research(?:\/|$)/i.test(candidate)) return true;
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
    const researchRoot = path.join(context.root, ".research");
    return resolved === researchRoot || resolved.startsWith(`${researchRoot}${path.sep}`);
  } catch (_error) {
    return false;
  }
}

function shellTargetsStateFile(context, toolInput, command) {
  const normalized = command.replace(/\\/g, "/");
  if (mentionsStateFile(normalized)) return true;
  if (!mentionsBareStateFile(normalized)) return false;
  return shellChangesIntoResearch(normalized) || toolRunsInsideResearch(context, toolInput);
}

function patchTargetPaths(toolInput) {
  const patch = commandText(toolInput) || stringifyToolValue(toolInput);
  const paths = [];
  const header = /^\*\*\*\s+(?:Add|Update|Delete|Move to)\s+File:\s*(.+)$/gim;
  const unified = /^(?:---|\+\+\+)\s+(?:[ab]\/)?([^\t\n]+)(?:\t.*)?$/gm;
  for (const pattern of [header, unified]) {
    let match;
    while ((match = pattern.exec(patch)) !== null) paths.push(match[1].trim());
  }
  return paths.join("\n").replace(/\\/g, "/");
}

function pathFields(value, result = []) {
  if (!value || typeof value !== "object" || result.length >= 32) return result;
  if (Array.isArray(value)) {
    value.forEach((child) => pathFields(child, result));
    return result;
  }
  const pathKeys = new Set([
    "path",
    "file",
    "file_path",
    "filePath",
    "target",
    "target_path",
    "targetPath",
    "destination",
    "destination_path",
    "destinationPath",
  ]);
  for (const [key, child] of Object.entries(value)) {
    if (pathKeys.has(key) && typeof child === "string") result.push(child);
    else if (child && typeof child === "object") pathFields(child, result);
    if (result.length >= 32) break;
  }
  return result;
}

function targetsStateFile(context, toolName, toolInput, command) {
  if (isPatchTool(toolName)) return mentionsStateFile(patchTargetPaths(toolInput));
  if (isShellTool(toolName)) return shellTargetsStateFile(context, toolInput, command);
  const paths = pathFields(toolInput).join("\n").replace(/\\/g, "/");
  return mentionsStateFile(paths)
    || (mentionsBareStateFile(paths) && toolRunsInsideResearch(context, toolInput));
}

function dangerousShellReason(command) {
  const checks = [
    [/\brm\s+(?:-[^\s]*[rR][^\s]*[fF]|-[^\s]*[fF][^\s]*[rR])\b/, "recursive forced deletion"],
    [/\bgit\s+reset\s+--hard\b/i, "git reset --hard"],
    [/\bgit\s+clean\s+-[^\s]*[fdx][^\s]*\b/i, "destructive git clean"],
    [/\bgit\s+checkout\s+--(?:\s|$)/i, "destructive worktree restoration"],
    [/\bgit\s+restore\s+--source(?:=|\s+)/i, "destructive worktree restoration"],
    [/(?:^|[;&|]\s*|\s)(?:sudo\s+)?(?:mkfs(?:\.\w+)?|shutdown|reboot)\b/i, "system-destructive command"],
    [/\bdiskutil\s+(?:erase|partitionDisk)\b/i, "disk erase or repartition"],
    [/\bdd\b[\s\S]*\bof=\/dev\//i, "raw device overwrite"],
    [/\bchmod\s+-R\s+777\s+\/(?:\s|$)/i, "recursive permission change at filesystem root"],
  ];
  for (const [pattern, label] of checks) {
    if (pattern.test(command)) return label;
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
    || /(?:>>?|\btee\b|\btruncate\b|\brm\b|\bmv\b|\bcp\b|\btouch\b|\bsed\b[\s\S]*\s-i\b|\bwriteFile|\bwrite_text|\bjson\.dump\b)/i.test(effective);
}

function gateBlocked(context, gate) {
  return context.policy.gate_order.includes(gate) && gateStatus(context, gate) !== "approved";
}

function explicitExperimentLaunch(toolName, command, text) {
  if (/(?:run|launch|start)[_:-]?(?:experiment|training|benchmark)|takeoff|arm[_:-]?drone/i.test(toolName)) {
    return true;
  }
  if (!command) return false;
  return /\b(?:sbatch|qsub|wandb\s+sweep|roslaunch|ros2\s+launch)\b/i.test(command)
    || /\bpython(?:3)?\s+[^\n;&|]*(?:train|experiment|benchmark)[^\s/]*\.py\b/i.test(command)
    || /\b(?:takeoff|arm_drone|arm-uav)\b/i.test(text);
}

function manuscriptMutation(toolName, text) {
  if (!(isMutatingTool(toolName) || isShellTool(toolName))) return false;
  const target = /(?:^|[\s"'/])(?:main|paper|manuscript|appendix|respond|response|rebuttal)\.(?:tex|md|docx)(?:$|[\s"',}:\]])/i;
  if (!target.test(text)) return false;
  if (!isShellTool(toolName)) return true;
  const command = text;
  return /(?:>>?|\btee\b|\bsed\b[\s\S]*\s-i\b|\bperl\b[\s\S]*\s-i\b|\bapply_patch\b|\bwriteFile|\bwrite_text)/i.test(command);
}

function externalReleaseAction(toolName, command, text) {
  const action = /(?:^|[:._-])(send|submit|publish|upload|post|forward|release)(?:$|[:._-])/i.test(toolName)
    || /\b(?:submit|publish|upload|send|post|forward)\b/i.test(command);
  const subject = /\b(?:manuscript|paper|rebuttal|reviewer[_ -]?response|camera[_ -]?ready|openreview|softconf)\b/i.test(text)
    || /(?:论文|稿件|审稿回复|投稿|返修回复)/.test(text);
  return action && subject;
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
      return deny("Direct mutation of .research/state.json is blocked. Use researchctl artifact register, enable|disable, gate, or checkpoint so artifact and Gate state changes remain validated and traceable.");
    }
  }

  if (
    gateBlocked(context, "method_experiment_approval")
    && explicitExperimentLaunch(toolName, command, text)
  ) {
    return deny("This is an explicit experiment, training, cluster, or hardware launch, but method_experiment_approval is not approved. Prepare the method and experiment contract, then record human approval through researchctl.");
  }

  if (gateBlocked(context, "claim_freeze") && manuscriptMutation(toolName, text)) {
    return deny("This tool call mechanically targets a manuscript or rebuttal artifact before claim_freeze is approved. Freeze evidence-bounded claims through researchctl before entering paper production.");
  }

  if (gateStatus(context, "release") === "approved" && manuscriptMutation(toolName, text)) {
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
  if (value === null || value === undefined) return;
  const parts = label.split(".");
  const canonical = parts.length >= 4 && parts[0] === "artifacts" && stageIds.has(parts[1]);
  if (typeof value === "string") {
    if (!value.trim()) {
      const message = `${label} is an empty artifact path`;
      (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
      return;
    }
    const candidate = path.isAbsolute(value) ? value : path.resolve(root, value);
    if (!fs.existsSync(candidate)) warnings.push(`${label} points to a missing artifact: ${value}`);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((child, index) => validateArtifactPointers(
      root,
      child,
      `${label}[${index}]`,
      errors,
      warnings,
      pointerFields,
      stageIds,
    ));
    return;
  }
  if (typeof value !== "object") {
    const message = `${label} must be a path string, {path}, list, or nested object`;
    (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
    return;
  }
  if (Object.prototype.hasOwnProperty.call(value, "path")) {
    const pathValue = value.path;
    const missing = pointerFields.filter((field) => !Object.prototype.hasOwnProperty.call(value, field));
    if (missing.length) {
      const message = `${label} missing fields: ${missing.join(", ")}`;
      (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
    }
    if (typeof pathValue !== "string" || !pathValue.trim()) {
      const message = `${label}.path must be a non-empty string`;
      (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
      return;
    }
    const candidate = path.isAbsolute(pathValue) ? pathValue : path.resolve(root, pathValue);
    const controlFiles = new Set([
      path.resolve(root, ".research", "state.json"),
      path.resolve(root, ".research", "memory.md"),
      path.resolve(root, ".research", "project-state.yaml"),
    ]);
    if (controlFiles.has(path.resolve(candidate))) {
      const message = `${label} points to research control metadata, which cannot be evidence: ${pathValue}`;
      (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
      return;
    }
    if (!fs.existsSync(candidate)) {
      const message = `${label} points to a missing artifact: ${pathValue}`;
      (canonical ? errors : warnings).push(message);
      return;
    }
    if (canonical && !isFile(candidate)) {
      errors.push(`${label} must point to a regular file: ${pathValue}`);
    }
    return;
  }
  const pointerMetadata = pointerFields.filter((field) => field !== "path");
  if (pointerMetadata.some((key) => Object.prototype.hasOwnProperty.call(value, key))) {
    const message = `${label} is an artifact pointer but has no path`;
    (canonical ? errors : warnings).push(canonical ? message : `legacy artifact pointer: ${message}`);
    return;
  }
  for (const [key, child] of Object.entries(value)) {
    validateArtifactPointers(
      root,
      child,
      `${label}.${key}`,
      errors,
      warnings,
      pointerFields,
      stageIds,
    );
  }
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
        const last = record.history[record.history.length - 1];
        if (!last || typeof last !== "object") {
          errors.push(`Gate ${gate} last history entry must be an object`);
        } else {
          if (last.decision_id !== record.latest_decision_id) errors.push(`Gate ${gate} latest_decision_id does not match history`);
          if (last.new_status !== record.status) errors.push(`Gate ${gate} status does not match history`);
          if (typeof last.reason !== "string" || !last.reason.trim()) errors.push(`Gate ${gate} last decision needs a reason`);
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
      }
    }
  }
  if (Object.prototype.hasOwnProperty.call(state, "stage_history") && !Array.isArray(state.stage_history)) {
    errors.push("stage_history must be an array");
  }
  if (!isFile(context.memoryPath)) errors.push("missing .research/memory.md");
  return { errors, warnings };
}

function postToolUse(context, input) {
  if (!stateWasTouched(context, input)) return {};
  const result = validateState(context);
  const lines = [
    "[POST-TOOL RESEARCH STATE CHECK]",
    result.errors.length
      ? `Schema/Gate errors (${result.errors.length}):\n${listLines(result.errors)}`
      : "Schema and Gate invariants: valid.",
    result.warnings.length
      ? `Artifact warnings (${result.warnings.length}):\n${listLines(result.warnings)}`
      : "Registered artifact pointers: valid and currently resolvable.",
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

  const gateOrWorkflowState = /\b(?:idea[_ -]?freeze|method[_ -]?experiment[_ -]?approval|claim[_ -]?freeze|release\s+gate|researchctl\s+(?:gate|artifact|checkpoint|enable|disable|init)|gate\s+(?:is\s+)?(?:pending|approved|reopened|frozen))\b/i.test(message)
    || /(?:门禁|闸门|冻结|批准|研究阶段)[^。！？\n]{0,50}(?:待定|通过|批准|重开|完成|进入|切换)/.test(message);
  if (gateOrWorkflowState) return true;

  const researchResultSubject = /\b(?:experiment|evaluation|benchmark|training)\s+(?:result|output|finding)s?\b/i.test(message)
    || /(?:实验结果|实验输出|实验发现|评估结果|基准结果|训练结果)/.test(message);
  const researchResultAssertion = /\b(?:completed?|finished|verified|validated|checked|measured|achieved|improved?|increased?|decreased?|reduced?|outperformed?|failed|excluded|supports?|shows?)\b/i.test(message)
    || /(?:已完成|完成了|已验证|验证通过|已检查|测得|达到|提升|提高|增加|下降|降低|优于|失败|排除|支持|表明|显示)/.test(message);
  if (researchResultSubject && researchResultAssertion) return true;

  const metricSubject = /\b(?:accuracy|precision|recall|loss|metric|sample\s+size)\b/i.test(message)
    || PERFORMANCE_METRIC_PATTERN.test(message)
    || /(?:准确率|精确率|召回率|平均精度|交并比|损失|指标|样本量)/.test(message);
  const metricAssertion = /\b(?:measured|achieved|improved?|increased?|decreased?|reduced?|outperformed?|failed|excluded)\b/i.test(message)
    || /(?:测得|达到|提升|提高|增加|下降|降低|优于|失败|排除)/.test(message)
    || /(?:\b(?:accuracy|precision|recall|loss|metric|sample\s+size)\b|\b(?:F1|f1|mAP|AUC|IoU|Dice)\b|(?:准确率|精确率|召回率|平均精度|交并比|损失|指标|样本量))[^。！？\n]{0,50}\d+(?:\.\d+)?\s*%?/.test(message);
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
    "Run the single stop-time semantic audit with the current session model before returning the final answer. Do not reveal private chain-of-thought; return only a corrected, evidence-bounded user-facing answer.",
    `Active stage: ${scalar(context.state.current_stage, "invalid")}`,
    `Gate to exit: ${gate ? `${gate} (${scalar(gateStatus(context, gate), "missing")})` : "none"}`,
    "Check the applicable policy invariants:",
    listLines(auditItems),
    "Correct any issue and then finish. This Hook requests exactly one continuation; stop_hook_active prevents another audit loop.",
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
