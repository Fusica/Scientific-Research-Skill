# Scientific Research Skill

一个面向 Codex 的项目级科研 Plugin：用一个 `$research` 入口贯通 idea、文献、方法、实验与结果、论文、返修六个阶段，同时用确定性命令维护 Gate，用 Hook 约束可机械判断的越界行为。

仓库不承诺自动产出“顶刊论文”，而是让研究判断、证据、实验和写作之间的关系清楚、可恢复、可审计。

## 核心结构

```text
Codex Plugin
├── Skill: $research                 唯一对话入口与阶段路由
├── Rules: references/policy.yaml    唯一流程、Gate 和退出条件
├── Command: scripts/researchctl.py  唯一状态与 Gate 写入口
├── Hooks                            项目上下文、工具边界、停止前复核
└── Project state: .research/        当前项目的本地状态与记忆
```

## 六阶段与四个 Gate

```mermaid
flowchart LR
    A[1 Idea] <--> B[2 Literature]
    B --> G1{{idea_freeze}}
    G1 --> C[3 Method]
    C --> G2{{method_experiment_approval}}
    G2 --> D[4 Experiment + Results]
    D --> G3{{claim_freeze}}
    G3 --> E[5 Paper]
    E --> G4{{release}}
    G4 --> F[6 Revision]
    F -->|new evidence or method issue| B
```

每个 Gate 都要求研究者明确批准或重开。模型不能把沉默、任务完成或一段积极表述解释成批准。

| 阶段                 | 主要职责                                                           |
| -------------------- | ------------------------------------------------------------------ |
| Idea                 | 生成候选、反证、可行性判断、预测与 kill criteria                   |
| Literature           | 背景搜索、closest work、证据矩阵、novelty 边界与 idea 迭代         |
| Method               | 假设、数学定义、模块接口、算法与可检验预测                         |
| Experiment + Results | 基线、实验矩阵、执行记录、失败诊断、统计分析与 Claim—Evidence 对齐 |
| Paper                | 结构、写作、数字与引用追溯、自审、编译和投稿检查                   |
| Revision             | reviewer concern、补充证据、论文修改与逐点回复闭环                 |

## 安装

Plugin 在每台 Codex 主机安装一次，研究流程按项目单独启用。

