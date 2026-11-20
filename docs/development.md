# 开发与维护指南

本文档面向仓库维护者与 Skill 开发者，说明本地环境初始化、目录结构、Git Hook 与 CI 机制以及本地验证方法。关于架构原则、硬性约束与设计红线，请参阅 [架构设计与约束规范](architecture.md)。

## 1. 环境要求

- **Node.js**：`>=22.16.0` 运行仓库脚本（CI 使用 Node.js `22.20`；`npx skills` CLI 要求 Node.js `>=22.20`）
- **pnpm**：`packageManager` 固定 `pnpm@11.4.0`，`engines.pnpm` 限制在 `>=11.4.0 <12`

首次克隆后，若尚未启用 Corepack，先执行 `corepack enable`，随后安装锁定依赖并启用项目级 Git Hook：

```bash
pnpm install --frozen-lockfile
pnpm run hooks:install
```

## 2. 仓库目录结构

```text
.
├── .claude-plugin/
│   └── marketplace.json      # Plugin Grouping 清单（由脚本自动生成，禁止手改）
├── .githooks/
│   └── pre-commit            # 源文件变更时自动同步并暂存 marketplace
├── .github/
│   └── workflows/
│       └── ci.yml            # CI：检查生成文件、校验 Skill、运行测试
├── skills/                   # 所有 Skill 存放根目录（分类目录规范）
│   ├── platform/
│   │   ├── macos/
│   │   │   └── macos-launch-agent/
│   │   ├── linux/
│   │   └── windows/
│   ├── development/
│   │   ├── docs/
│   │   │   └── check-mermaid/
│   │   └── workflow/
│   │       └── hands-on-teaching-development/
│   └── standards/
│       └── git-commit-message/
├── scripts/
│   ├── install-hooks.mjs     # 配置项目级 Git Hook
│   ├── uninstall-hooks.mjs   # 移除项目级 Git Hook 配置
│   ├── link-skills.mjs       # 开发调试：软链接挂载到本地 Agent 技能目录
│   ├── lib/
│   │   ├── skills.mjs        # 目录扫描、标准 YAML Frontmatter 解析与生成模型
│   │   └── cli-args.mjs      # 归一化 pnpm 透传的可选 -- 分隔符
│   ├── sync-marketplace.mjs  # 自动生成/检查 marketplace.json
│   └── validate-skills.mjs   # 目录结构、Frontmatter、Manifest 与本地链接校验
├── test/                     # 仓库脚本、Hook、CI 与配置自动化测试
├── docs/
│   ├── architecture.md       # 架构设计与约束规范
│   └── development.md        # 开发与维护指南（本文件）
├── package.json              # 脚本入口与 YAML 解析依赖
├── pnpm-lock.yaml            # 锁定脚本依赖
├── manifest.json             # 仓库元信息与分组描述配置（同步脚本输入源）
├── .gitignore
└── README.md
```

## 3. 配置中心与分组映射

- 仓库名称、`owner`、`metadata` 和分组描述统一在根目录的 [manifest.json](../manifest.json) 中维护。
- [.claude-plugin/marketplace.json](../.claude-plugin/marketplace.json) 完全由 `manifest.json` 与 `skills/` 扫描结果派生，**禁止手工编辑**，也不会反向读取旧生成文件。
- 分类目录会自动映射为 Plugin Group 分组：二级目录直接映射为 `<category>`，三级目录映射为 `<category>-<subcategory>`；若某个实际使用的分组未在 `manifest.json` 配置描述，将回退为默认描述。
- 分类拼接后若产生相同分组 ID（例如 `a-b/c` 与 `a/b-c`），脚本会报错阻断，不会静默合并。

## 4. 新增与维护 Skill

### 4.1 命名与目录约定

- 每个 Skill 位于分类目录下，支持两级目录（`skills/<category>/<skill-name>/SKILL.md`）或三级目录（`skills/<category>/<subcategory>/<skill-name>/SKILL.md`）。
- `SKILL.md` frontmatter 中的 `name` 必须：
  - 全仓库全局唯一；
  - 使用小写 `kebab-case`（如 `homebrew-manager`）；
  - 与所属目录名 `<skill-name>` 严格一致。
- `description` 须清晰说明功能、适用场景与触发条件；平台专属 Skill 须写明适用操作系统。

### 4.2 创建步骤

1. 在目标分类下创建独立 Skill 目录及 `SKILL.md`（按需附带 `references/`、`scripts/`、`assets/`、`tests/`）：

   ```text
   skills/platform/macos/<skill-name>/
   ├── SKILL.md
   ├── references/
   ├── scripts/
   ├── assets/
   └── tests/               # 可选：Skill 专项测试（*.test.mjs，根目录 pnpm test 会自动发现）
   ```

