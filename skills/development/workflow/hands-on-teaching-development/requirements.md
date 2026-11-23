# Hands-on Teaching Development — 需求基线

- **状态**：用户确认
- **日期**：2026-11-19
- **用途**：本文件是 Skill、配套脚本、模板实现和后续修改的需求基准。除非用户明确要求变更，不得擅自偏离。

## 1. 目标与核心原则

本 Skill 用于手把手带用户实现一个最终项目或产品。AI 研究一个或多个现有项目，从中提取有价值的设计和代码；AI 在 Agent Project 中持续实现小功能；用户可以在 User Project 中亲自实现，也可以明确要求直接采用 Agent 实现。

核心原则：

1. **Agent Project 是用户最终想要实现的项目或产品。** 它不是任何单一示例项目的副本。
2. 一个 Agent Project 可以关联多个 Example Project；每个 Example Project 可以只贡献一个局部思路，也可以贡献大部分实现。
3. AI 应主动评价 Example Project 的设计与实现，不机械照搬。若更优方案会改变已确认的方向或产生重要取舍，必须先征求用户意见。
4. AI 负责 Agent Project 的实现、中文教学注释、开发计划、过程记录和 Git 历史。
5. User Project 默认由用户亲自实现。初始化完成后，未经用户明确授权，AI 不得修改；用户明确要求直接移植或接管时，按本需求规定操作。
6. Example Project 严格只读。AI 不得修改其内容、Git 状态或配置。
7. 所有可能覆盖或清理 User Project 工作区的操作，必须先备份未暂存改动和未跟踪文件。
8. 可确定性自动化的文件操作、Diff 提取、内容比较、备份、Patch 应用和精确提交，优先交给 TypeScript 脚本；AI 负责理解、技术判断、代码审查和教学。

## 2. 三种项目定义与权限

### 2.1 Example Project（示例项目）

- 用户提供的一个或多个现有项目，是分析、学习和代码提取的资料来源。
- AI 可以读取源码、文档、构建配置、测试和 Git 历史。
- AI 严格只读：不得编辑、格式化、生成文件、切换分支、暂存、提交、清理或修改配置。
- 不要求完整吸收；可以按功能、模块、设计思想或代码片段选择性借鉴。
- 多个 Example Project 实现存在分歧时，AI 必须对比优缺点并咨询用户。

### 2.2 User Project（用户项目）

- 用户学习并亲自实现目标产品的项目。
- 必须有独立 Git 仓库。
- 初始化期间允许 AI 创建工程骨架、构建环境、基础依赖和最小可编译/可运行实现。
- 初始化完成后，除非用户明确要求，不得修改 User Project。
- 默认只读审查：可读取、分析、运行构建和测试、提取 Diff。
- 用户明确要求直接移植 Agent 实现时，允许按第 8 节修改。
- 用户明确要求保留半成品并让 AI 合并时，允许在用户确认的范围内修改。
- 不要求每个步骤 Commit；每个步骤 Review 通过后，由 `review-diff.ts stage-reviewed`（或用户手工精确 `git add`）将本步骤文件加入暂存区，用户可在多个步骤或一个模块完成后自行 Commit。
- Skill 不擅自创建 User Project Commit。

### 2.3 Agent Project（Agent 项目）

- 用户最终想要实现的项目或产品，是双方持续演进的最终成果。
- 由 AI 在其中编写参考实现、改进方案、中文教学注释和开发文档。
- 每个小步骤单独创建 Git Commit，但 Agent 可以在一个连续开发批次中先准备多个 Commit，用户之后逐 Commit 实现。
- 用户手写实现通过 Review 后，同步到 Agent Project，并创建 `[user]` Commit。
- 用户明确接受 Agent 实现时，也创建 `[user]` Commit，记录用户接受该实现。
- Agent Project 始终拥有独立 Git 仓库，不继承 User Project 的 Git 历史。

## 3. Workspace 目录与路径管理

标准结构：

