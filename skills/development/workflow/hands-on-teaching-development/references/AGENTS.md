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
- 每个小步骤尽量单独提交。
- 用户手写实现通过 Review 后同步到这里，使用 `[user]` Commit。
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
- User Project 不要求每步 Commit；Review 通过后提示用户精确 `git add`。
- 提交前检查暂存文件列表和 Diff，禁止混入无关文件。
- 不提交 User Project。

## 4. 教学代码要求

- Agent Project 新增的重要类、函数和复杂逻辑应有中文教学型注释。
- 注释解释职责、输入输出、关键逻辑、设计原因和容易误解的机制；不要为了数量给每行代码添加空洞注释。
- 每次只实现一个小功能或一个清晰步骤；多文件改动可以属于同一个功能 Commit。
- 用户实现与 Agent 实现可以不同。只要逻辑正确、无 Bug，不要求字面一致。
- Example Project 之间有分歧或需要改变已确认的设计方向时，先向用户解释选项并征求决定。

## 5. 文档要求

Agent Project 的 `docs/` 中维护：

- `development-plan.md`：整体产品目标、范围、模块、技术方案和步骤计划。
- `development-log.md`：逐步骤记录 Agent 实现、用户实现、Review、吸收内容、测试和 Commit。
- `teaching-state.md`：当前步骤、Review、同步、备份和恢复锚点。

恢复会话时先读取这三份文档，再检查两个可写项目的 Git 状态。不要仅凭对话记忆推断进度。

## 6. 自动化脚本优先

优先使用 Skill `scripts/` 中的脚本完成确定性操作：

- `setup.ts`：workspace 与项目初始化。
- `copy-project.ts`：复制项目、Git ignore 排除、符号链接保留。
- `review-diff.ts`：工作树与 Agent Commit 比较、Review 快照和暂存复核。
- `backup-worktree.ts`：工作区备份、清理和恢复。
- `sync-user.ts`：用户实现吸收或 Agent Patch 移植。
- `checkpoint.ts`：精确暂存和提交。

脚本输出异常、基线不一致、Patch 冲突或验证失败时，停止后续破坏性操作，向用户说明情况，不猜测成功。
