---
name: hands-on-teaching-development
description: 手把手教学开发。AI 研究多个 Example Project，在 Agent Project 中可一次完成整个功能并提交多个小 Commit；用户随后按教学队列逐 Commit 在 User Project 手写/Review 或直接接受。使用本地进度索引、需求偏好纠偏日志支持跨会话恢复。触发：教学模式、teach 模式、手把手教学、继续教学；退出：退出教学模式、cancel teach。
---

# Hands-on Teaching Development

本 Skill 管理一个最终目标产品的教学开发过程。它不是把某个示例仓库完整复制出来，而是从一个或多个 Example Project 中选择性吸收有价值的实现，最终形成用户自己的 Agent Project。

## 1. 三种项目角色（不可混淆）

| 名称 | 中文 | 权限与职责 |
|---|---|---|
| Example Project | 示例项目 | 一个或多个学习资料来源。AI 严格只读，分析、比较、提取思路和代码。 |
| User Project | 用户项目 | 用户练习和手写实现的项目。初始化阶段允许 AI 建骨架；初始化完成后，未经用户明确授权不得修改。默认用于 Review。 |
| Agent Project | Agent 项目 | 用户最终想要实现的项目/产品。AI 在这里写参考实现、中文注释、文档并维护独立 Git 历史。 |

核心原则：

1. 一个 Agent Project 可以关联多个 Example Project。
2. 每个 Example Project 可以贡献一个局部功能、设计思想，也可以贡献大部分实现；不要求完整吸收。
3. AI 必须独立判断 Example Project 的优缺点，不机械照搬。
4. 若更优方案会改变已确认的产品方向、架构边界或重要取舍，先向用户解释并征求意见。
5. User Project 初始化完成后，默认禁止 AI 写入；Review、读取、构建和测试不属于修改业务代码。不得因用户尚未写完或存在差异而自行代写、补齐或静默修改 User Project。
6. Agent Project 每个教学步骤仍对应一个小粒度 Commit，但允许一次连续开发一个模块/大功能、提前准备多个 `[agent]` Commit；用户按队列逐 Commit 学习。
7. 不提交 User Project，不修改 Example Project。
8. **严格遵守交互停顿边界**：向用户汇报步骤、指出差异与推荐方案、或询问选型/确认后，**必须立即结束本轮并等待用户下一轮明确回复**。严禁在同一轮内替用户做出 `decide --choice user|agent` 决策、代跑 `apply-agent` 或代写 User Project 代码。

## 2. 触发与退出

- 进入：`教学模式`、`teach 模式`、`手把手教学`。
- 恢复：`继续教学`。
- 退出：`退出教学模式`、`cancel teach`。
- 不把“我自己写，你别管”设置为特殊退出触发词。
- 退出只停止教学循环，不删除文档、Git 历史、Review 快照或工作区备份。

## 3. 工作区布局

以用户当前指定的 workspace 为根目录；默认使用当前工作目录。标准结构：

```text
workspace/
├── AGENTS.md
├── examples/
│   ├── example-a/                 # 实体目录
│   └── example-b -> /path/to/b    # 符号链接
├── user-project -> /path/to/user  # User Project 符号链接
├── .teaching/
│   ├── review/
│   │   ├── latest.json
│   │   └── latest/
│   │       ├── agent.patch
│   │       ├── user.patch
│   │       └── paths.json
│   └── backup/
│       └── current/               # 只保留一份最新工作区备份
└── <agent-project-name>/          # Agent Project 实体目录
    ├── .git/
    └── docs/
        ├── development-plan.md
        ├── development-log.md
        ├── teaching-state.md     # 机器可读的双游标状态索引
        ├── NOTES.md             # 长期有效的用户约定与纠偏
        ├── MISSION.md           # 最终目标、学习动机与成功标准
        └── RESOURCES.md         # 示例项目可参考的位置与版本
```

`examples/` 是普通目录，允许实体项目和符号链接共存。User Project 通过 `workspace/user-project` 符号链接访问。Agent Project 位于 workspace 根目录，目录名由用户确认。

## 4. 首次进入：项目发现与 workspace 初始化