```text
workspace/
├── AGENTS.md
├── examples/
│   ├── example-a/                 # 实体目录
│   └── example-b -> /path/to/b    # 符号链接
├── user-project -> /path/to/user  # User Project 符号链接
├── .teaching/                     # Review 快照和单份工作区备份
│   ├── review/
│   └── backup/current/
└── <agent-project-name>/          # Agent Project 实体目录
    ├── .git/
    ├── docs/
    └── ...
```

### 3.1 Example Project 导入

- `examples/` 是普通目录，允许实体目录和符号链接共存。
- Example Project 已位于 `examples/` 内：保持原样。
- Example Project 位于 workspace 内、但不在 `examples/` 内：移动到 `examples/<项目名>/`，保留项目自身文件和 `.git` 历史。
- Example Project 位于 workspace 外：在 `examples/<项目名>` 创建符号链接，指向外部路径。
- 目标名称冲突时停止并请用户决定，不覆盖、不自动改名。
- Example Project 必须是可访问的目录。

### 3.2 User Project 链接

- `workspace/user-project` 必须是符号链接，目标路径由用户明确提供。
- 创建链接时不以目标是否存在为前提；允许先创建悬空链接，再创建目标目录和工程骨架。
- 链接位置已存在非同目标的文件、目录或链接时，不覆盖，停止并请用户处理。
- User Project 与任何 Example Project 的实际路径不得重叠，避免破坏示例项目只读边界；检测到重叠时停止。
- User Project 的实际内容始终位于链接目标。

### 3.3 Agent Project 名称

- Agent Project 目录位于 workspace 根目录。
- 目录名由用户明确确认。
- 名称对应最终产品/项目，不根据某个 Example Project 自动命名。
- 不得覆盖已有目录或仓库。

## 4. 首次启动与工作区约束文件

- Skill 包内提供 `references/AGENTS.md`，作为 workspace 级约束。
- 首次初始化时，将其复制到 `workspace/AGENTS.md`。
- 如果目标不存在，复制；如果已存在且内容相同，跳过；如果已存在但内容不同，不覆盖，提示 AI 阅读现有规则并请用户决定如何合并。
- `workspace/AGENTS.md` 约束整个教学 workspace 中的 Example、User、Agent 项目操作边界。

## 5. User Project 初始化

### 5.1 项目状态

- User Project 不存在：按不存在项目处理。
- User Project 是空目录：等同于不存在。
- User Project 已有部分实现：保留现有内容，分析后继续，不擅自重建。
- 用户明确指定模板：优先使用该模板。
- 用户未指定模板：根据目标产品和 Example Project 选择合适的最小工程结构，补齐基本编译/运行环境。

### 5.2 AI 初始化权限

- AI 可以创建最小可编译、可运行的工程骨架，也可以加入必要的最小运行实现。
- AI 可以反思 Example Project 的实现，采用更优方案；改变主要方向或重要设计取舍前先征求用户意见。
- 初始化完成并建立教学基线后，User Project 进入默认禁止修改状态。

### 5.3 User Project Git

- User Project 必须是独立 Git 仓库。
- 已有独立 Git 仓库：直接使用，保留历史、分支和配置。
- 没有 Git 仓库：执行 `git init`，不创建初始 Commit。
- 不处理 User Project 嵌套在其他 Git 仓库中的情况。
- 初始化阶段自动执行一次 `git add -A`，建立教学暂存区基线；不创建 Commit。
- 该操作可能将现有未提交改动加入暂存区，这是用户明确批准的初始化行为。
- 后续每个用户步骤 Review 通过后，通过 `review-diff.ts stage-reviewed`（或用户手工精确 `git add`）将本步骤文件加入暂存区。

## 6. Agent Project 初始化

