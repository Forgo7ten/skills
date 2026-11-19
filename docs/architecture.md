# Agent Skills 架构设计与约束规范

## 1. 核心架构与设计哲学：三权分立

本仓库采用 **独立 Skill + Plugin Grouping** 架构。为了保证跨不同 Agent 工具（Claude Code、Codex、OpenCode、Antigravity 等）的通用性与可维护性，遵循以下**三权分立**原则：

1. **仓库目录结构 (`skills/<category>/<skill-name>/` 或 `skills/<category>/<subcategory>/<skill-name>/`)**：仅负责源码在仓库内的组织归类与日常人类维护，不作为 Agent 运行时的路由依据。
2. **Plugin Grouping (`.claude-plugin/marketplace.json`)**：仅用于 `npx skills` 在交互安装界面中提供分类折叠与批量选择体验，**严禁**设计为父级 Skill 管理子 Skill 的运行时路由系统。
3. **独立 Skill (`SKILL.md` + 自包含资源)**：负责具体的功能实现与运行时能力。每个 Skill 必须支持单独安装、单独调用与单独更新。

---

## 2. 目录结构与层级规范

### 2.1 分类目录规范

所有 Skill 均存放于分类目录下，支持两级目录（默认）或三级目录（按需细分子分类）：

- 两级结构：`skills/<category>/<skill-name>/`（如 `skills/standards/git-commit-message/`）
- 三级结构：`skills/<category>/<subcategory>/<skill-name>/`（如 `skills/development/docs/check-mermaid/`、`skills/platform/macos/macos-launch-agent/`）

### 2.2 Skill 内部资源组织

```text
skills/<category>/[<subcategory>/]<skill-name>/
├── SKILL.md          # 必需：核心流程、规则与说明
├── references/       # 可选：详细参考文档、命令手册（按需读取）
├── scripts/          # 可选：确定性执行脚本、辅助工具
└── assets/           # 可选：模板文件、静态图片或示意图
```

### 2.3 目录硬性约束

1. **分类归属**：每个独立 Skill 必须位于分类目录下（两级或三级），禁止直接平铺在 `skills/` 一级根目录。
2. **禁止路由 SKILL.md**：严禁在分类目录（如 `skills/standards/` 或 `skills/platform/macos/`）直接放置 `SKILL.md` 作为路由入口。
3. **禁止嵌套 Skill**：严禁在一个 Skill 目录内部再嵌套其他独立 Skill。
4. **分类目录文件限制**：分类目录中允许放置仅供人类阅读的 `README.md`，但不作为 Agent 自动加载的 Skill；禁止放置资源目录（`references/` 等）。
5. **禁用符号链接**：Skill 源码树内禁止使用符号链接（Symlink），以确保跨平台安装和打包的可靠性。

---

## 3. 独立 Skill 设计规范

### 3.1 单一职责与自包含性

- **一个功能对应一个独立 Skill**：严禁将多个无关功能打包进同一个大型 Skill。
- **完全自包含**：每个 Skill 必须在脱离本仓库其他文件时仍能独立运行。
- **禁止跨 Skill 引用**：禁止 Skill A 读取 Skill B 内部的 references/scripts；禁止通过 `../../` 访问仓库根目录文件。公共知识应内聚到本 Skill 内，或抽取为真正独立的通用 Skill。
- **平铺安装影响**：CLI 在安装时会以 Skill 的 `name` 作为目标目录名（例如安装至 `.claude/skills/<name>/`），仓库内的分类路径在安装后不复存在。因此所有内部相对引用必须相对于本 Skill 目录。

### 3.2 SKILL.md Frontmatter 规范

每个 `SKILL.md` 开头必须包含标准 YAML 1.2 frontmatter：

```yaml
---
name: check-mermaid
description: >
  Create, review, repair, or validate Mermaid diagrams embedded in Markdown documents.
  Use when a task adds or changes Mermaid diagrams, when the user asks to validate them.
---
```