### 4.1 先确认路径

1. 检查当前 workspace 是否已有 `examples/`、`user-project` 链接或 Agent Project。
2. 如果已有 `examples/` 内容，询问用户确认哪些是本次 Example Project；不要把普通目录自动认定为示例项目。
3. 如果没有示例项目，询问用户提供一个或多个 Example Project 路径。
4. 必须由用户明确提供 User Project 实际路径。
5. 用户确认 Agent Project 的名称；名称以最终产品/项目命名，不按 Example Project 名称自动命名。
6. 不覆盖已存在的目录、文件、符号链接或项目仓库。发生命名冲突时停下请用户决定。

### 4.2 导入 Example Project

运行：

```bash
npm --prefix <skill-dir> run setup -- workspace \
  --workspace <workspace> \
  --example /path/to/example-a \
  --example example-b=/path/to/example-b \
  --user-project /path/to/user-project
```

通过重复 `--example` 传入多个示例项目。可用 `名称=路径` 指定 workspace 内的名称。

规则：

- 已位于 `workspace/examples/`：保持原样。
- 位于 workspace 内、但不在 `examples/`：移动到 `examples/<名称>/`，保留其 `.git` 和历史。
- 位于 workspace 外：在 `examples/<名称>` 创建符号链接。
- 目标名称冲突：停止，不覆盖、不自动改名。
- Example Project 必须是可访问目录。

### 4.3 创建 User Project 符号链接

- 在 `workspace/user-project` 创建指向用户明确路径的符号链接。
- 创建链接不以目标存在为前提；允许先建立悬空链接。
- 如果链接位置已有不同目标的链接或普通文件/目录，停止并请用户处理。
- User Project 实际路径不得与任何 Example Project 路径重叠；脚本检测到重叠会停止。
- 脚本会创建目标目录（若不存在），以便 AI 初始化工程。

### 4.4 复制 workspace AGENTS.md

Skill 包内 `references/AGENTS.md` 是 workspace 级约束。首次启动时由 `setup.ts workspace` 复制到 `workspace/AGENTS.md`。

- 目标不存在：复制。
- 内容相同：跳过。
- 已存在但内容不同：绝不覆盖；先读取已有规则，向用户说明冲突，再由用户决定是否合并。

### 4.5 初始化 User Project 工程骨架

先读取所有 Example Project 的 README、工程配置、构建脚本、核心源码和相关文档，判断目标产品需要的语言、框架、构建工具和运行环境。

User Project 状态处理：

- 不存在或空目录：等同于不存在。
- 已有部分实现：保留现有内容，先分析，不擅自重建。
- 用户指定模板：优先使用指定模板。
- 未指定模板：基于目标产品和 Example Project 准备最小工程骨架，补齐依赖和编译环境；允许加入最小运行实现。
- AI 可以提出比 Example Project 更好的工程组织或技术方案；重要方向变化先征求用户意见。

初始化阶段是 AI 写入 User Project 的明确例外。初始化完成并建立 Git 基线后，User Project 进入默认禁止修改状态。

### 4.6 User Project Git 基线

User Project 必须是独立 Git 仓库：

- 已有独立仓库：保留原历史、分支和配置。
- 没有仓库：执行 `git init`，不创建 Commit。
- 不处理嵌套在其他 Git 仓库中的情况；脚本发现 Git 根目录不等于项目根目录时停止。
- 初始化骨架完成后，必须执行一次 `git add -A` 建立教学暂存区基线；不创建 Commit。
- 这一步可能把原有未提交修改加入暂存区，属于用户已确认的初始化行为。

命令：

```bash
npm --prefix <skill-dir> run setup -- user-baseline --workspace <workspace>
```

### 4.7 创建 Agent Project

Agent Project 名称由用户确认。先从 User Project 当前工作树复制，不复制 `.git`，再创建独立 Git 仓库。

复制规则由 `copy-project.ts` 实现：