- 从 User Project 当前工作树复制，不从 User Project Git 历史克隆。
- 不复制 `.git` 目录或 `.git` 文件。
- 复制规则由独立 `copy-project.ts` 实现。
- 排除任意层级的 `.git` 元数据。
- 使用 Git ignore 规则排除匹配文件/目录；为简化实现，不查询 Git Index，即使某文件已被跟踪但匹配忽略规则，也不复制。
- 不另设固定排除列表；除 `.git` 和 Git ignore 匹配项外，其他文件正常复制。
- 内部符号链接只复制链接本身，不跟随、不复制目标内容。
- User Project 根路径为符号链接时，复制其实际目录内容。
- 创建全新独立 Git 仓库，不继承 User Project 历史。
- 初始提交：`[agent] initial(init): 初始化项目基础结构`。
- Git 身份未配置时停止并提示用户自行配置；不修改全局 Git 配置。

## 7. 开发计划、日志与状态

Agent Project 内维护：

```text
docs/
├── development-plan.md
├── development-log.md
├── teaching-state.md
├── NOTES.md
├── MISSION.md
└── RESOURCES.md
```

### 7.1 development-plan.md

记录整体目标、范围、Example Project 来源、技术方案、模块依赖、开发顺序、步骤和验收标准。长期维护。用户可以调整教学执行顺序，但不因此擅自修改产品目标和功能范围。

### 7.2 development-log.md

按步骤记录实际发生的事情：Agent 实现、文件改动、参考来源、用户实现方式、Diff 结果、Review 结论、用户代码吸收、用户改进、测试结果和对应 Commit。原则上追加记录，不随意改写历史。

### 7.3 teaching-state.md

由 `progress.ts` 维护机器可读区块；分别记录 Agent 已准备的多个 Commit、User 已完成的步骤、当前教学队列、双游标与阻塞事项。跨会话只需读取轻量索引，必要时再按需展开详细开发日志。

### 7.4 NOTES.md

由 Agent 直接以 Markdown 维护（格式可按项目需要自由组织），持久记录经过用户确认的产品需求、工程/教学偏好、技术选择和纠偏。新旧规则冲突时明确废止或移出旧条目，保留简短历史便于追溯。单次 Review 选型、Agent 未确认的建议或短期教学反馈不得直接写入 `NOTES.md`。

### 7.5 MISSION.md

保持在一屏以内，记录产品动机、开发中希望掌握的关键能力、客观成功标准与当前边界。具体功能清单、设计与步骤仍归属 `development-plan.md`；目标变化前必须先征求用户确认。

### 7.6 RESOURCES.md

索引多个 Example Project 的路径、固定版本或 Git SHA、具体文件/方法定位、借鉴内容、局限与方案分歧。保持精简来源导航，不复制完整示例源码，也不替代详细开发计划。

### 7.7 计划执行

- 每次开始新模块/功能时，先询问用户本次优先级。
- 用户可以要求提前或推迟模块；记录顺序变化和原因，不删除整体计划内容。
- 一个小函数或独立小功能是默认步骤粒度。
- 一个步骤可以涉及多个文件；复杂功能拆分；避免过度拆分。
- Agent Project 每个步骤尽量对应一个独立 Commit。
- 根据用户实际开发表现、Review 反馈或明确要求，Agent 可在 `teaching-state.md` 自由区块记录短期教学提示，轻量调整后续未准备批次的步骤粒度、讲解深度和重点；**不自动重写已提交的教学 Commit**，不对用户能力评分，不建立独立 `learning-records/` 学习档案。

## 8. 教学实现与两种学习方式

### 8.1 手写方式（默认）

1. AI 在 Agent Project 可连续实现一个功能/模块的多个小步骤，分别提交多个独立 `[agent]` Commit，并登记批次队列。
2. 新增/修改代码应补充中文教学型注释：解释重要函数职责、关键逻辑、设计原因、易误解机制；不要求每行注释。
3. 执行必要构建和测试。
4. 各小步骤均创建 `[agent]` Commit，不必每次提交后打断 Agent 开发。
5. 批次开发完成后，登记 Commit 教学队列并汇报学习顺序，然后等待用户逐步实现。
6. 用户提出疑问时正常解释；用户表示完成后进入 Review。

