# Agent Skills Toolbox

基于 **独立 Skill + Plugin Grouping** 架构的个人跨 Agent Skills 仓库，支持通过 Vercel Labs `npx skills` CLI 按分类选择、独立安装与更新。

## 分类概览

| 分类路径 | Plugin Group | 状态 | 说明 |
|---|---|---|---|
| `skills/platform/macos/` | `platform-macos` | 预留 | macOS 平台相关 Skill |
| `skills/platform/linux/` | `platform-linux` | 预留 | Linux 平台相关 Skill |
| `skills/platform/windows/` | `platform-windows` | 预留 | Windows 平台相关 Skill |
| `skills/development/docs/` | `development-docs` | 已生成 | 开发文档与图表工具相关 Skill |
| `skills/development/workflow/` | `development-workflow` | 已生成 | 开发工作流与教学开发相关 Skill |

> 分类与分组可在根目录 [manifest.json](manifest.json) 与 `skills/` 中按需扩展。

## 安装方式

```bash
# 查看仓库中的所有 Skill
npx skills add <owner>/<repo> --list

# 交互式按分类分组选择 Skill
npx skills add <owner>/<repo>

# 安装到指定 Agent（如 Claude Code / Codex / OpenCode）
npx skills add <owner>/<repo> -a claude-code
npx skills add <owner>/<repo> -a codex
npx skills add <owner>/<repo> -a claude-code -a codex -a opencode

# 非交互安装指定 Skill
npx skills add <owner>/<repo> --skill <skill-name> -a claude-code

# 非交互安装全部 Skill
npx skills add <owner>/<repo> --skill '*' -a claude-code
```

> **说明**：Plugin Grouping（`.claude-plugin/marketplace.json`）仅用于交互安装时的分组展示与多选，不提供 `--group` 参数，也不参与 Agent 运行时路由；每个 Skill 均可独立安装使用。

## 更新方式

```bash
npx skills update
```

## 支持的 Agent

兼容支持通用 Agent Skills 规范的客户端，包括但不限于：

- Claude Code
- Codex
- OpenCode
- Antigravity

## 使用注意事项

- `npx skills` CLI 不会根据 `platform/` 目录自动过滤当前操作系统不适用的 Skill，安装与使用时请留意目标 Skill 的环境要求。
- 严禁在 Skill 中包含凭据、Token 或私有配置等敏感文件。

## 文档索引

- [架构设计与约束规范](docs/architecture.md)：了解三权分立原则、目录规范、Skill 独立性、Plugin Grouping 机制与设计红线。
- [开发与维护指南](docs/development.md)：了解本地开发环境（Node / pnpm）、新增 Skill 流程、Git Hook / CI 自动化与本地测试方法。