- 排除任意层级的 `.git` 目录或 `.git` 文件。
- 使用 `git check-ignore --no-index` 按 Git ignore 规则过滤；为简化实现，不查询 Git Index，因此已跟踪但匹配 ignore 规则的文件也排除。
- 不额外维护固定排除清单。
- 除 `.git` 和 ignore 匹配项外，其他内容正常复制。
- 内部符号链接只复制链接本身，不跟随目标。
- User Project 根路径是符号链接时，复制其实际目录内容。

创建全新 Git 仓库并提交：

```text
[agent] initial(init): 初始化项目基础结构
```

Git `user.name` / `user.email` 缺失时停止并提示用户自行配置；不得修改全局 Git 配置。

命令：

```bash
npm --prefix <skill-dir> run setup -- agent-init --workspace <workspace> --agent-name <用户确认的名称>
```

## 5. Agent Project 必须维护的文档

### `docs/development-plan.md` — 整体开发计划

记录产品目标、功能范围、Example Project 来源、技术方案、模块依赖、开发顺序、步骤清单和验收标准。长期维护。

每次开始新模块/功能时，先问用户本次优先级。用户可以调整教学执行顺序，但不因此擅自改变产品目标或删除整体计划内容；记录提前/推迟的步骤和原因。

### `docs/development-log.md` — 逐步开发记录

按实际发生顺序记录每一步：

- Agent 实现内容、修改文件、关键代码位置。
- 参考了哪个 Example Project 的哪些机制。
- 中文教学注释和实现原理。
- 用户实现方式（手写、完全一致、存在差异、直接接受 Agent 实现、半成品接管）。
- Diff 结果、Review 范围、发现的问题和结论。
- 用户实现吸收了哪些代码、保留了哪些用户改进。
- 构建/测试结果和对应 Commit。

`DIFFERENT` 时还应记录 Agent/User 两方案的优劣、Agent 推荐与理由、用户实现带来的新启发（如有）、最终选择；原则上只追加，不随意重写已完成步骤。

### `docs/MISSION.md` — 开发与学习的共同目标

保持一屏以内，明确为什么要开发、最终用户想独立掌握什么、成功标准和不做的范围；方向变化必须先向用户确认，不复制 `development-plan.md` 的详细需求表。

### `docs/RESOURCES.md` — Example Project 资料索引

记录有价值的示例项目、版本或 Git SHA、具体文件/方法定位、采用和不采用的理由；只读示例项目，不复制完整源码或整个文件树。

### `docs/NOTES.md` — 长期有效约定

由 Agent 直接以 Markdown 维护，格式可按项目需要自由组织（例如按全局/模块分组或条目列表）。只存用户已确认的长期需求、偏好、决策和纠偏；遇到新旧矛盾时明确废止或移出旧约定，避免冲突规则同时生效。一次 Review 的选择、Agent 未确认的建议和短期教学反馈不得直接提升为长期规则。

### `docs/teaching-state.md` — 当前状态

JSON 区块由 `progress.ts` 维护（Agent/User 双游标、当前批次与待学队列），下方自由 Markdown 区块可由 Agent 按需记录短期教学提示。Review 快照与备份保存在 workspace 的 `.teaching/` 下；`progress.ts status` 汇总这些状态供跨会话恢复，不替代计划和日志。

## 6. 开发计划、教学批次与步骤粒度

1. 根据最终产品的目标决定实现全部产品还是某个模块；比较 Example Project，明确吸收范围与技术取舍。
2. **开发粒度**与**教学交互粒度**分离：Agent 可以连续开发一个功能/大功能，形成多个小粒度 `[agent]` Commit；不要求写完一个 Commit 就打断思考等待用户。
3. 每个 Commit 尽量是可解释、可验证的小功能，一个步骤可以涉及多个文件；不要为了凑 Commit 过度拆分，也不要把大功能压为单次巨大 Commit。
4. 批次准备完成后，将其中各 `[agent]` Commit（SHA、步骤 ID、标题、涉及文件）登记为有序教学队列。User Project 按队列逐步实现或接受；Agent Project 可以领先多个 Commit。
5. 分别维护 **Agent 已准备进度** 与 **User 已完成进度**；计划中的模块状态也应区分这两个维度。
6. 批次期间如出现用户实现与后续已准备的代码冲突，不擅自覆盖、不重写用户代码；暂停并核对后续步骤，必要时明确作废未教学的旧队列，再基于用户实现重做。

