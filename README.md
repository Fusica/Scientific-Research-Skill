<p align="center">
  <img src=".github/assets/logo.svg" width="148" alt="Scientific Research Skill logo">
</p>

<h1 align="center">Scientific Research Skill</h1>

<p align="center">
  <strong>让研究判断、证据、实验与写作之间的关系清楚、可恢复、可审计。</strong>
</p>

<p align="center">
  <a href="#安装">安装</a> ·
  <a href="#六阶段与四个-gate">科研流程</a> ·
  <a href="#参与使用与共建">参与共建</a>
</p>

一个面向 Codex 的项目级科研 Plugin：用一个 `$research` 入口贯通 idea、文献、方法、实验与结果、论文、返修六个阶段，同时用确定性命令维护 Gate，用 Hook 约束可机械判断的越界行为。

仓库不承诺自动产出“顶刊论文”，而是让研究判断、证据、实验和写作之间的关系清楚、可恢复、可审计。

## 核心结构

```text
Codex Plugin
├── Skill: $research                 唯一对话入口与阶段路由
├── Rules: references/policy.yaml    唯一流程、Gate 和退出条件
├── Command: scripts/researchctl.py  唯一状态与 Gate 写入口
├── Hooks                            项目上下文、工具边界、停止前复核
└── Project data: .research/         当前项目的本地状态、记忆与工作流产物
```

`scripts/researchctl.py` 保持为唯一公开命令与写入口；实现层位于私有 `scripts/researchctl_core/`，按共享 schema/常量、policy、state store/validation、migration、artifact registry、Gate engine/validation、workspace checks、doctor aggregation、command orchestration 和 CLI 分模块。依赖方向单向汇入 doctor、commands 与 CLI，不增加第二套 state、schema 或写入口。

## 六阶段与四个 Gate

<p align="center">
  <img src=".github/assets/research-workflow.svg" width="100%" alt="Scientific Research Skill 的六阶段科研流程与四个人工批准 Gate">
</p>

上图只表示主路径与关键边界；回退、Gate 重开和完整转换条件以 [`skills/research/references/policy.yaml`](skills/research/references/policy.yaml) 为准。

每个 Gate 都要求研究者明确批准或重开。模型不能把沉默、任务完成或一段积极表述解释成批准。

`release` 是同一个 Gate 的两个发布目标：首次批准 `initial_submission` 后进入 Revision；返修完成后，还要针对 `revision_rebuttal` 再次明确批准。Gate 批准只记录决策与绑定证据，不会代替研究者执行投稿或发布。

| 阶段                 | 主要职责                                                           |
| -------------------- | ------------------------------------------------------------------ |
| Idea                 | 生成候选、反证、可行性判断、预测与 kill criteria                   |
| Literature           | 背景搜索、closest work、证据矩阵、novelty 边界与 idea 迭代         |
| Method               | 假设、数学定义、模块接口、算法与可检验预测                         |
| Experiment + Results | 基线、实验矩阵、执行记录、失败诊断、统计分析与 Claim—Evidence 对齐 |
| Paper                | 结构、写作、数字与引用追溯、自审、编译和投稿检查                   |
| Revision             | reviewer concern、补充证据、论文修改与逐点回复闭环                 |

文献阶段可以调用项目可用的学术检索系统，但以 provider-neutral 的 search-run manifest、允许保留时的原始结果 Hash（否则记录不可保留原因）、筛选和 passage-level evidence 为审计边界。计算实验优先兼容 W&B：W&B 负责实时曲线、sweep 和协作查看，本地 `run_registry` 与导出 Hash 才是 Gate 可依赖的权威记录；没有 W&B 时可使用现有 tracker 或纯本地记录。论文阶段显式声明 LaTeX/BibTeX 或 Biber 构建链，执行 clean build、保留日志并检查渲染 PDF，不把某个模板、引擎或目录结构写死。

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
├── state.json      # 阶段、Gate、artifact 指针和检查点
├── memory.md       # 研究内核、事实、决策、失败经验和下一步
└── artifacts/      # Plugin 新建的工作流产物
    └── <stage-id>/ # 按需创建的阶段目录