- **`name`**：
  - 全仓库全局唯一；
  - 必须使用小写 `kebab-case`（`^[a-z0-9]+(?:-[a-z0-9]+)*$`）；
  - 必须与所在目录名 `<skill-name>` 严格一致。
- **`description`**：
  - 准确说明功能、适用场景与触发条件，避免过于宽泛导致多 Skill 竞争同一意图；
  - 平台专属 Skill 必须在描述中明确适用的操作系统。
- **严格 YAML 解析**：仓库构建工具采用标准 YAML 1.2 解析器。禁止重复键、非法格式或非对象 frontmatter。
- **禁止伪 platform 字段**：不要依赖未被标准规范支持的 `platforms` frontmatter 字段，CLI 不会据此做运行时环境过滤。

### 3.3 Skill 内容推荐模板

```markdown
---
name: your-skill-name
description: Clear explanation of what the skill does and when to trigger it.
---

# Skill Name

## Purpose
说明 Skill 的职责。

## When to Use
明确说明触发场景与条件。

## Environment Requirements
说明操作系统、运行时工具及外部依赖要求。

## Workflow
具体执行步骤与决策分支。

## References
说明何时按需读取 references/ 中的哪些资料。

## Scripts
说明脚本用途、参数、调用方式与退出码。

## Constraints
列出禁止事项与边界条件。
```

### 3.4 References 与 Scripts 按需加载策略

- `SKILL.md` 应保持精简，保留核心工作流与关键规则。
- 详细参考、语法手册放入 `references/`，并在 `SKILL.md` 中指明**按需读取**条件，防止任务无关时将海量参考资料全部载入上下文。
- 确定性逻辑放入 `scripts/`，由 Agent 或用户按需调用。

---

## 4. 平台专属与跨平台规范

### 4.1 CLI 不自动做平台过滤

`npx skills` 在任意系统（如 Windows）上均可检索到全平台 Skill，不会因为路径包含 `macos/` 就自动隐藏或拦截。因此，平台专属 Skill 必须提供三层防护：

1. **目录与分组隔离**：存放于 `skills/platform/<os>/` 并映射到对应 `platform-<os>` 分组。
2. **描述与文档声明**：在 `description` 与 `Environment Requirements` 中明确声明目标系统。
3. **运行时控制**：具体执行保护与平台判断由 Skill 自身实现或用户在安装使用阶段控制，校验脚本不做静态 OS 关键词强制检查。

### 4.2 跨平台 Skill 要求

- 明确区分 macOS / Linux / Windows 命令差异。
- 脚本尽量采用跨平台运行时（如 Node.js / TypeScript、Python 等），避免直接调用某一系统特有的外部工具。

---

## 5. Plugin Grouping 与配置中心规范

### 5.1 单一输入源原则

- **[manifest.json](../manifest.json)**：仓库的**唯一配置源**，包含顶层元数据（`name`、`owner`、`metadata`）以及分组描述字典 `groups: { "<group-name>": "<description>" }`。
- **[.claude-plugin/marketplace.json](../.claude-plugin/marketplace.json)**：**纯生成文件**，禁止手动修改。该文件完全由同步脚本结合 `manifest.json` 与 `skills/` 扫描结果派生，不读取旧文件作为兜底。

### 5.2 Grouping 映射规则

- **Plugin Group ID**：二级目录由 `<category>` 派生（例如 `skills/standards/` -> `standards`），三级目录由 `<category>-<subcategory>` 自动派生（例如 `skills/development/docs/` -> `development-docs`）。
- **ID 冲突检测**：若目录出现产生相同 Group ID 的情况（如 `a-b/c` 与 `a/b-c`），同步与校验脚本会明确报错阻断。
- **映射对象**：每个 Plugin 的 `skills` 数组必须指向具体的 Skill 目录（`./skills/<category>/<skill-name>` 或 `./skills/<category>/<subcategory>/<skill-name>`），严禁只填分类目录。
- **完全覆盖**：仓库中所有有效 Skill 必须且只能属于一个分组，不允许遗漏或产生未分类的 `Other` 分组。