具体状态脚本、准备批次、暂停以及进度恢复命令见 [references/batch-and-progress.md](references/batch-and-progress.md)。Review 评估格式与轻量自适应教学原则见 [references/review-and-teaching.md](references/review-and-teaching.md)。

## 7. Agent 批量实现与教学循环

1. 在开始开发新模块/大功能前先问用户优先级；先核对 `MISSION.md`、`NOTES.md` 与适用的 `RESOURCES.md`，再制定小步骤计划。根据 `teaching-state.md` 中的短期教学提示调整**未准备的**步骤粒度，已提交的教学 Commit 不自动改写。
2. AI 可以在 Agent Project **连续**实现多个小功能：每步加中文教学注释，进行必要的构建/测试，并把代码与当步变更的计划/日志/状态文档一并通过 `checkpoint.ts` 创建 `[agent]` Commit。
3. 在每个小 Commit 中写清楚改动范围和目的；每个 Commit 都可独立供用户查看，避免一次大改造成教学难度。
4. 批次完成后运行 `progress.ts prepare --batch-id <id> --module <模块> --auto` 自动收集未登记的 `[agent]` Commit 并注册教学队列（默认自动跳过已完成的旧批次与 `[user]` 节点之前的提交，也可用 `--from <基线SHA>` / `--to <目标Rev>` 精确限定范围，或用 `--steps-file` 自定义覆盖）；将更新后的 `teaching-state.md` 与计划一并提交为教学文档检查点。
5. 汇报本批次实现的功能、各 Commit 及学习顺序；给出**第一个待学习 Commit** 的文件、关键位置与验收要点，然后停下等待用户。
6. 用户完成当前步骤后按第 8 节 Review、暂存、按需吸收，先运行 `progress.ts complete` 更新双游标，再更新文档并创建 `[user]` Commit。
7. 当前批次未完成时，不得把 Agent HEAD 最新 Commit 当成用户应做的下一步；由 `progress.ts status` 获取教学游标。

不同批次允许重新规划和开发；已准备的多个 Commit 不要求与用户的 `[user]` Commit 在 Git 历史中交替出现。

## 8. 用户手写实现与 Review

### 8.1 用户工作方式

- 用户在 User Project 工作树中手写当前步骤。
- Review 前不要求用户 Commit，也不要求先 `git add`。
- 用户完成后告诉 Agent 进行 Review。

### 8.2 自动 Diff 比较（强制）

必须优先运行 `review-diff.ts review`，不得只靠 Agent 目测或读取 Git Commit 猜测用户改动。

脚本比较三种状态：

1. User Project 当前工作树，包括已跟踪文件未暂存修改、删除和未跟踪文件。
2. User Project Index 与 Agent 当前步骤 Commit 父版本，确认比较基线。
3. Agent Project 当前步骤 `[agent]` Commit 对应文件的最终状态。

脚本按当前步骤明确的文件清单比较文件内容、路径存在状态、符号链接目标和可识别文件模式，不依赖原始 Patch 文本格式完全相同。

运行（激活教学队列时自动读取当前待学 Commit 与文件路径，无需每次手写路径 JSON；单步模式或扩展审查范围时可通过 `--agent-commit`、`--path` 或 `--paths-file` 覆盖）：

```bash
npm --prefix <skill-dir> run review-diff -- review \
  --user-dir <workspace>/user-project \
  --agent-dir <workspace>/<agent-project-name> \
  --workspace <workspace>
```

**批次教学时默认自动锁定队列当前待学 Commit**；传入其他步骤的 SHA 会拒绝跳步。Review 的比较基线为该 Commit 的父版本，不是 Agent 最新提交。

脚本结果：

- `EXACT_MATCH`：用户工作树文件与 Agent 实现一致，且 Index 基线匹配。跳过重复的深入逻辑审查，但仍运行必要构建和测试。
- `DIFFERENT`：Index 基线匹配但代码不同。先审查差异部分，根据风险决定是否扩大到完整逻辑审查。
- `BASELINE_MISMATCH`：User Index 与 Agent Commit 父版本不一致。不得误判为用户实现错误或自动通过；AI 需分析基线差异，必要时咨询用户。

