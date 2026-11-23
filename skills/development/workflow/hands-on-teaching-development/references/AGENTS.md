# Workspace Agent Rules — Hands-on Teaching Development

本文件由 `hands-on-teaching-development` Skill 在首次初始化时复制到 workspace 根目录。它约束整个教学工作区。

## 1. 项目角色

Workspace 采用以下结构：

```text
workspace/
├── AGENTS.md
├── examples/                 # Example Project：示例项目
├── user-project -> ...       # User Project：用户项目
└── <agent-project>/          # Agent Project：Agent 项目
```

### Example Project（示例项目）

- 是一个或多个学习资料来源。
- **严格只读**：只允许读取、搜索、构建信息分析和 Git 历史查看。
- 禁止写入、格式化、生成文件、切换分支、暂存、提交、清理或更改配置。
- 可以只借鉴局部代码/设计，也可以借鉴大部分实现；不得机械照搬。

### User Project（用户项目）

- 是用户学习和亲自实现目标产品的项目，必须有独立 Git 仓库。
- 初始化阶段允许创建最小可编译、可运行工程骨架；初始化完成后，默认只读审查。
- 未经用户明确授权，不得修改、格式化、生成、删除、暂存或提交 User Project 内容。
- 用户明确要求直接接受 Agent 实现时，按 Skill 规定先备份未暂存工作区，再使用脚本应用 Patch。
- 用户明确要求保留半成品时，先分析并合并，不得运行默认清理/覆盖流程。
- 不得创建 User Project Commit。

### Agent Project（Agent 项目）

- 是用户最终想要实现的项目或产品。
- AI 在此实现功能、补充中文教学注释、维护计划/日志/状态，并管理 Git 提交。
- 每个小步骤尽量单独提交，但允许 Agent 一次连续开发整个功能并提交多个 Commit，之后用户逐 Commit 学习。
- 用户手写实现必须先通过 Diff 审计；存在差异时**先对 Agent/User 方案作优劣评估、给出明确推荐与用户实现的新启发，然后停止本轮等待用户明确回复**；未收到用户明确确认前，严禁替用户执行 `decide`、`stage-reviewed`、`absorb` 或 `apply-agent`，严禁代写 User Project。使用 `[user]` Commit。
- 用户明确接受 Agent 实现时，也使用 `[user]` Commit 记录接受行为。

## 2. 权限和安全边界

1. 对项目进行操作前，先确认它属于 Example、User 还是 Agent Project。
2. 不得把 Agent Project 的写入权限扩展到 User Project 或 Example Project。
3. User Project 与 Example Project 实际路径不得重叠；若重叠，停止初始化并请用户重新指定路径。
4. 不得把 Example Project 的只读约束误用于 Agent Project。
5. User Project 初始化结束后，任何写操作都必须有用户明确授权或属于已确认的 Review/移植流程。
6. 清理 User Project 未暂存工作区前，必须先运行 `backup-worktree.ts` 并确认备份成功。
7. 不得执行未经备份的 `git restore --worktree`、`git clean -fd`、`reset --hard` 等破坏性操作。
8. 默认直接移植 Agent 实现时，保留 User Project 暂存区，备份未暂存修改和未跟踪文件；用户明确要求保留半成品时不得自动清理。
9. 不得覆盖 workspace 已有 `AGENTS.md`、示例项目、User Project 链接或 Agent Project。
10. 不修改 Git 全局配置。Git 身份缺失时提示用户自行配置。

## 3. Git 约定

Commit 格式：

```text
[来源] type(scope): description
```

- `[agent]`：Agent 自己实现。
- `[user]`：吸收用户实现，或记录用户明确接受 Agent 实现。
- Agent Project 每个小步骤单独提交。
- User Project 不要求每步 Commit；`EXACT_MATCH` 或经审计选用用户实现后，允许 Agent 调用 `stage-reviewed` 精确暂存已审查文件，禁止 `git add .`。
- 提交前检查暂存文件列表和 Diff，禁止混入无关文件。
- 不提交 User Project。