2. 确保所有引用资源均位于该 Skill 目录内部，不依赖其他 Skill 或仓库根目录文件。
3. 暂存并提交变更（若已启用 `hooks:install`，提交时会自动同步 `marketplace.json` 并校验）：

   ```bash
   git add skills/ manifest.json
   git commit -m "feat: add <skill-name>"
   ```

## 5. Git Hook 与 CI 自动化

仓库使用原生 Git Hook，不引入 Husky 或 Lefthook 等额外框架。

### 5.1 安装与卸载

- 安装项目级 Hook（仅设置当前仓库 `core.hooksPath=.githooks`，不修改全局配置）：

  ```bash
  pnpm run hooks:install
  ```

- 若当前仓库已配置其他本地 `core.hooksPath`，脚本默认拒绝覆盖；确认需要替换时执行：

  ```bash
  pnpm run hooks:install -- --force
  ```

- 移除项目级 Hook 配置：

  ```bash
  pnpm run hooks:uninstall
  ```

> 注意：若你的 Git 全局配置已设置 `core.hooksPath`，仓库本地配置会优先覆盖它，原全局 Hook 不会自动链式执行；需要时可自行从 `.githooks/pre-commit` 调用。

### 5.2 `pre-commit` 行为

[.githooks/pre-commit](../.githooks/pre-commit) 仅在暂存区包含 `skills/`、`manifest.json` 或 `.claude-plugin/marketplace.json` 变更时运行，与 Skill 无关的提交会快速跳过。触发后会依次执行：

1. 拒绝 `skills/` 或 `manifest.json` 存在未暂存修改，避免生成结果包含未进入本次提交的内容；
2. 拒绝 `skills/` 下存在未跟踪文件，要求先明确 `git add`；
3. 执行 `pnpm run skills:sync` 重新生成 `marketplace.json`；
4. 自动执行 `git add -- .claude-plugin/marketplace.json`；
5. 执行 `pnpm run skills:check` 校验，失败则阻止提交。

> Git 没有标准的 `pre-add` Hook，因此不会在 `git add` 时生成，而是在 `git commit` 的 `pre-commit` 阶段完成生成与暂存。

### 5.3 GitHub Actions CI

[.github/workflows/ci.yml](../.github/workflows/ci.yml) 在 Push 和 Pull Request 时运行（本地 Hook 可被 `--no-verify` 绕过，CI 是最终质量门禁）：

- `pnpm install --frozen-lockfile`
- `pnpm run skills:sync --check` 与 `pnpm run skills:sync -- --check`（只读检查，不写文件）
- `pnpm run skills:check`
- `pnpm test`
- Hook 安装/卸载与参数透传自检

## 6. 本地测试与验收

### 6.1 仓库检查与测试

```bash
pnpm run skills:sync                 # 手动预览/重新生成（通常由 Hook 自动执行）
pnpm run skills:sync -- --check      # 只读检查 marketplace 是否最新
pnpm run skills:check                # 校验目录结构、YAML Frontmatter、Manifest 与本地 Markdown 链接
pnpm test                            # 运行单元测试
npx skills add . --list              # 验证本地 CLI 发现结果
```

### 6.2 独立临时目录安装测试

在独立临时目录中验证分组展示与单 Skill 独立复制安装（避免污染当前仓库）：

```bash
TEST_DIR="$(mktemp -d)"
cd "$TEST_DIR"
npx skills add /path/to/skills -a claude-code --copy
npx skills list
```

### 6.3 本地开发挂载（软链接安装器）

开发与调试 Skill 时，可通过 `pnpm run skills:link` 将仓库内的 Skill 目录通过符号链接（Windows 下为 Junction）直接挂载至本地 Agent 运行时目录（如 `~/.claude/skills`、`~/.agents/skills` 或 `~/.gemini/skills`），无需每次修改后重复复制：

```bash
# 使用预设目标挂载全部 Skill（Codex 默认读取 ~/.agents/skills，与 agents 共用同一目标；antigravity 别名映射至 ~/.gemini/skills）
pnpm run skills:link --agent claude
pnpm run skills:link --agent agents
pnpm run skills:link --agent codex
pnpm run skills:link --agent gemini
pnpm run skills:link --agent all

# 指定自定义目录或单个 Skill
pnpm run skills:link --target ~/.claude/skills --skill git-commit-message

# 预演模式（不实际创建文件）
pnpm run skills:link --agent claude --dry-run

# 强制覆盖已有冲突链接或目录
pnpm run skills:link --agent claude --force

# 卸载由本仓库创建的软链接
pnpm run skills:link --agent claude --unlink
```