---

## 6. 自动化工具链与校验规范

项目通过 `package.json`（固定 `packageManager: pnpm@11.4.0`，约束 `engines.pnpm: ">=11.4.0 <12"`）与 `pnpm-lock.yaml` 管理开发与校验工具链：

### 6.1 参数归一化工具 (`scripts/lib/cli-args.mjs`)

- `normalizeScriptArgs(argv)`：用于安全去除包管理器透传的前导可选 `--` 分隔符，同时保留后续普通参数。确保 `pnpm run <script> --check` 与 `pnpm run <script> -- --check` 均能被 CLI 脚本稳定解析。

### 6.2 同步工具 (`scripts/sync-marketplace.mjs`)

- 递归扫描 `skills/` 目录，结合 `manifest.json` 自动生成 `.claude-plugin/marketplace.json`。
- 输出经过稳定字典序排序，保证无变动时结果幂等、零无意义 diff。
- 采用临时文件 + 原子重命名（Atomic Write），防止中断导致文件损坏。
- 支持 `--check` 选项：以只读模式比较当前文件与预期内容，用于 CI 和一致性检查。
- 通过 `normalizeScriptArgs` 兼容 `pnpm run skills:sync --check` 与 `pnpm run skills:sync -- --check` 参数格式。

### 6.3 校验工具 (`scripts/validate-skills.mjs`)

通过共享的生成模型对仓库执行轻量静态检查（聚焦目录结构、YAML Frontmatter、Manifest 与本地链接基本格式）：
1. **目录规范**：两级或三级规范目录层级、无嵌套 Skill、无分类级 SKILL.md、无软链接。
2. **Frontmatter**：标准 YAML 1.2 解析无语法错误、`name` 符合 kebab-case 且与目录同名、全局 `name` 唯一性、`description` 存在性。
3. **Marketplace 一致性**：生成的 `marketplace.json` 必须与 `manifest.json` + `skills/` 的派生结构严格深度相等（`isDeepStrictEqual`）。
4. **Markdown 本地链接与真实路径边界**：检查 Skill 内 Markdown 文件的本地链接（跳过 `templates/` 与 `tests/` 子目录，以及围栏代码块与行内代码），通过统一的真实路径解析函数（`realpathSync`）验证目标存在且不逃逸出当前 Skill 目录。

### 6.4 pnpm 版本策略与包管理器约束

- `packageManager` 声明 `pnpm@11.4.0`，供 Corepack 识别与对齐；
- `engines.node` 要求 `>=22.16.0`；
- `engines.pnpm` 声明 `>=11.4.0 <12`，约束在 pnpm 11 大版本生态；
- GitHub Actions CI 使用 `pnpm/action-setup@v6` 锁定 `version: 11.4.0`；
- `pnpm-lock.yaml` 使用 lockfile v9.0 格式，pnpm 11.4.0 原生兼容。

---

## 7. Git Hook 与 CI 门禁体系

### 7.1 本地 Pre-commit Hook

- Hook 文件位于 [.githooks/pre-commit](../.githooks/pre-commit)，不引入 Husky / Lefthook 等第三方框架。
- 通过 `pnpm run hooks:install` 配置本仓库本地 `core.hooksPath=.githooks`，不污染全局 Git 配置；提供 `pnpm run hooks:uninstall` 供卸载。
- **触发与安全策略**：
  1. 仅在暂存区涉及 `skills/`、`manifest.json` 或 `marketplace.json` 时运行；非 Skill 变更快速跳过。
  2. 严格拦截 `skills/` 或 `manifest.json` 存在未暂存修改的情况，防止生成物与提交物不一致。
  3. 严格拦截 `skills/` 下存在未跟踪文件的情况，防止漏提资源。
  4. 自动执行 `pnpm run skills:sync` 重新生成 marketplace 并自动执行 `git add -- .claude-plugin/marketplace.json`。
  5. 自动执行 `pnpm run skills:check`，失败则阻止本次提交。

