# macOS LaunchAgent 管理助手

**普通需求用固定模板，复杂需求用 Agent 生成 XML，所有已安装任务统一托管。**

本版本把“配置生成方式”与“任务生命周期”分开，不再把 XML 入口当成只能生成、无法管理的旁路。

## 环境与安装 Skill

需要 Python **3.10+** 和当前用户的 macOS 图形登录域（仅写盘不要求当前有 GUI 域）。管理器拒绝 root/sudo；Python 版本不足会返回明确错误。只需 Python 标准库，没有 pip 依赖。

将完整 `macos-launch-agent` 文件夹放入使用者的 Agent 所支持的 skills 位置；不要只复制 SKILL.md。本文命令假定在该文件夹执行；Agent 实际执行时应使用脚本绝对路径。没有操作用户 Mac 的工具时，只能交付文件/指引，不代表已安装到其机器。

## 结构与分工

| 组件 | 作用 |
|---|---|
| SKILL.md | 场景识别、入口选择、授权边界、调用流程 |
| scripts/launch_agent.py | 唯一管理器：install、install-plist、register、list、status、describe、run、repair、uninstall |
| scripts/xml_to_plist.py | 独立 XML→plist 导出，支持文件/字符串/stdin 与 XML/binary |
| scripts/custom_plist.py | 内部配置适配与常见字段校验，不负责生命周期 |
| references/ | XML 示例、管理恢复、技术依据与审查说明 |
| tests/ | 固定功能回归、XML 转换、跨入口集成及故障注入 |

固定安装和自定义导入共用同一个 `commit_install()`，避免安装、回滚和记录规则各自演化。转换器仍可单独使用；只想导出时无需托管导入。

## 常规需求

```sh
# 登录后执行；默认本次不注册、不执行
python3 scripts/launch_agent.py install \
  --command '/ABS/PATH/task.sh' --name 'login-service' --description '登录后运行指定任务'

# 每天两个时间点；默认在 GUI 域注册，不额外立即请求执行
python3 scripts/launch_agent.py install --mode calendar \
  --time '09:00,13:00' --command '/ABS/PATH/task.sh' --name 'twice-daily-maintenance' \
  --description '每天两次运行指定任务'
```

`/ABS/PATH/task.sh` 是占位路径。脚本有 executable/shebang 时可用 `--script`；复杂命令优先 `--command-stdin`。`--time` 可重复；星期、月日、月份参数见 `install --help`。没有时间时 CLI 的默认值仍为 09:00，**Agent 不得把缺省值当作用户已授权**。

登录安装/更新不带 `--start-now` 时只写盘；运行实例继续使用原注册配置。日历任务要只写盘用 `--defer`。要立即执行固定任务需用户明确请求后使用 `--start-now`。

## 英文任务名称与重复任务

Agent 创建固定任务或自定义任务时都应传稳定、简短的英文 `--name`。用户给中文名字，应翻译为英文标识（如“每日备份”→`daily-backup`）；没给名字，就依据用途生成并告知用户。CLI 直接接受中文名字时仍会用 `job-<hash>` 兜底，但 Agent 不应依赖该退化方式。修改命令、名称或从无名改为有名，都可能新增任务身份；本工具只提示同模式同命令的已托管任务，不自动停用。先 `list/status` 核对是否需要并存。

固定登录/定时任务的安装输出及 `list/status` 会显示 `RunAtLoad`、`KeepAlive`。全新安装不会提示存在旧任务；仅写盘不会被报告为本次已经注册；旧日程提示只会在先前有注册快照且本次确实修改了配置时出现。

## 扩展需求：XML 仍由 Agent 发挥，但安装不失管

适合 StartInterval、WatchPaths、QueueDirectories、精确 argv、环境变量或固定日期组合无法表达的场景。例如“周一 9 点、周五 18 点”应在 XML 中分别列两个日历字典，不能用固定参数展开成四次。

先从用户需求编写完整 Apple plist XML，推荐省略 Label、日志路径、Umask。将 XML 交给转换器：

```sh
python3 scripts/xml_to_plist.py --input custom.xml --output custom.plist
# 或 --input - 从 stdin 读取；或 --xml '完整 XML'
# --format binary 输出二进制；--overwrite 仅在明确允许覆盖时使用
```

**用户只要文件：到此结束。需要安装：再导入统一管理。**

```sh
python3 scripts/launch_agent.py install-plist \
  --input custom.plist --name 'my-custom-task' \
  --description '描述真实用途和触发方式' --dry-run

# 核对预览后，去掉 --dry-run 即写入托管位置；默认当前会话不注册
python3 scripts/launch_agent.py install-plist \
  --input custom.plist --name 'my-custom-task' \
  --description '描述真实用途和触发方式'
```

`install --dry-run` 与 `install-plist --dry-run` 共用真实安装的**磁盘只读预检**：缺少 `--replace` 的同 Label 变化、孤立记录、无管理记录 plist、哈希/模式冲突都会拒绝；预览和安装打印同一套触发、计划时间、工作目录、日志摘要。预览**不写盘、不调用 launchctl**，因此不保证 GUI 域存在、任务未被系统禁用或 bootstrap 能成功。实际提交会在事务锁内重新检查。

