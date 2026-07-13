# Scientific Research Skill

面向 Codex 的可审计科研工作流基础库。第一轮整合以 **Claude Scholar 的流程/证据骨架** 为主，吸收 **EvoScientist 官方 Skill 包 EvoSkills 的 idea–experiment 迭代**、**Nature Skills 的统计/写作/审稿回复**，并对 **agent-research-skills 的形式化与追溯思想进行 clean-room 重构**。

> 当前状态：第一轮基础组织（Alpha）。流程已可安装和调用，但 RL、LLM、无人机控制、个人实验习惯和目标 venue 尚未进行第二轮个性化定制。

## 目标

这个仓库不是把四个上游仓库全部安装到一起，而是建立一个稳定的个人母库：

- 每个科研阶段有明确入口、输入、输出和退出条件；
- idea、文献、方法、实验、结果、论文和返修能通过统一 artifacts 交接；
- 关键结论可向后追溯到论文原文、实验 run、分析代码和具体修改；
- agent 可以执行和迭代，但 idea freeze、实验方案、claim freeze 和外部提交由研究者审批；
- 上游快照与本地组合层分离，便于后续同步和高度定制。

## 总流程

```mermaid
flowchart LR
    A[Research intake] --> B[Idea evolution]
    B <--> C[Literature evidence]
    C --> G1{{Gate 1: idea freeze}}
    G1 --> D[Method formalization]
    D --> G2{{Gate 2: method and experiment approval}}
    G2 --> E[Experiment lifecycle]
    E --> F[Result synthesis]
    F -->|evidence gap| E
    F -->|assumption fails| D
    F --> G3{{Gate 3: claim freeze}}
    G3 --> H[Paper production]
    H --> G4I{{Gate 4: initial release}}
    H --> I[Review revision]
    I -->|new evidence needed| C
    I -->|new experiment needed| E
    I -->|method issue| D
    I --> G4R{{Gate 4: revision release}}
```

它是带反馈边的 state machine，不是强制一次走完的瀑布流程。

## 八个组合 Skill

| Skill | 作用 | 主要产物 |
| --- | --- | --- |
| `$research-orchestrator` | 判断当前阶段、检查依赖、组织 agents 和 gate | `project-state.yaml` |
| `$idea-evolution` | 生成、反证、比较、优化并冻结 idea | `idea_card.yaml` |
| `$literature-evidence` | 搜索、筛选、精读、closest-work 与 novelty 证据 | `search_protocol.yaml`, `evidence_matrix.jsonl` |
| `$method-formalization` | 假设、数学、算法、接口与 math↔code 映射 | `method_contract.md` |
| `$experiment-lifecycle` | 实验矩阵、执行登记、故障诊断与迭代 | `experiment_matrix.yaml`, `run_registry.jsonl`, `decision_log.yaml` |
| `$result-synthesis` | 统计、图表、负结果与 claim promotion | `analysis_registry.yaml`, `artifact_manifest.yaml`, `claim_ledger.yaml` |
| `$paper-production` | 从冻结 claims 组装、编译和审计论文 | `paper_claim_map.yaml`, `paper_change_map.yaml` |
| `$review-revision` | reviewer concern→证据→修改→回复闭环 | `review_map.yaml`, `revision_change_log.yaml` |

每个目录都符合 Codex Skill 结构：`SKILL.md`、`agents/openai.yaml` 和按需加载的 `references/`。

## 统一 artifact chain

默认在研究项目中使用 `.research/`：

```text
.research/project-state.yaml                 # 唯一 gate authority
    ↓
.research/idea/idea_card.yaml
    ↓
.research/literature/{search_protocol.yaml,paper_registry.jsonl,
                      evidence_matrix.jsonl,closest_work.md}
    ↓
.research/method/method_contract.md
    ↓
.research/experiments/{experiment_matrix.yaml,run_registry.jsonl,
                       decision_log.yaml}
    ↓
.research/results/{analysis_registry.yaml,artifact_manifest.yaml,
                    claim_ledger.yaml}
    ↓
.research/paper/{paper_claim_map.yaml,paper_change_map.yaml}
    ↓
.research/revision/{review_map.yaml,revision_change_log.yaml}
```

机器可读的唯一路径目录是 [contracts/artifact-catalog.yaml](contracts/artifact-catalog.yaml)，模板位于 [contracts](contracts/)。若现有项目已有等价文件，不要求重复创建，只需在 `project-state.yaml` 中建立映射。

`project-state.yaml` 是 gate 状态的唯一事实来源。批准/重开记录必须绑定 artifact ID、version 和 content hash；idea、method、experiment 或 claim 文件只能保存 `gate_ref`，不能自行声明已获批准。Gate 4 分别支持初次投稿和 revision/rebuttal release，不要求初次投稿先经过 review 阶段。