### 8.2 直接接受 Agent 实现

用户明确要求直接移植时，不再要求用户手写。正常情况下通过脚本将 Agent 对应 Commit 的 Patch 应用到 User Project。

### 8.3 用户写了一半后要求 Agent 接管

- **默认行为**：除非用户明确要求保留自己的半成品，否则直接通过脚本应用 Agent 对应 Commit 的 Diff/Patch。
- 应用前必须备份 User Project 未暂存的已跟踪文件修改和未跟踪文件。
- 清理未暂存工作区，保留已有暂存区内容；Git 忽略文件默认不处理。
- 校验 User Project 暂存区与 Agent Commit 父版本的比较基线；不一致时停止，不做破坏性清理。
- 使用 `git apply --index` 应用 Patch，使 Agent 实现进入 User Project 暂存区。
- 应用后校验 User Project Index 和工作树与 Agent Commit 对应文件一致。
- 应用失败时尝试恢复备份；备份始终保留，直到被下一次成功备份替换。
- **例外**：用户明确要求保留自己的实现时，AI 先分析半成品，再与 Agent 实现合并；不运行默认清理/覆盖流程。存在重要冲突或不确定性时先询问用户。
- 用户接受 Agent 实现后，Agent Project 创建 `[user]` Commit 记录用户接受；该 Commit 表示接受来源，不声称代码由用户亲手编写。

## 9. User Project Review 与自动 Diff

### 9.1 用户工作方式

- 用户在 User Project 工作树中手写当前步骤，不要求先 Commit，也不要求 Review 前先暂存。
- Review 通过后，由 `review-diff.ts stage-reviewed`（或用户手工精确 `git add`）将本步骤涉及文件加入暂存区。
- 用户可以多个步骤或一个模块后再自行创建 User Project Commit。

### 9.2 自动比较

`review-diff.ts` 负责确定性比较。比较对象：

1. User Project 当前工作树文件状态（包括未跟踪文件）。
2. User Project Index 与**教学队列指定**的 Agent Commit 父版本状态比较，不使用最新 HEAD 代替当前步骤。
3. Agent Project **当前待教学 `[agent]` Commit** 的最终状态（可以早于 HEAD）。

不得只依赖原始 Diff 文本相等；应比较文件内容、存在状态、符号链接目标和可识别的文件模式。

结果：

- `EXACT_MATCH`：相关文件与 Agent 实现完全一致；不重复做深入逻辑审查，但仍执行必要构建和测试。
- `DIFFERENT`：先审查差异部分；根据风险决定是否扩大到完整逻辑审查。实现可以不同，只要逻辑正确、无 Bug，不要求字面一致。
- `BASELINE_MISMATCH`：User Index 与 Agent Commit 父版本不一致，不能自动判定；交给 AI 分析原因，必要时询问用户。

脚本必须：
- 包含已跟踪文件未暂存修改、删除和未跟踪文件。
- 不把此前已暂存的完成步骤误当作本次工作区修改。
- 不修改 User Project，不执行 `git add`。
- 保存 Review 快照，记录本次文件状态、Agent Commit 和比较基线。
- 暂存后验证暂存区内容与 Review 快照一致；如果 Review 后文件又发生变化，要求重新 Review。

### 9.3 Review 审计、选型与吸收