明确同意激活时加 `--activate`。这会注册原 XML 条件，**可能马上运行**，但不会额外 kickstart。后续仍用同名、更新后的源文件和 `--replace` 更新。也可单独按 Label 注册，无需重新构造 XML。

管理器生成独立托管 Label，并设置日志、私有 Umask 和默认工作目录；业务字段原样保留。来源 XML 的原 Label 只作记录，绝不会用来操作外部服务。明确冲突的日志/Umask 会报错，不静默改掉。源文件移动/删除不妨碍已导入任务的管理，但任务依赖的真正脚本/程序仍须存在。

`install-plist --dry-run` 和真实导入都显示源 XML 的 `来源 Label`（缺失时显示“未提供”）；源 Label 不是托管 Label。不带 `--activate` 的真实导入会明确显示“未注册，仅写盘”。

## 统一管理，不再区分谁生成了配置

```sh
python3 scripts/launch_agent.py list
python3 scripts/launch_agent.py status --label '<实际 Label>'
python3 scripts/launch_agent.py describe --label '<实际 Label>' --description '新的用途'
python3 scripts/launch_agent.py register --label '<实际 Label>' --allow-execution
python3 scripts/launch_agent.py run --label '<实际 Label>'
python3 scripts/launch_agent.py uninstall --label '<实际 Label>'
```

`register` 停止/重注册当前磁盘配置，更新可信注册快照，可能触发业务执行；`--allow-execution` 为必需的风险确认参数。仅更新文件、`accept-plist` 或 `reset-snapshot` 都不能证明内存配置已同步。

`run` 要求已加载、记录/磁盘一致且存在匹配的可信注册快照；拒绝未知或漂移状态。不使用 `kickstart -k` 强杀正在运行的实例，不承诺启动第二份。`status` 读取管理记录成功即退出 0，加载状态另看输出。

## 异常恢复

| 状态 | 操作 | 副作用 |
|---|---|---|
| 磁盘被改，但仍符合相应托管外壳 | `repair --action accept-plist --trust-current-plist` | 信任磁盘，不注册；旧快照可能变未知 |
| 磁盘增加其他合法字段而不再符合固定模板 | `repair --action drop-unrecognized --confirm-delete` | 须显式确认：已托管任务停止并删除异常 plist/JSON；不接管更改，保留日志 |
| 需要放弃旧快照、不想执行 | `repair --action reset-snapshot --trust-current-plist` | 清快照，不动服务；run 仍被拒绝 |
| plist 丢失，仅留有效记录 | `repair --action remove-orphan --confirm-stop` | 先停止可能仍加载的服务，再删记录 |
| JSON 损坏，文件权限安全、plist 仍可信 | `repair --action rebuild-record --trust-current-plist --description '重新确认的用途'` | 重建记录，不注册；快照标未知 |
| JSON 损坏且 plist 缺失 | `repair --action remove-orphan --confirm-stop` | 记录必须是当前用户的安全普通文件；若同 Label 服务仍加载，先人工核实并停止，脚本不凭损坏记录自动停服 |
| plist 无法解析，记录有效 | 人工核对服务并按需 `bootout`、核实后手动删除该 plist，再 `repair --action remove-orphan --confirm-stop` | 不自动删除无法检查内部 Label 的文件 |
| JSON 损坏且 plist 也无法识别 | 人工核对来源与运行态，必要时手工停服和删除已核实的 plist，再 `remove-orphan --confirm-stop` | 不自动重建不可信文件，不按损坏记录停止同名服务 |
| 只有同前缀 plist，没有管理记录 | `list` 单列“未管理”；人工核实来源、运行态，确认废弃后人工停止并删除 | 不允许 `install`、`uninstall` 或 `repair` 接管同名未知服务 |

除只需人工处置的未知文件外，修复命令均需附上真实 `--label`；不可用它们接管无记录任务。**先决定保留还是丢弃**：`status`、`install --replace` 和 `uninstall` 对仍符合托管模板的哈希漂移会优先给出 `accept-plist` 指引，先核对修改内容，再显式接受；其后按需 `register --allow-execution`（可能触发执行）。仅希望删除时才使用 `drop-unrecognized`。`drop-unrecognized` 只用于**有有效管理记录、普通安全文件且内部 Label 匹配的可解析异常 plist**；只有用户明确同意丢弃改动后才能清理，不能用于任意文件或恢复为正常任务。若加载状态未知会拒绝，若清理失败不会自动启动被外部更改的配置。`status` 会显示固定模板差异键，帮助决定修复还是删除；若 plist 仍符合模板但仅哈希不同，则说明可接受并保留配置，不应只建议删除。若意图关闭登录自启，请用 `uninstall`，不要修改 `RunAtLoad=false`；已改坏时应恢复原模板后卸载或明确选择异常清理。接受外部修改后需重新注册时，固定与 XML 都使用 `register --label ... --allow-execution`。登录任务会因 RunAtLoad 可能执行；不想现在执行就仅复位/写盘，不能声称注册恢复完成。完整诊断流程见 `references/operations.md`。

