# hands-on-teaching-development

一个通过 Example Project、User Project、Agent Project 三项目协作来手把手实现目标产品的 Agent Skill。

## 核心能力

- **批量开发与双游标队列**：Agent 可在一个模块内连续准备多个小粒度 `[agent]` Commit，运行 `progress.ts prepare --auto` 直接从 Git 收集并登记为教学队列；User Project 按队列逐 Commit 实现或接受，跨会话通过 `progress.ts status` 读取双游标与 Review/备份状态。
- **内联差异审计与选型门禁**：`review-diff.ts review` 自动复用队列当前 Commit 与文件路径，一次 JSON 返回 `diffs.userVsAgent`、`diffs.userPatch`、`diffs.agentPatch` 与 `whitespaceOnlyCandidates`（超大差异显式标记截断）。`EXACT_MATCH` 构建测试后由 `stage-reviewed` 精确暂存并直接进入提交记录；`DIFFERENT` 须经 Agent 审计（实质差异对比双方优劣并给出明确推荐，纯风格差异简短说明）并由用户确认后运行 `decide`，选 `user` 才允许暂存和吸收，选 `agent` 则走 `sync-user.ts apply-agent` 备份并应用 Patch。
- **分工清晰的教学文档**：`docs/MISSION.md` 记录产品动机、学习目标与边界，`docs/RESOURCES.md` 索引多个示例项目的版本与代码定位，`docs/NOTES.md` 由 Agent 直接以 Markdown 自由维护经用户确认的长期约定与纠偏，`docs/development-log.md` 记录逐步审查与提交事实。
- **自由维护的短期教学提示**：在 `teaching-state.md` 自由区块按实际观察记录短期讲解重点或后续步骤粒度，不引入独立学习档案、能力评分或僵硬枚举，不自动重写已准备的 Commit。

## 文件结构

```text
hands-on-teaching-development/
├── SKILL.md                 # Skill 主流程
├── requirements.md          # 用户确认的需求基线
├── package.json
├── tsconfig.json
├── agents/
│   └── openai.yaml          # ChatGPT Skill 展示与调用策略元数据
├── references/
│   ├── AGENTS.md            # 首次启动复制到 workspace/AGENTS.md
│   ├── batch-and-progress.md
│   └── review-and-teaching.md
├── templates/
│   ├── development-plan.md
│   ├── development-log.md
│   ├── teaching-state.md
│   ├── NOTES.md
│   ├── MISSION.md
│   └── RESOURCES.md
├── tests/
│   ├── audit-regressions.test.mjs
│   ├── batch-progress.test.mjs
│   ├── review-choice.test.mjs
│   └── mission-resource-setup.test.mjs
└── scripts/
    ├── doctor.mjs
    ├── run.mjs
    ├── common.ts
    ├── setup.ts
    ├── copy-project.ts
    ├── progress.ts
    ├── review-diff.ts
    ├── backup-worktree.ts
    ├── sync-user.ts
    └── checkpoint.ts
```

## 环境要求

- Node.js 22.6+（免安装原生运行 TypeScript）或 Node.js 20.0–22.5（需 `npm install` 安装 `tsx`）
- Git 2.30+

环境自检与测试：

```bash
npm --prefix <skill-dir> run doctor
npm --prefix <skill-dir> test
```

`npm run setup` 会先进行无依赖自检。在 Node.js 22.6+ 下无需在 Skill 目录执行 `npm install`，直接从任意工作目录通过 `npm --prefix <skill-dir> run <script> -- ...` 调用脚本即可；仅在 Node.js 20.0–22.5 或需要运行 `npm run typecheck` 时才需在 Skill 目录安装依赖。

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

执行自动比较（激活教学队列时自动取当前待学 Commit 与文件路径，无需每次手写路径 JSON；如需指定或扩展范围，可传 `--agent-commit`、`--path` 或 `--paths-file`）：

```bash
npm --prefix <skill-dir> run review-diff -- review \
  --user-dir "$USER_PROJECT" \
  --agent-dir "$AGENT_PROJECT" \
  --workspace "$WORKSPACE"
```

存在差异（`DIFFERENT`）时，先根据首次 JSON 输出审计并向用户说明推荐与理由，经用户明确选择后再执行（`--recommend` 与 `--inspiration` 可选）：

```bash
npm --prefix <skill-dir> run review-diff -- decide \
  --workspace "$WORKSPACE" --choice user --recommend user \
  --reason "用户已确认采用自写方案"
```

根据结果：