`review` 一次 JSON 输出 `diffs.userVsAgent`（当前用户实现与 **指定** Agent Commit 最终版本的直接差异）、`diffs.userPatch`、`diffs.agentPatch`、`diffs.extraUserPatch`、`different`、`baselineMismatch`、`extraUserPaths` 和 `whitespaceOnlyCandidates`，**不要求 Agent 再读一次补丁文件**。只在超大差异被显式标记 `truncated: true` 时读取保存的完整报告；可以用 `--max-inline-bytes` 调整单项内联限制，最大 2 MiB。`whitespaceOnlyCandidates` 只是提示，Python 缩进、字符串、宏等对空白敏感的实现必须审计，不得据此直接放行。

脚本会在 `workspace/.teaching/review/latest.json` 保存 Review 快照，并生成 `user-vs-agent.diff`、`agent.patch`、`user.patch`、`user-extra.patch` 和文件清单。如果 User Project 还有当前步骤清单之外的未暂存改动/未跟踪文件，返回 `DIFFERENT` 并列出 `extraUserPaths`；AI 判断是否属于本步骤，必要时扩展范围后重新 Review。`review` 只读 User Project，不执行 `git add`。

### 8.3 先审计，再决定保留哪种实现（强制）

- `EXACT_MATCH`：执行必要的构建/测试；通过后可直接进行 8.4 的精确暂存，无需用户再次选择。
- `DIFFERENT`：**先使用本次 `review` 返回的差异内容进行审计**。至少判断差异属于空白/命名/风格、等价的设计替代、功能增减、风险/潜在 Bug 或基线/无关修改。必要时检查上下文和完整实现；不要把不一致直接视为错误。
- 只有风格差异：解释影响（含语言中空白可能改变语义的情况），也让用户选择采用教学 Commit 或保留自己的写法；**不替用户默认做决定**。
- 等价实现或用户有更好的设计：**必须比较两方案优劣、维护/安全/性能取舍、对后续 Commit 的影响，并明确推荐哪一种及理由**；若用户实现有新启发，列为候选建议，不擅自扩展需求。然后**询问用户采用 `agent` 还是 `user`**。用户确认长期取舍时才更新 `docs/NOTES.md`。
- 存在 Bug、未实现、明显风险：提供具体文件位置、原因和建议；**不能把有缺陷的代码标记为审查通过并吸收**。让用户修正后重新 Review，或者明确选择教学 Commit 方案。
- `BASELINE_MISMATCH`：先处理偏移，不运行 `decide` / `stage-reviewed` / `absorb`。

向用户说明审查结论、优劣对比与推荐后，**必须停止本轮并等待用户明确回复**；收到用户的明确选择后，才能在下一轮执行 `decide`（实质差异可附带可选的 `--recommend` 和 `--inspiration`，纯风格差异无需机械填表）：

```bash
npm --prefix <skill-dir> run review-diff -- decide \
  --workspace <workspace> --choice user|agent \
  --reason "审查结论与用户确认的选择" \
  [--recommend agent|user] [--inspiration "用户实现带来的新启发"]
```

`decide` 只在用户明确确认后记录选型，不会自动修改两个项目。Bug 未修复不得建议吸收 User 版本。具体 Review 与用户选择写入 `development-log.md`；只有可复用且用户确认的长期约定才写 `NOTES.md`。详见 [references/review-and-teaching.md](references/review-and-teaching.md)。

### 8.4 精确暂存与快照复核

- `EXACT_MATCH`：构建/测试通过后，可让 Agent 执行 `stage-reviewed`，不再要求用户手动输入 `git add`。
- `DIFFERENT` 且用户确认 **采用自己的实现**：Agent 审计通过、`decide --choice user` 后才能执行 `stage-reviewed`。
- `DIFFERENT` 且用户确认 **采用教学 Commit**：**不要**执行 `stage-reviewed`，而是运行第 9 节 `sync-user.ts apply-agent`；它会先备份用户工作区、按当前 Commit 应用 Patch 并暂存。该操作会替换未暂存半成品，必须在用户确认选择后进行。

