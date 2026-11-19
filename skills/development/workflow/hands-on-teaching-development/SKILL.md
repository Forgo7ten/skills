---
name: hands-on-teaching-development
description: 手把手带用户实现最终项目的教学开发模式。AI 研究一个或多个 Example Project，在独立 Agent Project 中逐步实现并提交；用户可在 User Project 手写并接受 Review，也可明确要求直接移植 Agent 实现。用户说“教学模式 / teach 模式 / 手把手教学”时进入，说“继续教学”时恢复，说“退出教学模式 / cancel teach”时退出。
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
5. User Project 初始化完成后，默认禁止 AI 写入；Review、读取、构建和测试不属于修改业务代码。
6. Agent Project 每个小步骤单独 Commit；用户实现通过 Review 后，或用户明确接受 Agent 实现后，在 Agent Project 创建 `[user]` Commit。
7. 不提交 User Project，不修改 Example Project。

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
        └── teaching-state.md
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

原则上追加历史记录，不随意重写已完成步骤。

### `docs/teaching-state.md` — 当前状态

记录当前阶段、模块、步骤、Agent Commit、步骤文件列表、Review 状态、Review 快照、待用户暂存、待同步、待创建 `[user]` Commit、最近备份路径、未完成 Patch 操作和阻塞事项。它用于跨会话恢复，不替代计划和日志。

## 6. 开发计划与步骤粒度

1. 根据用户目标决定实现全部产品还是指定模块/功能。
2. 分析 Example Project，比较可借鉴实现，明确采用/不采用的部分。
3. 编写整体计划和模块依赖。
4. 每次进入新模块时询问用户优先级。
5. 一个小函数或独立小功能是默认步骤粒度；一个步骤可以涉及多个文件。
6. 复杂功能主动拆分，但不为凑数量过度拆分。
7. 每个 Agent 步骤尽量对应一个独立 Git Commit。

## 7. Agent 手把手实现循环

每个步骤按以下流程执行：

1. 更新 `teaching-state.md`，标记当前模块、步骤、文件清单和状态。
2. 在 Agent Project 实现一个小功能；不在 User Project 写业务代码。
3. 新增/修改代码时补充中文教学型注释，解释重要函数职责、关键逻辑、设计原因和容易误解的机制；不要求每行都注释。
4. 运行项目适用的构建、测试或静态检查。
5. 更新 `development-plan.md`、`development-log.md`、`teaching-state.md`。
6. 使用 `checkpoint.ts` 精确暂存本步骤代码与本次修改的 `docs/development-plan.md`、`docs/development-log.md`、`docs/teaching-state.md`，检查提交文件清单，**在同一 Commit 原子提交代码和教学记录**。
7. 告诉用户：实现了什么、修改哪些文件/位置、关键代码含义、如何查看和验证。
8. 停止并等待用户；不要主动实现下一个步骤。

Commit 格式：

```text
[agent] feat(scope): 添加某个小功能
```

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

在 Agent Project 中为当前步骤准备一个 JSON 文件，例如 `workspace/.teaching/review/current-step-paths.json`：

```json
[
  "src/config/ConfigLoader.java",
  "src/config/ConfigOptions.java"
]
```

运行：

```bash
npm --prefix <skill-dir> run review-diff -- review \
  --user-dir <workspace>/user-project \
  --agent-dir <workspace>/<agent-project-name> \
  --workspace <workspace> \
  --agent-commit <当前步骤的agent-commit> \
  --paths-file <workspace>/.teaching/review/current-step-paths.json
```

脚本结果：

- `EXACT_MATCH`：用户工作树文件与 Agent 实现一致，且 Index 基线匹配。跳过重复的深入逻辑审查，但仍运行必要构建和测试。
- `DIFFERENT`：Index 基线匹配但代码不同。先审查差异部分，根据风险决定是否扩大到完整逻辑审查。
- `BASELINE_MISMATCH`：User Index 与 Agent Commit 父版本不一致。不得误判为用户实现错误或自动通过；AI 需分析基线差异，必要时咨询用户。

