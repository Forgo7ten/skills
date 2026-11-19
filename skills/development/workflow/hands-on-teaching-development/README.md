# hands-on-teaching-development

一个通过 Example Project、User Project、Agent Project 三项目协作来手把手实现目标产品的 Agent Skill。

## 文件结构

```text
hands-on-teaching-development/
├── SKILL.md                 # Skill 主流程
├── requirements.md          # 用户确认的 v1.0 需求基线（v1.1 修订）
├── package.json
├── tsconfig.json
├── agents/
│   └── openai.yaml          # ChatGPT Skill 展示与调用策略元数据
├── references/
│   └── AGENTS.md            # 首次启动复制到 workspace/AGENTS.md
├── templates/
│   ├── development-plan.md
│   ├── development-log.md
│   └── teaching-state.md
├── tests/
│   └── audit-regressions.test.mjs
└── scripts/
    ├── doctor.mjs
    ├── common.ts
    ├── setup.ts
    ├── copy-project.ts
    ├── review-diff.ts
    ├── backup-worktree.ts
    ├── sync-user.ts
    └── checkpoint.ts
```

## 环境要求

- Node.js 20+
- Git 2.30+
- 在 Skill 目录安装脚本运行依赖：

```bash
cd <skill-dir>
npm install
npm run doctor
npm test
```

`npm run setup` 会先进行无依赖自检。未安装 `tsx` 时，预检仍能执行并提示安装依赖，不会进入初始化。

之后从 workspace 目录通过 `npm --prefix <skill-dir> run <script> -- ...` 调用脚本；若环境已提供 `tsx`，也可直接使用 `npx tsx <skill-dir>/scripts/<name>.ts`。

## 首次初始化

先设置 workspace 的绝对路径（命令通过 `npm --prefix` 在 Skill 目录运行，因此项目参数应使用绝对路径）：

```bash
WORKSPACE=/absolute/path/to/workspace
USER_PROJECT="$WORKSPACE/user-project"
AGENT_PROJECT="$WORKSPACE/my-product"
```

### 1. 建立 workspace、导入示例项目、创建 User Project 链接

```bash
npm --prefix <skill-dir> run setup -- workspace \
  --workspace "$WORKSPACE" \
  --example /absolute/path/to/example-a \
  --example retrofit=/absolute/path/to/example-b \
  --user-project /absolute/path/to/user-project
```

`--example` 可以重复。若不写 `name=`，使用源目录名。

### 2. AI 检查或初始化 User Project

- User Project 不存在或为空：根据目标和 Example Project 创建最小可编译、可运行骨架。
- 已有内容：先分析并保留。
- 重要方案变化先征求用户意见。

### 3. 建立 User Project Git 教学基线

```bash
npm --prefix <skill-dir> run setup -- user-baseline --workspace "$WORKSPACE"
```

会确保独立 Git 仓库，并执行一次 `git add -A`，不创建 Commit。

### 4. 用户确认 Agent Project 名称后初始化

```bash
npm --prefix <skill-dir> run setup -- agent-init --workspace "$WORKSPACE" --agent-name my-product
```

Agent Project 从 User Project 当前工作树复制，不复制 `.git`，并创建独立 initial Commit。若 Git 身份未配置，先按提示自行配置，再重新运行命令。

## Review 工作流

为当前步骤建立文件清单，例如 `workspace/.teaching/review/current-step-paths.json`：

```json
[
  "src/config/ConfigLoader.java",
  "src/config/ConfigOptions.java"
]
```

执行自动比较：

```bash
npm --prefix <skill-dir> run review-diff -- review \
  --user-dir "$USER_PROJECT" \
  --agent-dir "$AGENT_PROJECT" \
  --workspace "$WORKSPACE" \
  --agent-commit <agent-commit-sha> \
  --paths-file "$WORKSPACE/.teaching/review/current-step-paths.json"
```

根据结果：

- `EXACT_MATCH`：必要构建和测试后，提示用户精确 `git add`。
- `DIFFERENT`：AI 审查差异，确认正确后提示用户精确 `git add`。
- `BASELINE_MISMATCH`：停止自动判断，AI 分析基线差异。

用户暂存后验证：

```bash
npm --prefix <skill-dir> run review-diff -- verify-staged \
  --user-dir "$USER_PROJECT" \
  --workspace "$WORKSPACE"
```

验证通过后吸收用户实现：

```bash
npm --prefix <skill-dir> run sync-user -- absorb \
  --user-dir "$USER_PROJECT" \
  --agent-dir "$AGENT_PROJECT" \
  --workspace "$WORKSPACE"
```

AI 更新开发计划、日志和状态，再使用 `checkpoint.ts` 创建 `[user]` Commit。

## 用户直接接受 Agent 实现

用户明确要求直接移植时：

```bash
npm --prefix <skill-dir> run sync-user -- apply-agent \
  --user-dir "$USER_PROJECT" \
  --agent-dir "$AGENT_PROJECT" \
  --workspace "$WORKSPACE" \
  --agent-commit <agent-commit-sha> \
  --paths-file "$WORKSPACE/.teaching/review/current-step-paths.json"
```

默认会备份并清理 User Project 未暂存工作区，保留 Index，然后使用 `git apply --index`。用户明确要求保留半成品时，不要运行此命令，先备份并由 AI 分析合并。

## 工作区备份

```bash
# 只备份
npm --prefix <skill-dir> run backup-worktree -- create --user-dir "$USER_PROJECT" --workspace "$WORKSPACE"

# 备份后清理未暂存工作区（保留 Index）
npm --prefix <skill-dir> run backup-worktree -- clear --user-dir "$USER_PROJECT" --workspace "$WORKSPACE"

# 恢复最新备份
npm --prefix <skill-dir> run backup-worktree -- restore --user-dir "$USER_PROJECT" --workspace "$WORKSPACE"
```

只保留一份最新备份，内容为未暂存的已跟踪修改和未跟踪且未忽略文件。

## Agent Project Commit

```bash
npm --prefix <skill-dir> run checkpoint -- \
  --agent-dir "$AGENT_PROJECT" \
  --source agent \
  --type feat \
  --scope config \
  --message "添加配置文件读取" \
  --paths-file "$WORKSPACE/.teaching/commit-paths.json"
```

提交前脚本要求 Agent Project Index 干净，只暂存指定文件。

## 注意

- `requirements.md` 是已确认的需求基线；修改 Skill 或脚本前先对照它。
- `references/AGENTS.md` 是 workspace 级行为约束。已有 `workspace/AGENTS.md` 不会被覆盖。
- 所有 User Project 写操作都必须来自初始化例外或用户明确授权的移植/合并流程。
- Example Project 永远只读。

> 提交约束：任何发生修改的 `docs/development-plan.md`、`docs/development-log.md`、`docs/teaching-state.md` 必须与当步代码一同列入 `checkpoint.ts --paths-file`。
