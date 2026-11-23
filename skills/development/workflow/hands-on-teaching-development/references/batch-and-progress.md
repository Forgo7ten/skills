# 批量开发、逐 Commit 教学与本地进度索引

## 双游标模型

- Agent 游标：Agent Project 最新完成的实现提交，以及已准备的批次队列。
- User 游标：用户已完成的教学步骤、当前待学的 `[agent]` Commit、通过的 Review 状态。
- **不要把 `git log -1` 或 `HEAD` 直接当作用户当前任务。** Agent 可以领先多个 Commit，`[user]` 文档检查点也可能出现在预先准备的实现提交之后。
- 一次 Agent 开发可以准备一个完整功能/模块，但仍要分成有意义的小 Commit。只有**批次完成后**才停止，按队列引导用户逐步学习。

## 首次初始化 / 旧项目升级

```bash
npm --prefix <skill-dir> run progress -- init --agent-dir <Agent Project>
npm --prefix <skill-dir> run progress -- status --agent-dir <Agent Project> --json
```

`init` 不删除旧 `teaching-state.md` 内容。旧版非结构化进度会原样留在迁移区域，需对照 Git 历史人工登记当前进度。**不要自行推断已完成步骤。**

## Agent 批次准备

Agent 在一个模块中可以连续实现、测试并分别提交，例如：

```text
A1  [agent] feat(config): 定义配置模型
A2  [agent] feat(config): 加载配置文件
A3  [agent] test(config): 覆盖默认配置与错误路径
```

直接从 Git 历史自动收集未登记的 `[agent]` 代码提交并登记队列（无需手写临时 JSON；默认自动跳过已完成批次与最近 `[user]` 检查点之前的提交，如需显式指定范围可加 `--from <基线SHA>` / `--to <目标Rev>`）：

```bash
npm --prefix <skill-dir> run progress -- prepare \
  --agent-dir <Agent Project> --batch-id config-v1 \
  --module config --auto
```

如需自定义步骤编号、标题或文件范围，也可改用 `--steps-file <steps.json>`（其中 `paths` 可省略，默认取对应提交的非文档文件）：

```json
[
  {"id":"S001","commit":"<A1>","title":"配置数据模型","paths":["src/config.ts"]},
  {"id":"S002","commit":"<A2>","title":"配置文件加载","paths":["src/config.ts"]},
  {"id":"S003","commit":"<A3>","title":"异常路径测试","paths":["tests/config.test.ts"]}
]
```

然后把更新的 `docs/teaching-state.md` 与开发计划、批次日志一起提交 `[agent] docs(config): 登记配置模块教学队列`。

## 用户逐步学习

1. 执行 `progress.ts status`，确认 `currentUserStep` 的 Commit 和文件列表。
2. 直接调用 `review-diff.ts review --user-dir <User> --agent-dir <Agent> --workspace <Workspace>`（激活队列时自动复用当前待学 Commit 与文件路径，无需每次手写路径 JSON）。
3. User 工作树实现 → 自动 Diff（一次返回具体差异）→ 有差异则 Agent 审计并问用户选择（通过 `decide` 记录）→ `review-diff.ts stage-reviewed` 精确暂存并校验（仅手工 `git add` 或跨轮复核时再单独运行 `verify-staged`）。用户选择教学 Commit 时改用 `sync-user.ts apply-agent`，不走用户实现吸收。
4. `EXACT_MATCH` 在 `stage-reviewed` 成功后**无需运行 `absorb`**，直接进入下一步；仅 `DIFFERENT` 且 `decide --choice user` 时在修改文档前运行 `sync-user.ts absorb`。
5. 通过构建/测试后，先运行（`--step` 可省略，默认推进当前队首步骤）：

```bash
npm --prefix <skill-dir> run progress -- complete \
  --agent-dir <Agent Project> \
  --mode handwritten --review EXACT_MATCH
```

6. 补全开发日志，并用 `checkpoint.ts --path ...` 将本次状态、日志和吸收的代码一同提交为 `[user]` Commit；无需 User Project 每步 Commit。
7. 继续下一条 queued step。**不得**把 Agent 最新 HEAD 当作下一个用户步骤。

> 这不是让 User 跟着 Agent HEAD 走：如 Agent HEAD = A3，User 学 A1，脚本自动锁定 A1 的 SHA；`verify-staged` 只要求 A1 是当前历史祖先，不要求 HEAD == A1。