`Planning with Files` 没有被禁用：长任务或多 agent 任务可以使用 `.planning/<task>/` 保存执行状态；`.research/` 专门保存可审计的科学事实与决策，两者职责不同。

## 四个上游的职责

| 上游 | 第一轮角色 | 引入方式 |
| --- | --- | --- |
| [Galaxy-Dawn/claude-scholar](https://github.com/Galaxy-Dawn/claude-scholar) | 工作流、research contract、结果报告、ML 写作、publication chart | 固定 Codex 分支快照，MIT |
| [EvoScientist/EvoScientist](https://github.com/EvoScientist/EvoScientist) + [官方 EvoSkills](https://github.com/EvoScientist/EvoSkills) | 前者提供 runtime 设计参考；科研 Skill 实体来自后者，包括 idea 迭代、paper planning、实验 pipeline、失败诊断和经验记忆 | core 不复制；EvoSkills 固定快照，Apache-2.0 |
| [Yuan1z0825/nature-skills](https://github.com/Yuan1z0825/nature-skills) | 统计、Nature 风格写作、reviewer response、共享契约 | 固定快照，Apache-2.0 |
| [lingzhi227/agent-research-skills](https://github.com/lingzhi227/agent-research-skills) | atomic decomposition、math/code mapping、traceability 等设计启发 | 当前快照未发现仓库许可证，不复制源码，仅 clean-room 重构 |

精确 commit、选择边界和 caveats 见 [upstreams.lock.yaml](upstreams.lock.yaml)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与各 `vendor/*/UPSTREAM.md`。

Vendored 内容保持原样，默认不会被安装或直接路由。真正供 Codex 使用的组合 Skill 位于 `skills/`。

EvoScientist 主仓是 agent runtime，而不是完整科研 Skill 包；因此本轮没有搬运其 LangGraph/TUI/WebUI 等产品代码，只记录架构出处，并从其官方 companion repository EvoSkills 选择科研流程。

## 快速开始

```bash
git clone https://github.com/Fusica/Scientific-Research-Skill.git
cd Scientific-Research-Skill

python3 -m pip install -r requirements-dev.txt
python3 scripts/validate_repo.py
python3 scripts/install_codex.py --mode link
```

默认安装到 `$CODEX_HOME/skills`，未设置时使用 `~/.codex/skills`。链接模式便于在仓库中修改后立即生效；若需要独立副本：

```bash
python3 scripts/install_codex.py --mode copy
```

安装器不会覆盖已有同名 Skill，除非显式传入 `--force`；覆盖前会创建带时间戳的备份。

## 使用方式

完整项目从 orchestrator 开始：

```text
Use $research-orchestrator to inspect this repository, initialize the
research state, and tell me the next evidence-backed stage.
```

也可以直接调用单一阶段：

```text
Use $idea-evolution to challenge and refine this UAV-control research idea.
Use $review-revision to map these comments to evidence and manuscript edits.
```

对小型、单次任务不必强制初始化全流程；使用最小适用 Skill 即可。

## 目录

```text
skills/       # 本地组合层；Codex 默认安装这些
contracts/    # 阶段交接模板
profiles/     # 领域、venue、agent 策略；第一轮仅提供基线
vendor/       # 经过许可审查的只读上游快照
docs/         # 架构、执行流程和下一轮定制路线
scripts/      # 安装与仓库校验
tests/        # 结构和行为约束测试
```

## 第一轮明确不做的事

- 不声称仅靠 Skill 即可保证顶刊论文质量；
- 不使用 citation count 判断论文贡献类型或 novelty；
- 不把 LLM 自评/Elo 当作 idea 的最终科学裁决；
- 不套用上游通用的 seed、方差、提升幅度或 attempt budget；
- 不复制缺少明确许可证的上游源码；
- 不再分发 Claude Scholar 中含独立条款的 venue/LaTeX 模板，使用时从官方 venue 获取；
- 不引入体积较大的 Nature figure assets；
- 不在尚未确定个人工具链前绑定 W&B/MLflow、Slurm、ROS2/PX4 或特定 simulator；
- 不在第一轮固化 RL、LLM、UAV 和 venue 的细粒度协议。

## 下一轮定制

下一轮建议围绕真实科研实践讨论并冻结：

1. RL、LLM、UAV/control 三类 domain profiles；
2. 常用仿真、实机、训练、调参和实验追踪工具；
3. idea 评审维度、kill criteria 和个人经验沉淀规则；
4. NeurIPS/ICML/ICLR、ICRA/IROS/RSS/CoRL、RA-L/T-RO 等 venue profiles；
5. 论文数字反向溯源、自动编译/视觉 QA、rebuttal 一致性检查；
6. 项目级 memory 与跨项目经验库的边界。

详见 [docs/customization-roadmap.md](docs/customization-roadmap.md)。

## License

本地组合层使用 Apache License 2.0。Vendored 内容继续遵循各自上游许可证；具体归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