1. `review-diff.ts review` 首次 JSON 返回内联差异（`diffs.userVsAgent`、`diffs.userPatch`、`diffs.agentPatch`、`diffs.extraUserPatch`）及 `whitespaceOnlyCandidates`（超大补丁显式标记截断并保留报告文件）；缩进/空白候选仅作提示，不能跳过语言相关的语义审查。
2. `EXACT_MATCH`：执行必要构建和测试后，可由 Agent 调用 `review-diff.ts stage-reviewed` 精确暂存当前步骤文件，保留已有暂存内容，无需用户手工 `git add`。
3. `DIFFERENT`：不全盘自动吸收用户实现。Agent 根据 diff 审查正确性、逻辑、边界、风格及与后续已准备 Commit 的关系；对于实质方案差异，应对比 Agent/User 双方优劣、权衡风险与后续影响并给出**明确推荐方案与理由**（用户实现带来新启发时可列为待确认建议，不自动扩展产品范围）；纯风格或命名差异可简短说明，不必机械填表。经用户明确确认后，运行 `review-diff.ts decide --choice user|agent --reason "..."`（可选附带 `--recommend`、`--inspiration`）记录选型。存在未修复 Bug 时不得放行吸收。
4. 用户选择 `user` 路线：执行 `review-diff.ts stage-reviewed` 精确暂存并校验（若用户手工 `git add` 或跨轮复核，则运行 `verify-staged`），再运行 `sync-user.ts absorb` 将差异代码同步到 Agent Project。`EXACT_MATCH` 不把旧步骤文件覆写回领先的 Agent `HEAD`；`DIFFERENT` 且与后续已准备 Commit 修改同一文件时，`absorb` 阻断覆盖，使用 `progress.ts pause` 记录原因并协调后续队列。
5. 用户选择 `agent` 路线：不执行 `stage-reviewed` / `absorb`，按第 8.3 节通过 `sync-user.ts apply-agent` 备份用户未暂存工作区并应用当前待学 Commit Patch。
6. 更新 `development-log.md`、`development-plan.md`、`teaching-state.md` 及其他发生变更的文档；通过 `progress.ts complete` 推进 User 游标（不改变其余已准备步骤），并用 `checkpoint.ts` 精确提交 `[user]` Commit。如果代码完全一致没有代码变更，也必须更新文档并创建 `[user] docs(scope): ...` Commit。

## 10. Commit 规范

统一格式：

```text
[来源] type(scope): description
```

- 来源：`[agent]` 或 `[user]`。
- 类型：`initial`、`feat`、`fix`、`refactor`、`docs`、`test`、`build`、`chore`、`perf`、`style`、`ci`、`revert` 等。
- scope：本次修改涉及的模块/功能。
- description：简洁描述实际修改。

示例：

```text
[agent] initial(init): 初始化项目基础结构
[agent] feat(config): 添加配置文件读取
[user] feat(config): 吸收用户实现的配置读取逻辑
[user] docs(config): 记录用户完成配置读取手写实现
```

- Agent 实现使用 `[agent]`。
- 吸收用户实现或记录用户接受 Agent 实现使用 `[user]`。
- Agent Project 每个小步骤单独提交；允许一次连续开发多个提交的批次，不强制每个 Commit 后与用户交互。
- 只暂存本次明确涉及的文件；提交前核对 staged 文件列表和 Diff。
- 不提交 User Project。

## 11. 工作区备份与恢复

- 默认备份目录：`workspace/.teaching/backup/current/`。
- 只保留一份最新备份；新备份完成并校验后才替换旧备份。
- 备份范围：未暂存的已跟踪文件修改/删除，以及未跟踪且未被 Git 忽略的文件/符号链接。
- 不备份或清理已暂存内容。
- Git 忽略文件默认不处理。
- 每次清理 User Project 工作区前必须先备份并验证。
- 备份记录 User Project 路径和 Index Tree；Index 改变后禁止盲目恢复。
- 恢复前检查是否会覆盖用户后来产生的新文件/修改；冲突时停止，不覆盖。
- 不得在没有有效备份的情况下执行 `git restore --worktree`、`git clean -fd` 等破坏性清理。

## 12. 跨会话恢复与退出