## 用户差异与提前开发冲突

- `EXACT_MATCH`：User 实现和 A1 完全一致。即便 Agent HEAD 已到 A3，也不得把 A1 文件拷回 HEAD，否则可能抹掉 A2/A3。
- `DIFFERENT` 且 User 修改的文件未被后续已准备的 Commit 触及：通过审查后可安全吸收，继续队列。
- `DIFFERENT` 且后续 Commit 触及同一文件：`sync-user.ts absorb` 必须拒绝覆盖。使用：

```bash
npm --prefix <skill-dir> run progress -- pause \
  --agent-dir <Agent Project> --reason "S001 的用户改动与 A2/A3 重叠"
```

- 由 Agent 审查冲突并与用户确认方向后，按以下闭环处理：
  1. 在 Agent Project 合入当前冲突步骤的用户实现（保留原 Git 历史）；
  2. 在暂停态运行 `progress.ts complete --agent-dir <Agent Project> --mode handwritten --review "DIFFERENT(user, conflict-reconciled)"` 推进该冲突步骤（剩余未学步骤仍保持 `paused`）；
  3. 更新开发日志并用 `checkpoint.ts --source user` 提交该步骤；
  4. 基于用户实现重新提交后续受影响的 `[agent]` Commit，再执行 `progress.ts prepare --batch-id <新批次> --module <模块> --replace-paused --auto` 登记新队列（自动扣减被作废的旧待学步数，编号单调递增不冲突）。
- 用户直接要求按 Agent 实现时，仍按 `sync-user.ts apply-agent` 的基线检查、备份、Patch 和回滚规则；一律只应用当前待学 Commit，不应用整个批次。

## 游标误推进时的恢复

- 若尚未创建 `[user]` Commit：直接 `git -C <Agent Project> restore docs/teaching-state.md` 即可还原。
- 若已提交或需将当前批次游标回退到某一步骤（例如误连续调用了 `complete`）：

```bash
npm --prefix <skill-dir> run progress -- rewind \
  --agent-dir <Agent Project> --to-step S001
```

`rewind` 会把指定步骤及之后重置为 `pending`、同步扣回 `user.completedSteps`，并将误结束的批次恢复为 `active`；核对 User Project 暂存区后随下一次 `checkpoint.ts` 提交修正即可（跨批次历史也可直接 `git restore --source=<旧Commit> -- docs/teaching-state.md`）。

## 本地需求/偏好/纠偏（`docs/NOTES.md`）

每次用户确认新的长期产品需求、工程/教学偏好、重要架构决定或纠正之前的方案，由 Agent 直接编辑 `docs/NOTES.md` 持久化，格式可按项目实际需要自由组织（例如按全局/模块分组、清单或表格），无需通过脚本维护固定 JSON：

- 新旧约定发生冲突时，在 `NOTES.md` 中明确标注旧条目已废止或移入历史变更区，避免冲突规则同时生效。
- `development-plan.md` 是规划，`development-log.md` 是发生过的事件与 Commit；不要求每轮全文阅读它们。
- 恢复教学时先运行 `progress.ts status` 读取双游标、批次队列与 Review/备份状态，再直接阅读精简的 `NOTES.md`；确实需要根因时才读取最近日志和相关 Commit。
- 修改 `NOTES.md` 或 `teaching-state.md` 后，纳入最近一次 Agent Project 的精确文件检查点，不留下只存在于某次会话的决策。

## 教学反馈不需要学习档案

- 短期讲解深度、后续未准备步骤的拆解重点由 Agent 自由记录在 `docs/teaching-state.md` 的 Markdown 区块，不绑定固定枚举，也不是用户能力评分。
- 下次恢复使用 `progress.ts status` 读取双游标与批次队列，并直接查阅 `NOTES.md` 中的长期约定和 `teaching-state.md` 中的短期提示。
- 只在后续**未准备的批次**调整 Commit 粒度；已准备的教学提交不凭推测重新拆分或重写。
- 学到的有用原理可以作为本步 Review 注记写入 `development-log.md`，但不额外建立纯学习式 `learning-records/` 体系。
- Mission/Resources/NOTES 的分工见 [review-and-teaching.md](review-and-teaching.md)。