```bash
npm --prefix <skill-dir> run review-diff -- stage-reviewed \
  --user-dir <workspace>/user-project --workspace <workspace>

# 仅在用户手工 git add 或跨轮复核暂存区时单独执行：
npm --prefix <skill-dir> run review-diff -- verify-staged \
  --user-dir <workspace>/user-project --workspace <workspace>
```

`stage-reviewed` 在暂存前重新核验 User Index 基线和本次审查的工作区快照；只运行精确路径的 `git add -A -- <paths>`，不会执行 `git add .`，不创建 User Commit，也不覆盖之前已暂存的历史步骤，且在暂存后立即完成与 `verify-staged` 等价的校验（因此 `stage-reviewed` 返回 `STAGED` 后可直接进入 `absorb`，无需重复运行 `verify-staged`）。若用户自行手工 `git add` 或在跨轮后继续，须先运行 `verify-staged` 返回 `VERIFIED`。任何快照失效或构建/测试产生未审查的源码修改，均须重新 Review。

### 8.5 按需吸收到 Agent Project 并完成检查点

- **`EXACT_MATCH`**：`stage-reviewed` 成功后 User 实现已与教学 Commit 完全一致，**无需调用 `sync-user.ts absorb`**。直接执行 `progress.ts complete`（如有激活批次），更新开发日志/计划，用 `checkpoint.ts` 创建 `[user] docs(scope): ...` Commit 即可。
- **`DIFFERENT` 且用户确认 `--choice user`**：在 `stage-reviewed` 成功后、**修改 Agent Project 文档或运行 `progress.ts complete` 之前**（保持 Agent 工作区干净），运行：

```bash
npm --prefix <skill-dir> run sync-user -- absorb \
  --user-dir <workspace>/user-project \
  --agent-dir <workspace>/<agent-project-name> \
  --workspace <workspace>
```

`absorb` 会对 `DIFFERENT` 强制检查 Review 快照中已记录 `choice=user`；未选择时拒绝同步。脚本只吸收与教学 Commit **真正不同** 的用户代码，不允许未经确认吸收，也不修改 User Project。若用户采用教学 Commit，按第 9 节应用后只提交 `[user]` 文档记录，不以“用户改进”名义吸收代码。

吸收完成后，执行 `progress.ts complete` 推进用户游标，更新开发日志、计划与必要的长期笔记，再使用 `checkpoint.ts` 精确提交为 `[user]` Commit。

若该用户步骤与 Agent 已准备的后续 Commit 修改相同文件，`absorb` 应拒绝覆盖；暂停队列，协调未来实现，不得擅自替换后续代码。

## 9. 用户明确要求直接移植 Agent 实现

用户可以不手写，直接要求把当前 Agent 实现同步到 User Project。

### 9.1 正常情况或用户写了一半但未要求保留

默认直接使用脚本应用 Agent 对应 Commit Patch。即使用户写了一半，只要用户没有明确要求保留半成品，也按此默认策略处理。

前置条件：

- User Project Index 对应文件必须与 Agent Commit 父版本一致。
- 若暂存区基线不一致，停止，不清理、不覆盖，先交给 AI 分析。

执行（激活教学队列时自动取当前待学 Commit 与文件路径；无队列时传入 `--agent-commit <sha>`，必要时通过 `--path` / `--paths-file` 覆盖）：

```bash
npm --prefix <skill-dir> run sync-user -- apply-agent \
  --user-dir <workspace>/user-project \
  --agent-dir <workspace>/<agent-project-name> \
  --workspace <workspace>
```

脚本行为：

1. 先验证 User Index 与 Agent Commit 父版本一致。
2. 若存在未暂存修改或未跟踪文件，先备份，再清理未暂存工作区；保留已暂存内容。
3. 通过 `git apply --index` 应用 Agent Patch。
4. 验证 User Index 和工作树与 Agent Commit 对应文件一致。
5. 失败时尝试恢复工作区备份；备份保留。