- 进入或继续教学时，先运行 `scripts/progress.ts status` 读取结构化教学状态、双游标和最近 Git 提交，再直接查阅精简的 `NOTES.md`（及按需读取 `MISSION.md` / `RESOURCES.md`），不需要全文读取开发日志。
- 检查 Agent Project 和 User Project 的 Git 状态、当前 Review 快照、待同步步骤和工作区备份。
- 保留所有未提交/未暂存差异，不因恢复会话自动清理。
- 不重复执行已完成步骤；从当前锚点继续。
- 退出教学模式后停止教学循环，保留所有文档、Git 历史和备份。

### 触发词

- 进入：`教学模式`、`teach 模式`、`手把手教学`。
- 恢复：`继续教学`。
- 退出：`退出教学模式`、`cancel teach`。
- 不设置“我自己写，你别管”作为特殊退出触发词。

## 13. 脚本交付范围

至少提供以下 TypeScript 脚本：

| 脚本 | 职责 |
|---|---|
| `setup.ts` | workspace、示例项目导入、User Project 符号链接、User Git 基线、Agent Project 初始化 |
| `copy-project.ts` | 从 User Project 复制到 Agent Project，按 Git ignore 规则过滤并保留内部符号链接 |
| `review-diff.ts` | 自动比较 User 工作树、User Index 基线和 Agent Commit；保存 Review 快照；验证用户暂存结果 |
| `backup-worktree.ts` | 创建/替换单份工作区备份、清理未暂存工作区、恢复备份 |
| `sync-user.ts` | 吸收已审查的 User 实现；或备份后将 Agent Patch 应用到 User Project |
| `checkpoint.ts` | 精确暂存 Agent Project 指定文件并创建规范 Commit |
| `progress.ts` | Agent/User 双游标、批次队列自动收集与跨会话状态汇总 |

脚本应使用 Node.js 标准库和 Git CLI，避免不必要的第三方依赖。运行环境建议 Node.js 20+，通过 `npx tsx` 执行。

## 14. 非目标与禁止事项

- 不把 Example Project 复制成 Agent Project 并机械复刻。
- 不要求 User Project 与 Agent Project 代码字面一致。
- 不在 User Project 初始化完成后擅自修改业务代码。
- 不在 Example Project 中写入任何文件。
- 不提交 User Project。
- 不覆盖用户已有的 workspace `AGENTS.md`、目录、符号链接或项目内容。
- 不自动修改 Git 全局配置。
- 不为追求自动化而实现不必要的复杂框架。
- 不在没有备份的情况下清理 User Project 工作区。
- 不建立独立 `learning-records/`、掌握度档案或用户能力评分体系；本 Skill 以项目开发为核心，即时教学反馈归入 `development-log.md`，短期教学节奏归入 `teaching-state.md`。

## 15. 实现与安全细节约束

- Skill 内 `references/AGENTS.md` 作为 workspace 级约束，首次初始化复制到 workspace 根目录，已有且内容不同时不覆盖。
- Root Commit 按 Git 空树基线处理；merge commit 不属于单一步骤，明确拒绝。
- Review 产出的 `user.patch` 和 `user-extra.patch` 包含未跟踪的新建文件（包括二进制与符号链接）。
- Agent Patch 应用失败时，回滚必须恢复应用前完整 Index Tree，保留用户已暂存的旧步骤；不得运行会丢失暂存历史的普通 `git reset`。
- 所有已修改的核心教学文档（`development-plan.md`、`development-log.md`、`teaching-state.md`、`NOTES.md`、`MISSION.md`、`RESOURCES.md`）必须随当步代码一起由 `checkpoint.ts` 精确提交；新 Agent Project 初始化自动创建模板，已有项目不覆盖原文档。
- `progress.ts prepare` 以 SHA 固定已准备的教学队列，`progress.ts complete` 在 `[user]` 检查点前更新用户完成游标；`review-diff.ts` 与 `sync-user.ts` 接受教学队列中仍为当前 Git 历史祖先的 `[agent]` Commit，不强制等于 `HEAD`。
- 提供 `node:test` 回归测试套件、预安装可执行的 `doctor.mjs` 环境自检，以及 `agents/openai.yaml` 宿主调用元数据。