脚本会在 `workspace/.teaching/review/latest.json` 保存 Review 快照，并生成 `agent.patch`、`user.patch`、`user-extra.patch`、`paths.json`、`user-untracked/` 和 `user-extra/`。如果 User Project 还有当前步骤清单之外的未暂存改动/未跟踪文件，返回 `DIFFERENT` 并列出 `extraUserPaths`；AI 判断它们是否属于本步骤，必要时扩展文件清单后重新 Review。脚本只读 User Project，不执行 `git add`。

### 8.3 AI 审查原则

- 完全一致：不重复做深入逻辑审查；构建和测试仍需验证。
- 存在差异：优先审查差异部分，检查逻辑、边界条件、异常处理、资源释放、安全性和潜在 Bug；必要时扩大到完整审查。
- 代码思路、命名、结构可以不同。只要逻辑正确且无 Bug，不要求用户照抄 Agent 实现。
- 有问题：指出文件、位置、原因和修正方向；不代替用户修改 User Project。等待用户修正后重新 Review。
- 通过：先保留 Review 快照，再提示用户精确暂存当前步骤文件。

### 8.4 用户暂存与复核

Review 通过后提示用户执行精确路径的 `git add`，例如：

```bash
git -C <workspace>/user-project add -- src/config/ConfigLoader.java src/config/ConfigOptions.java
```

不得使用 `git add .` 或 `git add -A` 代替当前步骤的精确暂存。

随后运行：

```bash
npm --prefix <skill-dir> run review-diff -- verify-staged \
  --user-dir <workspace>/user-project \
  --workspace <workspace>
```

只有返回 `VERIFIED` 才能同步。若 Review 后用户又修改了文件，或暂存内容与快照不一致，必须重新 Review。

### 8.5 吸收用户实现

```bash
npm --prefix <skill-dir> run sync-user -- absorb \
  --user-dir <workspace>/user-project \
  --agent-dir <workspace>/<agent-project-name> \
  --workspace <workspace>
```

脚本只将快照中已验证的步骤文件从 User Project 同步到 Agent Project，不修改 User Project。

然后 AI 更新计划、日志和状态，使用 `checkpoint.ts` 将本步代码与发生修改的三份教学文档**一并提交**为 `[user]` Commit。

- 有代码差异并吸收：例如 `[user] feat(config): 吸收用户实现的配置读取逻辑`。
- 代码完全一致：没有代码变更需要复制，但仍更新开发记录并创建 `[user] docs(config): 记录用户完成配置读取手写实现`。

## 9. 用户明确要求直接移植 Agent 实现

用户可以不手写，直接要求把当前 Agent 实现同步到 User Project。

### 9.1 正常情况或用户写了一半但未要求保留

默认直接使用脚本应用 Agent 对应 Commit Patch。即使用户写了一半，只要用户没有明确要求保留半成品，也按此默认策略处理。

前置条件：

- User Project Index 对应文件必须与 Agent Commit 父版本一致。
- 若暂存区基线不一致，停止，不清理、不覆盖，先交给 AI 分析。

执行：

```bash
npm --prefix <skill-dir> run sync-user -- apply-agent \
  --user-dir <workspace>/user-project \
  --agent-dir <workspace>/<agent-project-name> \
  --workspace <workspace> \
  --agent-commit <当前agent-commit> \
  --paths-file <workspace>/.teaching/review/current-step-paths.json
```

脚本行为：

1. 先验证 User Index 与 Agent Commit 父版本一致。
2. 若存在未暂存修改或未跟踪文件，先备份，再清理未暂存工作区；保留已暂存内容。
3. 通过 `git apply --index` 应用 Agent Patch。
4. 验证 User Index 和工作树与 Agent Commit 对应文件一致。
5. 失败时尝试恢复工作区备份；备份保留。

应用成功后运行必要构建/测试，更新 Agent Project 开发日志和状态，并创建 `[user]` Commit 记录用户接受 Agent 实现。User Project 不创建 Commit。

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