### 7.2 GitHub Actions CI

- 配置文件位于 [.github/workflows/ci.yml](../.github/workflows/ci.yml)，在 push 和 pull request 时自动执行。
- 使用 `pnpm install --frozen-lockfile` 安装锁定依赖。
- 运行 `pnpm run skills:sync -- --check`，以只读方式确保提交的 `marketplace.json` 未发生漂移。
- 运行 `pnpm run skills:check` 与 `pnpm test`。
- CI 是不可绕过的最终质量门禁。

---

## 8. 本地测试与多 Agent 验收流程

在将 Skill 正式发布或合并前，必须完成以下验收步骤：

```bash
# 1. 静态同步与一致性检查
pnpm run skills:sync -- --check
pnpm run skills:check

# 2. 自动化测试套件
pnpm test

# 3. 本地 CLI 发现检查
npx skills add . --list

# 4. 独立临时目录隔离安装测试（验证复制与自包含性）
TEST_DIR="$(mktemp -d)"
cd "$TEST_DIR"
npx skills add /absolute/path/to/skills --skill <skill-name> -a claude-code --copy
npx skills list
# 确认安装后包含完整的 SKILL.md、references/、scripts/，且脱离原仓库可独立使用
rm -rf "$TEST_DIR"
```

针对支持通用 Agent Skills 的客户端（Claude Code、Codex、OpenCode、Antigravity），验证其能够正确加载指令并执行脚本。

---

## 9. 架构设计禁止红线（12 条绝对禁令）

1. **禁止合并大包**：不得将全部功能合并成一个单一庞大的 `SKILL.md`。
2. **禁止父级路由**：不得使用分类目录的 `SKILL.md` 作为子 Skill 的路由调度入口。
3. **禁止依赖分组路由**：不得将 Plugin Grouping 用于运行时逻辑分支，它仅负责安装展示。
4. **禁止跨 Skill 依赖**：不得让一个 Skill 依赖读取另一个 Skill 内部的未公开资源。
5. **禁止根目录平铺**：不得把所有 Skill 直接堆在 `skills/` 一级目录下，必须放置在分类目录中（支持二级或三级）。
6. **禁止重名**：全仓库内 Skill `name` 必须全局唯一，不同分类下禁止出现同名 Skill。
7. **禁止在 Manifest 填分类目录**：Manifest 的 `skills` 数组只能填写具体 Skill 的完整相对路径。
8. **禁止手工编辑 Marketplace**：不得手工维护 `.claude-plugin/marketplace.json`，必须通过脚本从 `manifest.json` 自动派生。
9. **禁止假设平台过滤**：不得假设 `npx skills` 会自动根据操作系统屏蔽不兼容的 Skill。
10. **禁止破坏通用规范**：不得为了特定 Agent 的私有特性破坏通用 Agent Skills 格式。
11. **禁止未验证删除/覆盖**：重构时必须在确认功能等效并备份后才能迁移，禁止盲目覆写。
12. **禁止静默修改用户环境**：不得在未提示的情况下自动向全局安装 Skill 或修改用户的外部 Agent 配置。

---

## 10. 官方参考依据

- [Vercel Skills CLI 仓库](https://github.com/vercel-labs/skills)
- [Creating Skills 官方指南](https://github.com/vercel-labs/skills/blob/main/docs/creating-skills.md)
- [CLI Commands 参考](https://github.com/vercel-labs/skills/blob/main/docs/commands.md)
- [Supported Agents 兼容性列表](https://github.com/vercel-labs/skills/blob/main/docs/agents.md)
- [Plugin Grouping 实现源码](https://github.com/vercel-labs/skills/blob/main/src/plugin-manifest.ts)
- [Skill Discovery 递归发现机制](https://github.com/vercel-labs/skills/blob/main/src/skills.ts)