- `EXACT_MATCH`：必要构建/测试通过后，由 Agent 调用 `review-diff.ts stage-reviewed` 精确暂存，无需再跑 `absorb`，直接推进进度并提交 `[user]` 文档记录。
- `DIFFERENT`：根据首次 JSON 输出审计两种方案；实质差异对比双方优劣并提出明确推荐与理由（纯风格差异可简短说明），经用户确认后运行 `decide`。选 `user` 后精确暂存并吸收（吸收须在修改 Agent 文档前执行）；选 `agent` 后按备份和 Patch 流程替换未暂存代码。
- `BASELINE_MISMATCH`：停止自动判断，AI 分析基线差异。

精确暂存（已内置暂存后校验；仅在用户手工 `git add` 或跨轮复核时需额外运行 `verify-staged`）：

```bash
npm --prefix <skill-dir> run review-diff -- stage-reviewed \
  --user-dir "$USER_PROJECT" --workspace "$WORKSPACE"

# 可选：用户手工暂存或跨轮复核时使用
npm --prefix <skill-dir> run review-diff -- verify-staged \
  --user-dir "$USER_PROJECT" --workspace "$WORKSPACE"
```

仅 `DIFFERENT` 且确认 `--choice user` 时吸收用户实现：

```bash
npm --prefix <skill-dir> run sync-user -- absorb \
  --user-dir "$USER_PROJECT" \
  --agent-dir "$AGENT_PROJECT" \
  --workspace "$WORKSPACE"
```

AI 更新开发计划、日志和状态，再使用 `checkpoint.ts` 创建 `[user]` Commit。

## 用户直接接受 Agent 实现

用户明确要求直接移植时（激活教学队列时自动复用当前待学 Commit 与文件路径；无队列时传入 `--agent-commit <sha>`）：

```bash
npm --prefix <skill-dir> run sync-user -- apply-agent \
  --user-dir "$USER_PROJECT" \
  --agent-dir "$AGENT_PROJECT" \
  --workspace "$WORKSPACE"
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
  --path src/config.ts \
  --path docs/development-plan.md \
  --path docs/development-log.md
```

提交前脚本要求 Agent Project Index 干净，只暂存通过 `--path` 或 `--paths-file` 明确指定的文件。

## 注意

- `requirements.md` 是已确认的需求基线；修改 Skill 或脚本前先对照它。
- `references/AGENTS.md` 是 workspace 级行为约束。已有 `workspace/AGENTS.md` 不会被覆盖。
- 所有 User Project 写操作都必须来自初始化例外或用户明确授权的移植/合并流程。
- Example Project 永远只读。

> 提交约束：任何发生修改的 `docs/development-plan.md`、`docs/development-log.md`、`docs/teaching-state.md`、`docs/NOTES.md`、`docs/MISSION.md`、`docs/RESOURCES.md` 必须与当步代码一同纳入 `checkpoint.ts`（`--path` 或 `--paths-file`）。


## 批量开发与本地进度

Agent 可以在一个功能内连续提交多个 `[agent]` Commit，然后把它们登记成教学队列；用户在 User Project 中逐 Commit 手写/Review/接受。Agent 领先用户几个 Commit 是正常状态。详见 `references/batch-and-progress.md`。

```bash
# 只读查询：Agent 进度、User 进度、下一步骤与 Review/备份状态
npm --prefix <skill-dir> run progress -- status --agent-dir "$AGENT_PROJECT"

# 自动从 Git 收集未登记的 [agent] 代码提交并登记教学队列（也支持 --steps-file 自定义）
npm --prefix <skill-dir> run progress -- prepare \
  --agent-dir "$AGENT_PROJECT" --batch-id config-v1 --module config --auto

# 用户通过 Review 或直接移植后，更新当前 User 游标（须随 [user] Commit 提交）
npm --prefix <skill-dir> run progress -- complete \
  --agent-dir "$AGENT_PROJECT" --mode handwritten --review EXACT_MATCH

# 若误推进了游标，可回退到当前批次的指定步骤
npm --prefix <skill-dir> run progress -- rewind \
  --agent-dir "$AGENT_PROJECT" --to-step S001
```

**旧项目迁移**：对尚无结构化索引的旧 `teaching-state.md`，先运行 `progress.ts init` 添加索引；原内容保留，需对照 Git 历史人工确认游标，不自动推断。已有项目也不会被覆盖原有文档或 `workspace/AGENTS.md`。

## 文档分工与自由维护

首次创建 Agent Project 时会包含 `docs/NOTES.md`、`docs/MISSION.md` 与 `docs/RESOURCES.md` 模板，由 Agent 按需直接编辑并随 `checkpoint.ts` 提交。其中 `NOTES.md` 与 `teaching-state.md` 的短期教学提示区块均不绑定脚本格式，可按项目特点自由组织；不引入独立学习记录档案或能力评分，以项目开发为中心。