## 4. 教学代码要求

- Agent Project 新增的重要类、函数和复杂逻辑应有中文教学型注释。
- 注释解释职责、输入输出、关键逻辑、设计原因和容易误解的机制；不要为了数量给每行代码添加空洞注释。
- 每个 Commit 只承载一个清晰的小功能；Agent 可连续准备多个 Commit 后再向用户展示教学队列。
- 用户实现与 Agent 实现可以不同。只要逻辑正确、无 Bug，不要求字面一致。
- Example Project 之间有分歧或需要改变已确认的设计方向时，先向用户解释选项并征求决定。

## 5. 文档要求

Agent Project 的 `docs/` 中维护：

- `development-plan.md`：整体产品目标、范围、模块、技术方案和步骤计划。
- `development-log.md`：逐步骤记录 Agent 实现、用户实现、Review、吸收内容、测试和 Commit。
- `teaching-state.md`：由 progress.ts 维护的 Agent/User 双游标、当前批次、Review 和恢复锚点。
- `NOTES.md`：由 Agent 直接维护的 Markdown 文档，记录经用户确认的长期需求、偏好、纠偏和取舍（格式自由，冲突时明确废止旧条目）。一次 Review 选择不自动变成长期偏好。
- `MISSION.md`：为什么开发、预期掌握什么、成功标准和当前不做什么；变更方向先问用户。
- `RESOURCES.md`：可借鉴的示例项目、版本/SHA 和源码定位、取舍理由；不复制整个项目代码。

恢复会话时先运行 `progress.ts status` 读取双游标和当前批次状态，再直接阅读 `NOTES.md`（及 `teaching-state.md` 中的短期教学提示）、按需读取简短 MISSION 与相关 RESOURCES，并检查 Git、Review 快照与备份。仅需要细节时打开最近日志。不要仅凭会话记忆推断进度。

## 6. 自动化脚本优先

优先使用 Skill `scripts/` 中的脚本完成确定性操作：

- `setup.ts`：workspace 与项目初始化。
- `copy-project.ts`：复制项目、Git ignore 排除、符号链接保留。
- `review-diff.ts`：返回内联差异、保存 Review 快照；`DIFFERENT` 须经审计并由用户确认后执行 `decide`，之后才允许精确暂存和吸收。
- `backup-worktree.ts`：工作区备份、清理和恢复。
- `sync-user.ts`：用户实现吸收或 Agent Patch 移植。
- `checkpoint.ts`：精确暂存和提交。
- `progress.ts`：批次提交队列自动登记与 Agent/User 双游标查询。

脚本输出异常、基线不一致、Patch 冲突或验证失败时，停止后续破坏性操作，向用户说明情况，不猜测成功。

## 批量开发与恢复

- 使用 `scripts/progress.ts prepare --auto` 登记已完成的多个 `[agent]` Commit 与步骤 SHA；审查或移植当前步骤默认复用队列游标与文件范围。
- User 游标独立于 Agent HEAD，不能让用户跳到最新实现，也不能用旧步骤的文件覆盖 Agent 新版本。
- 跨会话先运行 `progress.ts status` 读取进度索引，再阅读 `NOTES.md`，必要时才读取相关开发日志与代码。
- 用户确认长期需求/偏好/纠偏后，由 Agent 直接更新 `docs/NOTES.md`，修改同步进入 Agent Git 检查点。

## 开发优先的自适应教学

- 根据用户真实反馈调整讲解，允许对后续**未准备**的 Commit 调整步骤粒度；已准备 Commit 不自动改写。
- 短期教学提示由 Agent 自由记录在 `docs/teaching-state.md` 的 Markdown 区块；用户明确提出可复用长期偏好时再写 `NOTES.md`。
- 本 Skill 不建独立学习档案、不评估用户总体掌握程度；实际问题简要记在 `development-log.md`。
