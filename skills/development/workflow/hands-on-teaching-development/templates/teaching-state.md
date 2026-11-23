# Teaching State

> 以下 JSON 区块仅由 `scripts/progress.ts` 管理（双游标与批次队列）。跨会话先运行 `progress.ts status`，再按需阅读 `NOTES.md` 及其他文档。

<!-- teaching-state-index:start -->
{
  "version": 2,
  "updatedAt": "",
  "agent": {
    "latestPreparedCommit": null,
    "preparedSteps": 0
  },
  "user": {
    "completedSteps": 0,
    "lastCompletedStep": null
  },
  "batch": null
}
<!-- teaching-state-index:end -->

## 短期教学提示（Agent 自由维护，可选）

<!-- 按开发过程中的实际观察自由记录当前阶段的讲解深度、后续未准备步骤的拆分粒度或需要重点说明的机制；不是对用户能力的永久评价，不因此改写已提交的教学 Commit。 -->

## 安全与恢复提醒

- 当前状态区块与 Git 历史必须交叉验证；发现无法对齐的情况应先停下检查。
- Review 快照位于 `workspace/.teaching/review/latest.json`，工作区备份位于 `workspace/.teaching/backup/current/`。
- 对于尚无结构化索引的旧项目，先使用 `progress.ts init` 迁移，再人工核对旧进度和当前 Git Commit，不得自动猜测。