应用成功后运行必要构建/测试；若处于批次队列中先运行 `progress.ts complete --mode accepted --review APPLIED`，再更新 Agent Project 开发日志与计划并创建 `[user]` Commit 记录用户接受 Agent 实现。User Project 不创建 Commit。

### 9.2 用户明确要求保留半成品

如果用户明确说要保留自己的实现：

1. 不运行 `sync-user.ts apply-agent`，因为它会清理未暂存工作区。
2. 先使用 `backup-worktree.ts create` 备份当前未暂存工作区。
3. AI 检查用户半成品和 Agent 实现，确定可复用部分、冲突和替换范围。
4. 说明合并方案；有重要取舍时征求用户意见。
5. 只在用户明确授权的范围内修改 User Project。
6. 构建和测试后，记录用户选择保留半成品及合并结果。
7. Agent Project 创建 `[user]` Commit 记录最终接受结果。

## 10. 工作区备份

备份目录固定为 `workspace/.teaching/backup/current/`，只保留一份最新备份。

备份范围：

- 未暂存的已跟踪文件修改和删除。
- 未跟踪且未被 Git 忽略的文件和符号链接。
- 不备份、不清理已暂存内容。
- Git 忽略文件默认不处理。

任何清理 User Project 工作树的操作前必须先备份并验证。新备份应先写入临时目录并验证，成功后再替换旧备份。恢复前检查 Index Tree 是否仍与备份一致，并检查是否会覆盖新文件/修改；不安全时停止。

命令：

```bash
# 创建/替换最新备份
npm --prefix <skill-dir> run backup-worktree -- create --user-dir <workspace>/user-project --workspace <workspace>

# 备份后清理未暂存工作区（保留 Index）
npm --prefix <skill-dir> run backup-worktree -- clear --user-dir <workspace>/user-project --workspace <workspace>

# 恢复最新备份
npm --prefix <skill-dir> run backup-worktree -- restore --user-dir <workspace>/user-project --workspace <workspace>
```

不得在没有有效备份的情况下执行 `git restore --worktree`、`git clean -fd` 或其他清理操作。不要使用 `git reset --hard`。

## 11. Commit 规范与自动提交

统一格式：

```text
[来源] type(scope): description
```

- `[agent]`：Agent 自己实现。
- `[user]`：吸收用户实现，或记录用户明确接受 Agent 实现。
- `type`：`initial`、`feat`、`fix`、`refactor`、`docs`、`test`、`build`、`chore`、`perf`、`style`、`ci`、`revert` 等。
- `scope`：模块或功能。
- `description`：简洁描述本次实际修改。

使用 `checkpoint.ts` 精确暂存并提交（常规少量文件直接重复传 `--path`，文件较多时可改用 `--paths-file`）：

```bash
npm --prefix <skill-dir> run checkpoint -- \
  --agent-dir <workspace>/<agent-project-name> \
  --source agent|user \
  --type feat \
  --scope config \
  --message "添加配置文件读取" \
  --path src/config.ts \
  --path docs/development-plan.md \
  --path docs/development-log.md \
  --path docs/teaching-state.md
```

脚本要求 Agent Project 暂存区在提交前干净，只暂存明确指定的路径，并检查暂存文件列表；如果 `development-plan.md`、`development-log.md`、`teaching-state.md`、`NOTES.md`、`MISSION.md` 或 `RESOURCES.md` 有修改但未列入 `--path` / `--paths-file`，将拒绝提交。Git 身份未配置时停止，不修改全局配置。不得提交 User Project。

## 12. 跨会话恢复（先读索引，按需展开）

1. **优先运行** `npm --prefix <skill-dir> run progress -- status --agent-dir <Agent Project>`（可加 `--json`），一次取得双进度、待学 Commit、未登记提交和阻塞状态；同时直接阅读简短的 `NOTES.md`（及 `teaching-state.md` 下的短期教学提示），按需阅读 `MISSION.md` 和相关 `RESOURCES.md`，不要每次全文读取开发日志。
2. 检查 Agent Project / User Project 的 Git 状态、当前 Review 快照和工作区备份；若索引与实际 Git 提交不一致，先停止自动推进，核对进度。
3. 仅在需要细节时读取 `development-plan.md` 当前模块、`development-log.md` 最近步骤以及具体的源码/Commit。
4. 对旧版只有非结构化 `teaching-state.md` 的工作区，执行 `progress.ts init` 并**人工核对旧状态**；脚本保留旧文档，不从对话记忆猜测游标。
5. 有未完成的 Review、暂存验证、Patch 应用或备份恢复时，先保证安全，不自动清理 User Project。