```

`.research/` 默认写入当前 clone 的 `.git/info/exclude`，因此其中的状态、记忆和新建工作流产物都不提交 Git、也不跨服务器同步。`init` 是幂等的，不覆盖已有 state 或 memory；若现有 state 已禁用，使用 `researchctl enable` 重新启用。已有源码、论文、数据和运行输出保留在原位置，通过 artifact 指针登记，不为了满足布局而复制。升级时也不会自动迁移旧 `research/`、`contracts/` 或 `artifacts/` 中已经登记的产物，以免破坏路径、Hash 和 Gate 历史；只有后续新建的工作流产物使用新布局。

`.research` 中需要人工审核的中间产物、持久化 memory、checkpoint summary 和 Gate reason 默认采用中文骨干。论文、返修回复、代码及注释等正式输出保持英文；JSON/YAML 字段、ID、枚举、路径、命令、公式、原始引文、书目信息和原始日志保持英文或原文。该规则只影响后续新写内容，不翻译或重写已有 artifact。

不存在 `.research/state.json`、状态无法解析或 `enabled` 为 `false` 时，公共 Hook 严格输出 `{}`，普通项目不受影响。

## `researchctl`

下面用 `researchctl` 简写 `python3 <plugin-root>/scripts/researchctl.py`：

```bash
researchctl init
researchctl status
researchctl status --json
researchctl enable
researchctl disable
researchctl artifact register idea_card \
  --stage idea --path .research/artifacts/idea/idea-card-v1.yaml \
  --artifact-id IDEA-CARD-001 --version 1 --status approval-ready