这是 Codex 当前的安装边界：marketplace 可以来自项目或 Git 仓库，但已安装 bundle 缓存在主机用户目录；项目隔离由 `.research/state.json` 的显式启用实现。参见 [Build plugins](https://developers.openai.com/codex/plugins/build)。

从 GitHub marketplace 安装：

```bash
codex plugin marketplace add Fusica/Scientific-Research-Skill
codex plugin add scientific-research-skill@scientific-research-skill
```

本地开发安装：

```bash
git clone https://github.com/Fusica/Scientific-Research-Skill.git
cd Scientific-Research-Skill
codex plugin marketplace add "$PWD"
codex plugin add scientific-research-skill@scientific-research-skill
```

首次安装或 Hook 内容变化后，在 Codex 中检查并信任 Hook，然后新建 thread。文件存在并不代表 Hook 已经被信任或运行。

## 项目启用

在待研究项目根目录执行：

```bash
python3 /path/to/Scientific-Research-Skill/scripts/researchctl.py init
```

也可以在 Codex 中明确调用：

```text
Use $research to initialize this repository and report the current research stage.
```

初始化创建：

```text
.research/
├── state.json   # 阶段、Gate、artifact 指针和检查点
└── memory.md    # 研究内核、事实、决策、失败经验和下一步
```

`.research/` 默认写入当前 clone 的 `.git/info/exclude`，因此位于项目目录中但不提交 Git、不跨服务器同步。`init` 是幂等的，不覆盖已有 memory。

不存在 `.research/state.json`、状态无法解析或 `enabled` 为 `false` 时，公共 Hook 严格输出 `{}`，普通项目不受影响。

## `researchctl`

下面用 `researchctl` 简写 `python3 <plugin-root>/scripts/researchctl.py`：

```bash
researchctl init
researchctl status
researchctl status --json
researchctl enable
researchctl disable
researchctl gate approve idea_freeze --reason "Closest-work and feasibility review completed"
researchctl gate reopen claim_freeze --reason "New evaluation invalidated the frozen wording"
researchctl checkpoint --summary "Baseline reproduced; preparing proposed-method runs"
researchctl checkpoint --summary "Begin registered literature search" --stage literature
researchctl doctor
```

Gate 只能通过该命令更新。每条 decision 记录 action、前后状态、理由和 UTC 时间；批准后是否推进阶段由 `policy.yaml` 的 `advance_to` 决定，命令本身不维护第二套流程知识。非 Gate 阶段切换使用 `checkpoint --stage`，同样必须符合 `allowed_transitions` 及其 Gate 前置条件。

批准或重开 Gate 时，`researchctl` 会把 `state.json` 中当时已登记的 artifact 指针复制进 decision，避免后续指针变化抹掉批准依据。`release` 第一次在 `paper` 阶段批准时记录 `initial_submission` 并进入 `revision`；修改已批准的稿件前必须先重开 `release`，重开后仍停留在 `revision`；再次批准时记录 `revision_rebuttal`。

`doctor` 校验 schema/workflow 版本、阶段、Gate、artifact 路径和本地排除设置。若发现旧 `.research/project-state.yaml`，只做保守迁移：保留旧文件，不根据旧文本或模型判断伪造批准。

## Hook 约束

| 事件               | 行为                                                                                     |
| ------------------ | ---------------------------------------------------------------------------------------- |
| `SessionStart`     | 注入有界的 project ID、阶段、Gate、artifact 指针和 memory 摘要                           |
| `UserPromptSubmit` | 注入当前阶段允许行为、证据要求与退出条件                                                 |
| `PreToolUse`       | 对支持的工具入口拦截危险命令、直接写 Gate state 和可机械判断的越界                       |
| `PostToolUse`      | 在状态被触及时复核 schema 和 artifact 指针，并反馈错误                                   |
| `Stop`             | 首次停止前要求当前主模型检查 Claim—Evidence、overclaim、承诺验证和退出条件；最多触发一次 |

机械边界可以硬阻止；novelty、实验充分性和论证质量仍属于模型辅助判断。Hook 不声称覆盖所有 shell 绕行、外部程序或科研错误。

## 更新与多服务器使用

- 每台服务器各自安装 Plugin，并各自在需要的项目中初始化 `.research/`。
- Plugin 代码与规则通过 GitHub marketplace 更新；Hook 变化后重新检查信任并新建 thread。
- 项目 memory 不同步。若同一 Git 项目在另一台服务器使用，应在该 clone 中重新 `init`，再由研究者决定写入哪些本地事实和 Gate。
- 更新后先运行 `researchctl doctor`；`state.json` 中的 workflow version 用于发现不兼容状态。

## 仓库开发与验证

```bash
python3 -m pip install -r requirements-dev.txt
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
node --test tests/hooks.test.js
```

验收不止检查文件：还要确认 Plugin 已安装/启用、Hook 已信任，并在一个新 thread 中完成项目初始化、上下文恢复和一次 Gate 流转。

## 外部参考与许可证

本地组合层采用 Apache-2.0。设计过程中参考过以下公开项目：

- [Claude Scholar](https://github.com/Galaxy-Dawn/claude-scholar)
- [EvoSkills](https://github.com/EvoScientist/EvoSkills)
- [Nature Skills](https://github.com/Yuan1z0825/nature-skills)
- [agent-research-skills](https://github.com/lingzhi227/agent-research-skills)

这些仓库仅作为外部设计参考；本仓库不保存、安装或重新分发其源码。各项目的许可证与使用边界以上游仓库为准。