## 13. 脚本清单

| 脚本 | 职责 |
|---|---|
| `scripts/setup.ts` | workspace、示例项目导入、User Project 链接、Git 基线、Agent Project 初始化 |
| `scripts/copy-project.ts` | 复制 User Project；排除 `.git` 和 Git ignore 匹配项；保留内部符号链接 |
| `scripts/review-diff.ts` | 自动输出内联差异、保存快照、记录用户选型；按快照精确暂存并验证 |
| `scripts/backup-worktree.ts` | 备份、清理和恢复 User Project 未暂存工作区 |
| `scripts/sync-user.ts` | 吸收用户已审查实现；或备份后应用 Agent Patch |
| `scripts/checkpoint.ts` | 精确暂存 Agent Project 文件并创建 `[agent]` / `[user]` Commit |
| `scripts/common.ts` | 脚本共享的 Git、路径、文件和快照工具 |
| `scripts/doctor.mjs` | 检查 Node.js、Git 和 TypeScript 执行环境（Node 22.6+ 支持免安装运行） |
| `scripts/run.mjs` | 零依赖脚本启动器：优先使用已安装的 `tsx`，否则在 Node 22.6+ 自动回退 `--experimental-strip-types` |
| `scripts/progress.ts` | 双游标、批次队列自动收集与跨会话状态汇总 |

脚本使用 Node.js 标准库和 Git CLI，不引入额外业务运行时依赖。Node.js 22.6+ 无需在 Skill 目录执行 `npm install`，直接通过 `npm --prefix <skill-dir> run <script> -- ...` 运行；Node.js 20.0–22.5 需先在 Skill 目录执行 `npm install`。

## 14. 每轮交互要求

- Agent 可连续准备多个小 Commit；批次开发完成后汇报各步骤并等待用户，不必每做一小步就停止。
- 用户问问题时先解释，不因用户提问自动进入下一步骤。
- 用户说实现完成或要求 Review 时，先运行自动 Diff；存在差异时根据首次 diff 审计，实质差异需对比双方优劣并提出明确推荐，然后**停止本轮等待用户回复**，收到明确选择后再记录 `decide`，禁止自动吸收或代用户决策。
- 用户选择直接移植时，先按第 9 节检查基线和备份，再应用 Patch。
- 逻辑错误时指出问题并等待用户修改；不得代修 User Project，除非用户明确授权。
- 不要求用户照抄 Agent 代码；正确的用户改进应被吸收到 Agent Project。
- 开发是首要目标：仅在用户明确要求更细讲解、Review 暴露反复障碍或用户确认已有基础时，在 `teaching-state.md` 自由区块记录短期教学提示，轻量调整后续讲解与尚未准备的步骤；不建立独立学习档案或能力评分。
- 双游标与批次队列通过 `progress.ts` 写入 `teaching-state.md`；用户确认的新长期要求/偏好/纠偏由 Agent 直接维护在 `NOTES.md` 并随检查点提交。计划与开发日志仍按步骤维护。

## 15. 环境自检与回归测试

- 首次操作前运行 `npm --prefix <skill-dir> run doctor`；Node 22.6+ 支持免安装原生运行，无需执行 `npm install`；仅在 Node 20.0–22.5 且缺少 `tsx` 时再安装依赖。`npm run setup` 会自动运行 `presetup` 预检。
- 修改脚本后运行 `npm test`，覆盖 Root Commit、新文件二进制 Diff、悬空链接、暂存区回滚及文档同提交。
- Skill 保留 `agents/openai.yaml` 调用元数据；在支持该配置的宿主中关闭隐式调用。