researchctl gate approve idea_freeze --reason "已核对最近工作、可行性与否证条件"
researchctl gate reopen claim_freeze --reason "新评估结果使冻结表述失效"
researchctl checkpoint --summary "基线已复现，下一步运行所提方法"
researchctl checkpoint --summary "开始执行已登记的文献检索" --stage literature
researchctl doctor
```

canonical artifact 通过 `artifact register` 登记到对应的 `stage.role`；其中 stage 传给 `--stage`，role 作为位置参数。Plugin 新建的工作流产物按 `policy.yaml` 统一放在 `.research/artifacts/<stage-id>/`，不再创建项目根目录的 `research/`、`contracts/` 或 `artifacts/`。命令自动计算文件 SHA-256，并保留 ID、版本和描述性状态；`--status` 默认是 `current`，不表示 Gate 已批准。`.research/state.json`、`.research/memory.md` 等控制元数据不能登记为科研证据。

所有修改 state 的命令都通过 `.research/state.lock` 串行化完整的读取、校验和原子替换事务，避免并行 agent 静默覆盖彼此的更新。该 lock 也是控制元数据，不能登记为证据或直接修改。

`policy.yaml` 中的 Gate role 覆盖各阶段 reference 明列的 canonical 交付物；同一现有文件可以映射到多个 role，不要求复制。原始 run、analysis 和大体量输出可由已登记的 registry 或 manifest 间接追溯，但其中必须记录稳定 ID、路径和 checksum；`researchctl` 只验证 registry/manifest 文件本身，引用文件的 checksum 仍由对应阶段实际核验。登记不复制或备份文件，已批准版本必须保留在稳定的版本化路径。

Gate 只能通过该命令更新。每条 decision 记录 action、前后状态、理由和 UTC 时间；批准前，`researchctl` 按 `policy.yaml` 验证必需 artifact role 的文件与 hash，并把这些指针复制进 decision，避免后续 state 变化抹掉批准依据。批准后是否推进阶段由 policy 的 `advance_to` 决定；非 Gate 阶段切换使用 `checkpoint --stage`，同样必须符合 `allowed_transitions` 及其 Gate 前置条件。

`release` 第一次在 `paper` 阶段批准时记录 `initial_submission` 并进入 `revision`；修改已批准的稿件前必须先重开 `release`，重开后仍停留在 `revision`；再次批准时记录 `revision_rebuttal`。已被批准的 artifact role 在 Gate 重开前不能替换；已批准路径不能用不同内容原地复用，同一 `artifact_id@version` 也不能重新绑定到另一组路径、Hash 或元数据。

`doctor` 校验 schema/workflow 版本、UTC 时间与历史连续性、阶段、Gate 状态机、当前 artifact、已批准 Gate 与其当前 artifact 绑定、历史 Gate 引用的路径和 SHA-256，以及本地排除设置。当前 Gate 所需文件缺失或 hash 失配会阻止批准；历史批准文件遗失或改变会持续给出 audit warning，但在明确重开 Gate、以新路径登记新版本后不会永久阻断后续批准。旧式裸路径继续作为兼容输入并给出 warning，但不能满足新的 Gate artifact 要求。若发现旧 `.research/project-state.yaml`，只做保守迁移：保留旧文件，不根据旧文本或模型判断伪造批准。

## Hook 约束

| 事件               | 行为                                                                                     |
| ------------------ | ---------------------------------------------------------------------------------------- |
| `SessionStart`     | 只注入项目已启用、当前阶段和 state 权威等最小边界                                       |
| `UserPromptSubmit` | 按 prompt 分流；明确的代码重构、调试或代码解释不注入研究阶段合同                         |
| `PreToolUse`       | 对支持的工具入口拦截危险命令、直接写 Gate state 和可机械判断的越界                       |
| `PostToolUse`      | 在状态被触及时执行快速结构检查；完整路径与 Hash 审计仍以 `researchctl doctor` 为准          |
| `Stop`             | 仅当回答包含科研结论、结果、交付物或 Gate 声明时请求一次精简语义复核；保留原回答，只追加审查结论 |

Prompt 分流只减少上下文和不必要的 Stop continuation，不是权限旁路；`PreToolUse` 的机械边界始终生效。novelty、实验充分性和论证质量仍属于模型辅助判断，Hook 不声称覆盖所有 shell 绕行、外部程序或科研错误。

## 更新与多服务器使用

- 每台服务器各自安装 Plugin，并各自在需要的项目中初始化 `.research/`。
- Plugin 代码与规则通过 GitHub marketplace 分发；各主机刷新 marketplace 并重新安装新版本后，再新建 thread。若 Hook 发生变化，还要重新检查信任。
- 项目 memory 与 `.research/artifacts/` 不同步。若同一 Git 项目在另一台服务器使用，应在该 clone 中重新 `init`，再由研究者决定迁移哪些本地事实、工作流产物和 Gate。
- Plugin package version 用于分发和缓存；`state.json` 中的 workflow version 只在 policy 或状态契约不兼容时变化。更新后先运行 `researchctl doctor`；若旧工作区提示缺少 `.research/artifacts/`，重新执行幂等的 `researchctl init` 补齐目录后再检查。

## 仓库开发与验证

```bash
python3 -m pip install -r requirements-dev.txt
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
node --test tests/hooks.test.js
```

验收不止检查文件：还要确认 Plugin 已安装/启用、Hook 已信任，并在一个新 thread 中完成项目初始化、上下文恢复和一次 Gate 流转。

## 参与使用与共建

如果你正在做 CS、ML、强化学习、机器人或无人机方向的长期研究，欢迎把它放进真实项目试用。遇到流程边界、状态恢复、证据追溯或 Hook 行为问题，可以[提交 Issue](https://github.com/Fusica/Scientific-Research-Skill/issues)；对规则、文档或实现有明确改进，也欢迎提交 Pull Request。

反馈时建议附上当前阶段、相关命令、最小复现和预期行为，但不要上传未公开论文、私有数据或其他敏感材料。如果这个项目对你有帮助，也欢迎 Star，让更多研究者看到并一起验证这套边界是否真的实用。

## 外部参考与许可证

本地组合层采用 Apache-2.0。设计过程中参考过以下公开项目：

- [Claude Scholar](https://github.com/Galaxy-Dawn/claude-scholar)
- [EvoSkills](https://github.com/EvoScientist/EvoSkills)
- [Nature Skills](https://github.com/Yuan1z0825/nature-skills)
- [agent-research-skills](https://github.com/lingzhi227/agent-research-skills)

这些仓库仅作为外部设计参考；本仓库不保存、安装或重新分发其源码。各项目的许可证与使用边界以上游仓库为准。