使用 `checkpoint.ts` 精确暂存并提交：

```bash
npm --prefix <skill-dir> run checkpoint -- \
  --agent-dir <workspace>/<agent-project-name> \
  --source agent|user \
  --type feat \
  --scope config \
  --message "添加配置文件读取" \
  --paths-file <本次提交文件列表.json>
```

脚本要求 Agent Project 暂存区在提交前干净，只暂存明确指定的路径，并检查暂存文件列表；如果三份核心教学文档有修改但未列入 `--paths-file`，将拒绝提交。Git 身份未配置时停止，不修改全局配置。不得提交 User Project。

## 12. 跨会话恢复

用户说“继续教学”或重新进入 Skill 时：

1. 读取 Agent Project 的 `docs/teaching-state.md`。
2. 读取 `docs/development-plan.md`。
3. 阅读 `docs/development-log.md` 最近记录。
4. 检查 Agent Project `git status --short` 和最近 Commit。
5. 检查 User Project `git status --short`、暂存区和工作区。
6. 检查 `workspace/.teaching/review/latest.json` 是否仍对应当前 Agent Commit。
7. 检查 `workspace/.teaching/backup/current/manifest.json` 是否存在待恢复备份。
8. 如有未完成的 Review、暂存验证、Patch 应用或备份恢复，先恢复安全状态，不得直接开始下一步。
9. 从 `teaching-state.md` 当前锚点继续；不得只依赖对话记忆猜测。

恢复会话时不得自动清理 User Project 未暂存差异。

## 13. 脚本清单

| 脚本 | 职责 |
|---|---|
| `scripts/setup.ts` | workspace、示例项目导入、User Project 链接、Git 基线、Agent Project 初始化 |
| `scripts/copy-project.ts` | 复制 User Project；排除 `.git` 和 Git ignore 匹配项；保留内部符号链接 |
| `scripts/review-diff.ts` | 比较 User 工作树、Index 基线和 Agent Commit；保存 Review 快照；验证用户暂存 |
| `scripts/backup-worktree.ts` | 备份、清理和恢复 User Project 未暂存工作区 |
| `scripts/sync-user.ts` | 吸收用户已审查实现；或备份后应用 Agent Patch |
| `scripts/checkpoint.ts` | 精确暂存 Agent Project 文件并创建 `[agent]` / `[user]` Commit |
| `scripts/common.ts` | 脚本共享的 Git、路径、文件和快照工具 |
| `scripts/doctor.mjs` | 检查 Node.js、Git 和 Skill 依赖环境 |

脚本使用 Node.js 标准库和 Git CLI，不引入额外业务运行时依赖。建议 Node.js 20+；在 Skill 目录执行 `npm install`、`npm run doctor`、`npm test`，从任意工作目录通过 `npm --prefix <skill-dir> run <script> -- ...` 运行。

## 14. 每轮交互要求

- 每完成一个 Agent 步骤，必须告诉用户改了什么、位置在哪里、为什么这样实现，然后等待用户。
- 用户问问题时先解释，不因用户提问自动进入下一步骤。
- 用户说实现完成或要求 Review 时，先运行自动 Diff 脚本。
- 用户选择直接移植时，先按第 9 节检查基线和备份，再应用 Patch。
- 逻辑错误时指出问题并等待用户修改；不得代修 User Project，除非用户明确授权。
- 不要求用户照抄 Agent 代码；正确的用户改进应被吸收到 Agent Project。
- 所有状态变更都要更新 `teaching-state.md`；完成步骤要更新计划和日志。

## 15. 环境自检与回归测试

- 首次操作前运行 `npm --prefix <skill-dir> run doctor`；依赖缺失时先让用户执行 `npm install`。`npm run setup` 会自动运行 `presetup` 预检。
- 修改脚本后运行 `npm test`，覆盖 Root Commit、新文件二进制 Diff、悬空链接、暂存区回滚及文档同提交。
- Skill 保留 `agents/openai.yaml` 调用元数据；在支持该配置的宿主中关闭隐式调用。
