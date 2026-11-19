# Teaching State

> 当前状态快照，用于跨会话恢复。历史过程写入 `development-log.md`，整体计划写入 `development-plan.md`。

## 模式状态

- **状态**：inactive
- **最近更新时间**：待填写
- **当前阶段**：初始化

## 项目路径

- **Workspace**：待填写
- **Example Projects**：待填写
- **User Project 链接**：`user-project`
- **User Project 实际路径**：待填写
- **Agent Project**：待填写

## 计划锚点

- **计划文件**：`docs/development-plan.md`
- **当前模块**：无
- **当前步骤**：无
- **最近完成步骤**：无
- **下一步**：等待制定计划

## 当前步骤状态

- **步骤 ID / 标题**：无
- **Agent Commit**：无
- **本步骤涉及文件**：`[]`
- **用户实现状态**：未开始
- **Review 状态**：未开始
- **Review 快照**：无
- **待用户暂存**：否
- **待同步到 Agent Project**：否
- **待创建 [user] Commit**：否

## 安全与恢复状态

- **最近工作区备份**：无
- **备份路径**：`workspace/.teaching/backup/current`
- **是否存在待恢复备份**：否
- **是否存在未完成 Patch 应用**：否
- **阻塞 / 待用户决策**：无

## 恢复检查清单

- [ ] 检查 Agent Project `git status --short`
- [ ] 检查 User Project `git status --short`
- [ ] 检查当前步骤对应的 Agent Commit
- [ ] 检查 Review 快照是否仍有效
- [ ] 检查是否存在待处理工作区备份
- [ ] 确认后续动作不会覆盖用户未同步的内容