**未管理路径的可见性**：`list` 中，同命名样式的普通 `.plist` 每项只有一行摘要，完整的人工核对/停服/删除说明留给 `status --label` 和 `references/operations.md`。目录、符号链接及其他非普通文件通过 `lstat` 分类为异常路径，不跟随链接，不按普通 plist 处理或建议直接删除。`status` 也按类型显示提示；它们不会被自动接管。

## 文件、权限与通用约束

管理记录位于 `~/Library/Application Support/macOSLaunchAgentSkill/Agents/<Label>.json`，含描述、模式、来源、plist 路径、磁盘哈希、注册快照；XML 任务还记录来源文件哈希、原 Label 和稳定名称。与 plist 共同判定归属，不能只看前缀。

plist 在 `~/Library/LaunchAgents/`；日志在 `~/Library/Logs/LaunchAgents/`。元数据专属父/子目录均 `0700`，文件 `0600`。`.local.my.agents.lock` 为常驻 0 字节锁文件，与任务本身无关。卸载只删除托管 plist/JSON，保留运行日志、转换日志及外部源文件。

`~/Library/LaunchAgents` **不支持符号链接目录**，即使指向安全的实体目录；`list/status` 等读取操作也会拒绝。请先人工核对原目录和链接目标，使用当前用户所有、群组和其他用户不可写的实体目录（建议权限 `0700`）后重试。Skill 不会自动改动链接、迁移文件或接管原有任务。

转换器向 `conversions.log` 追加 prepared/written/failed 审计事件。它不会自动成为安装记录；日志并非同用户不可篡改的安全审计设施。
日志里的 `source_label` 只是源 XML 自带的 Label（也可能为空），不是后来由管理器分配的托管 Label。

## Disabled 和系统禁用覆盖

自定义 XML 禁止 `Disabled`（包括 `false`），它是启用状态而非触发条件。系统设置“登录项与扩展”或 `launchctl disable` 可能产生该任务的持久禁用覆盖，使后续 `register` / `--activate` 失败并出现泛用 EIO。`list/status` 独立显示“已禁用”或“无法确认”，不会将无法解析的文本误判为已启用。

本工具不自动调用 `launchctl enable/disable`。用户明确同意后，可在自己的 Mac 上执行 `launchctl enable "gui/$(id -u)/<真实 Label>"`，或从系统设置重新打开，再执行 `register --label '<真实 Label>' --allow-execution`。enable 是跨重启的持久更改；EIO 也可能由其他配置/权限问题引起，不能仅凭退出码断定服务已被禁用。

持久禁用覆盖是 **Label 的系统状态**，即使 `uninstall` 已删除 plist 与管理记录也可能保留。卸载成功后若明确检测到会提醒用户，不会自动 `launchctl enable`。同名新任务可以只写盘，注册或激活仍可能被拒绝；这不代表卸载残留了任务文件。

**按操作区分提示：** `register` 一定尝试注册，没有 `--start-now`、`--defer` 或 `--activate` 这样的只写盘模式；若只是更新登录任务，另用 `install`（不加 `--start-now`）。已禁用的日历任务只更新磁盘配置，使用 `install --replace --defer`；**卸载后同名日历任务重装只写盘则用 `install --defer`**。自定义导入不带 `--activate` 时只写盘。`list` 对禁用覆盖只输出短状态；`status` 输出完整操作建议。

不要自动提权、修改其他软件任务、改写用户业务命令、运行用户命令作测试、伪造 JSON 或绕过哈希/权限错误。库校验只覆盖常见类型和用户级边界，任意深层语义仍须按目标 macOS 手册确认。没有 root、LaunchDaemon、一次性年份任务或可靠事件队列承诺。

## 当前模式的边界

- `install` 处理固定登录与日历参数；`install-plist` 接收 Agent 编写的自定义 plist；`xml_to_plist.py` 只导出，不直接安装。
- 三种模式使用相互独立的 Label 身份，跨类型调整必须先确认原任务，不能直接宣称已替换。
- 没有有效管理记录的外部 plist 不属于本工具的管理范围，不自动接管或卸载；若位于 LaunchAgents 且文件名符合当前托管前缀，`list` 会单列为“未管理”，仅供人工核对，不代表归属已获验证。
- 管理记录按必填字段和允许字段严格校验，不使用格式版本号；不自动接管未受托管的文件。

## 测试与验证边界

```sh
python3 --version  # 3.10+
python3 -m unittest discover -s tests -v
```

测试在隔离临时目录中进行，管理器用模拟 launchctl，不执行任何用户命令；XML CLI 有真实子进程的读写测试。实际 macOS plutil、GUI 域、权限授权、调度/睡眠和业务执行仍需在目标 Mac 上验证。代码与测试通过不等于覆盖每个 launchd 字段或保证零缺陷。

审查结果见 `references/review.md`；技术依据见 `references/sources.md`。
