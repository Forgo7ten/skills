---
name: check-mermaid
description: >
  编写 Markdown 文档时用 mermaid 作图，并在完成后用本 skill 的脚本校验全部 mermaid 代码块。
  在生成/修改含 mermaid 的 md、用户要求画图校验、或文档交付前自检时使用。
---

# check-mermaid — mermaid 作图与校验

生成 Markdown 需要画图时使用 mermaid，写完后必须校验全部 mermaid 代码块再交付。

## 校验责任

- **写文档的子 agent**：写完后立刻自校验，**全部通过**（退出码为 0）才向主 agent 报告完成；报告本身即「校验通过」的承诺。
- **主 agent 自己写的**：自跑自校验。
- 严禁在未实际跑通（退出码非 0）的情况下声称校验通过。

## 脚本位置

- 主脚本：`scripts/check_mermaid.ts`
- 辅助模块：`scripts/lib/markdown-fences.mjs`
- 请基于**当前 Skill 所在目录**解析上述路径，不要硬编码或假设全局安装路径（不同 Agent 的安装路径可能不同，例如 `~/.agents/skills/` 或 `~/.claude/skills/`）。

## 调用方式

```bash
npx --yes tsx <本 SKILL 目录>/scripts/check_mermaid.ts <md文件绝对路径>
```

可选参数：

```bash
npx --yes tsx <本 SKILL 目录>/scripts/check_mermaid.ts <md文件绝对路径> --keep-temp
```

`--keep-temp` 会保留生成的 `.mmd` 和 `.svg` 临时文件便于排错；返回的 JSON 中也会包含 `temp_dir`、`input_file` 与 `output_file` 路径。

## mmdc 渲染器选择策略（脚本内已实现）

1. 优先使用 `mmdc`（PATH 中已安装，速度最快）
2. 否则回退到 `npx --yes --package @mermaid-js/mermaid-cli mmdc`（首次运行会自动下载）
3. 若两者都不可用，则报告环境错误

如果目标 Markdown 文件中不包含任何 mermaid 代码块，则直接通过（退出码 0），不强制依赖 `mmdc` 或 `npx`。

## Markdown 围栏解析规则

- 支持 CommonMark 风格的反引号（`` ` ``）或波浪号（`~`）围栏，围栏长度支持 3 个及以上字符，允许最多 3 个前导空格缩进。
- 仅校验信息字符串以 `mermaid` 开头的围栏块；嵌套在其他非 mermaid 代码块（例如 ```` ```markdown ````）中的示例文本会被自动忽略。
- 行号采用 1-based 计数：
  - `start_line`：Mermaid 内容的第一行（不含开头的围栏行）；
  - `end_line`：闭合围栏所在行（若围栏未闭合，则为输入文件的最后一行）。
- 未闭合的 mermaid 围栏块会作为 `input_error` 上报。

## 输出与退出码

脚本向 stdout 输出 JSON。每个失败块都会带有 `error_type` 和 `error` 诊断信息（合并渲染器 stdout/stderr 并在过长时截断，不保证是原始 stderr）。

### 错误类型 (`error_type`)

| `error_type` | 含义 | 处理建议 |
|---|---|---|
| `syntax_error` | Mermaid 语法或解析器报错 | 修复对应的 Mermaid 代码后重跑 |
| `render_error` | 渲染器因未分类原因失败 | 检查渲染器输出与本地运行环境 |
| `environment_error` | 渲染器/浏览器/命令不可用或无法启动 | 修复本地运行环境，不要盲目改写图表语法 |
| `dependency_error` | `npx` 无法拉取或运行 Mermaid CLI 依赖 | 排查 npm/网络/依赖访问，不要盲目改写图表语法 |
| `input_error` | 参数错误、文件不可读或围栏未闭合 | 修复调用参数或 Markdown 围栏结构 |
| `timeout` | 渲染执行超时 | 检查图表复杂度或系统资源 |

### 退出码

| 码 | 含义 |
|----|------|
| `0` | 全部代码块校验通过，或文档中无 mermaid 代码块 |
| `1` | 存在至少一个 Mermaid 语法或渲染失败块 |
| `2` | 输入参数、围栏结构、运行环境、依赖拉取或超时错误 |

输出 JSON 示例：

```json
{
  "file": "/path/to/document.md",
  "runner": "mmdc",
  "total": 1,
  "passed": 1,
  "failed": 0,
  "blocks": [
    {
      "index": 1,
      "start_line": 2,
      "end_line": 5,
      "ok": true,
      "diagram_type": "flowchart"
    }
  ]
}
```

## 工作流

1. 在 Markdown 中使用 ` ```mermaid ` 代码块作图
2. 对目标文件完整运行校验脚本
3. 若出现 `syntax_error`，按 `start_line`、`error` 和 `snippet` 定位修复对应代码块
4. 若出现 `environment_error`、`dependency_error` 或 `timeout`，优先排查运行环境问题，不要将其误判为语法错误
5. 修改后重新运行校验脚本
6. 仅当进程退出码为 `0` 时才报告文档完成；若受环境阻塞无法校验，应如实向用户反馈阻塞原因
