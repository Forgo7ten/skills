---
name: macos-launch-agent
description: "macOS LaunchAgent：登录自启、日历定时与XML扩展，统一安装、查询、更新、修复和卸载。"
---

# macOS LaunchAgent 管理助手

**定位：常规需求用固定参数生成配置；模板确实不能表达的需求由 Agent 生成 XML；所有安装都交给同一个管理器。** XML 转换不是另一套安装方案，更不是绕过归属检查的后门。

要求 **Python 3.10+**，仅依赖标准库。两个 CLI 在运行时检查最低版本。实际管理必须在用户的 Mac 上、以目标用户身份执行，禁止 sudo/root。其他环境只允许生成文件及 `--dry-run` 预览，不得声称已安装到用户机器。

## 触发场景与作用

- **登录自启动**：用户要求 Mac 开机后自动运行命令、脚本或本地服务。按“用户图形登录后”理解并说明，不冒充登录前的系统启动；明确要求退出后重启时才配置 KeepAlive。
- **日历定时**：每天一次/多次、工作日、每周某日、每月某日或每年某月某日执行备份、整理、打开网页、提醒等本地命令。
- **扩展触发与执行配置**：按秒间隔、路径变化、队列目录、不同星期不同时间的日历组合、明确的环境变量、直接 argv、条件 KeepAlive 等。固定参数无法完整表达时走 XML，不修改安装器源码。
- **查询与维护**：列出此前通过本 Skill 安装的固定任务和 XML 任务，查看用途、命令、来源文件、触发配置、加载状态、配置漂移；更新描述、注册/重注册、明确触发、修复或卸载。
- **只生成文件**：用户只要 XML→plist、二进制转换或交付配置文件时，转换后停止，不安装、不注册。

只管理**本 Skill 有效 JSON 记录所证明的当前用户 LaunchAgent**；不管理其他程序的任务、LaunchDaemon、root、登录前服务或 ChatGPT 云端定时提醒。XML 不能突破 launchd 自身的能力边界；不承诺任意 cron、精确一次执行、未来某年一次性任务、可靠事件队列或系统级权限。

## 先选择路径，再执行

| 用户意图 | 路径 | 是否写 LaunchAgents / 是否托管 |
|---|---|---|
| 常规登录或固定日历规则能完整表达 | `launch_agent.py install` | 是 / 是 |
| 需要固定参数以外的合法用户级配置 | Agent 生成 XML → `xml_to_plist.py` 导出 → `launch_agent.py install-plist` | 导出不写；导入后写 / 是 |
| 只转换或导出配置 | `xml_to_plist.py`，导出后停止 | 否 / 否 |
| 查询、更新、修复、注册或卸载已有任务 | 先 `list/status` 获取真实 Label，再用管理器对应子命令 | 只操作已托管对象 |

**区分能力缺口与运行故障。** 不存在的路径、无 GUI 域、权限错误、损坏记录、哈希不一致，不是切换到 XML 的理由；必须修正输入或按恢复流程处理，不能借扩展入口绕开检查。

**避免错误的日历展开。** 固定 `--time '09:00,18:00' --weekday 'mon,fri'` 表示周一和周五都在这两个时间执行。用户要求“周一 09:00、周五 18:00”时，应生成两个独立 `StartCalendarInterval` 字典，不能套用固定参数制造四次触发。

## 生成与管理的职责边界

- `scripts/launch_agent.py` 是**唯一托管安装及生命周期入口**。固定 `install` 和扩展 `install-plist` 共用 `commit_install` 的锁、归属/哈希校验、原子写入、注册、快照及回滚。`register/list/status/describe/run/repair/uninstall` 对两类任务通用。
- `scripts/xml_to_plist.py` 是可独立使用的**导出器**：传入 XML 与输出路径，验证并生成 XML/binary plist；不调用 launchctl，不写任务管理记录。导出审计日志不等于管理记录。
- `scripts/custom_plist.py` 是内部配置适配层，不是新的 CLI：保留业务参数、环境变量与触发键，检查常见类型和用户级边界，添加管理外壳。Agent 不调用或改写其内部函数。
- 自定义导入自动分配 `local.my.agents.custom.<名称摘要>-<哈希>`；来源 XML 建议省略 Label。已有来源 Label 仅记录为来源信息，**不会按该 Label 停止、替换或接管任何服务**。托管前缀由管理器独占，转换器在所有目录都拒绝该前缀的 Label。
- 全部托管 Label 使用 `local.my.agents.` 前缀。Agent 传给 `--name` 的应是语义明确的简短英文名；中文名要先翻译，缺失名称时依据用途生成。脚本对纯中文名称仍按原逻辑退化为 `job-<hash>`，不要依赖兜底命名。
- 安装器统一分配 `Label`、`StandardOutPath`、`StandardErrorPath`、`Umask=63`（077）。自定义 XML 不要填写日志路径/Umask；冲突值会报错而非静默覆盖。`WorkingDirectory` 可指定已有绝对目录，否则默认用户主目录，并执行 `resolve(strict=True)`。
- 扩展字段不采用“只允许固定模板所有键”的白名单；例如 StartInterval、WatchPaths、复杂日历、EnvironmentVariables、条件 KeepAlive 可以保留。**常见字段检查并非 launchd 的完整 schema**，未覆盖键需查目标 Mac 的 `man launchd.plist`，不能把 XML/plutil 通过当成业务正确。
- **自定义 XML 禁止 `Disabled`（包括 false）**：该字段属于系统启用状态，不是触发条件。管理器注册前检查明确的持久禁用覆盖；不会自行调用 `launchctl enable/disable`。
- 转换器机械拒绝向名为 LaunchAgents/LaunchDaemons 的自动加载目录及其子目录写文件，也会检查解析后的路径。安装必须使用 `install-plist`，转换器没有直接安装选项。这属于防误用，不是对同用户手工移动文件、编辑 XML 的安全隔离。

## Agent 执行规则

1. 先区分**导出、安装、更新、迁移、查询**。用户已明确的信息不要重复询问；仅缺少会影响行为的命令、时间或目标时澄清。用户未给具体时间时，不得默用每天 09:00；固定脚本有该默认值，但它不是用户授权。
2. 用户明确要求安装，才写入 LaunchAgents；明确要求立即执行，才使用固定 `--start-now` 或 `run`。自定义 `--activate` 和 `register --allow-execution` 表示同意注册及可能立即触发的副作用，不等于“保证只运行一次”。
3. 固定模板可以满足时，只传参数，不手写 XML。模板不能完整表达时，先阅读 [自定义 XML 流程](references/custom-xml.md)，生成最小必要配置，转换、预览、核对后再导入。不因用户没指定 XML 这个词就拒绝合理的扩展需求。
4. 保留真实命令和参数，不擅自添加 sudo、nohup、后台 `&`、重试、秘密或业务动作。不要为测试直接执行用户命令。固定命令使用 `/bin/zsh -lc`；自定义 `ProgramArguments` 是 argv 数组，不做 shell 拆分、插值或转义猜测。
5. **任务英文命名（Agent 必做）**：首次安装固定或 XML 任务时一律传 `--name`，采用简短、稳定、语义明确的英文小写加连字符（如 `daily-backup`）。用户提供中文名称时先译成简短英文标识（如“每日备份”→`daily-backup`）；未给名称时根据用途生成英文标识并告知用户。不得把中文直接传给 `--name`；脚本本身的 `job-<hash>` 是直接调用的兜底行为，Agent 不依赖它。更新必须复用现有任务名称与真实 Label，不能重新翻译或擅自改名。新任务必须提供准确的 `--description`，不得包含密码或令牌。
6. 更新先 `list/status`，核对实际 Label、模式、内容和用途；变化需要 `--replace`。**固定与自定义是独立身份**；不能把“从固定升级到 XML”偷偷做成新任务并遗忘旧任务。跨身份替换须明确迁移计划和旧 Label；本版不提供跨 Label 原子迁移。
7. XML 字段控制触发行为，不修改它们来假装“安装不执行”。需要只准备时仅导出；导入不带 `--activate` 也会把 plist 放入登录加载目录，下次图形登录可能执行。正在运行的同 Label 任务不会因只写盘而更新。
8. **预览是只读磁盘预检**：`install` 与 `install-plist --dry-run` 使用真实提交相同的归属、哈希、模式、孤立记录及 `--replace` 检查，并展示触发配置、日历、工作目录、日志。预览不写盘、不调用 launchctl，**无法保证 GUI 域可用、系统没有禁用覆盖或注册能成功**；实际提交在锁内重新检查。
9. 不直接运行 bootstrap/bootout、不伪造管理 JSON、不覆盖托管 plist；**仅在核实异常文件来源并由用户明确同意的人工清理步骤中例外**，不得凭 Label 前缀认领外部文件。先读 `status/list` 提供的状态专属恢复建议，缺 plist 的有效/损坏记录使用 `remove-orphan --confirm-stop`，损坏 JSON 而有效 plist 仍在时才用 `rebuild-record --trust-current-plist --description`。若 JSON 损坏且同 Label 服务加载，不能证明其归属，先人工核实并停止，不让脚本自动停服。只有已核实并获明确同意时执行相应 `repair`。**模板内哈希漂移优先保留**：先核对磁盘内容，确认可信后用 `repair --action accept-plist --trust-current-plist`；如需更新可信注册快照，再经明确授权 `register --allow-execution`，后者可能立即执行。**明确丢弃任务**时才使用 `repair --action drop-unrecognized --confirm-delete`，须由用户同意停止服务并删除文件；它不接管或信任被修改内容，也不自动重新注册。已被系统设置/`launchctl disable` 禁用的服务由用户明确确认后再启用，Agent 不自动 `launchctl enable`。可查阅 [管理与恢复](references/operations.md)。
10. 输出用中文；技术字段名保留。报清“已导出 / 已写盘 / 已注册 / 已请求执行”，不能把成功退出、加载成功当成业务完成；异常不得换入口继续写入。

## 常规路径：固定参数

先确定 Skill 的实际目录。下文 `/ABS/PATH/...` 是占位符。复杂命令通过单引号 heredoc 原样传递。

登录自启动：

```sh
python3 "/ABS/PATH/macos-launch-agent/scripts/launch_agent.py" install \
  --description "登录后运行用户指定的本地服务" --name 'login-service' --command-stdin <<'__USER_COMMAND__'
<用户原始命令>
__USER_COMMAND__
```

每天 09:00 和 13:00：

```sh
python3 "/ABS/PATH/macos-launch-agent/scripts/launch_agent.py" install \
  --mode calendar --time '09:00,13:00' \
  --description "每天两次执行指定维护命令" --name 'twice-daily-maintenance' --command-stdin <<'__USER_COMMAND__'
<用户原始命令>
__USER_COMMAND__
```

日期参数：工作日 `--weekday 'mon,tue,wed,thu,fri'`；每月 1 日 `--month-day 1`；每年 12 月 1 日 `--month 12 --month-day 1`。星期与每月日期不能在固定入口混用；明确月份的不存在日期会拒绝。`--script` 接收存在、可执行且有 shebang 的绝对脚本路径；需要解释器时提供完整命令，不自动猜。

| 模式 | 默认行为 | 显式执行/激活 |
|---|---|---|
| login | 只写盘；桌面与 SSH/headless 都不调用 launchctl | `--start-now` 注册，RunAtLoad 可能立即执行 |
| calendar | GUI 域可用才注册；RunAtLoad=false，不额外 kickstart | `--start-now` 额外请求执行；`--defer` 只写盘 |
| custom | 只写盘；保留 XML 的触发条件 | `--activate` 注册，但不额外 kickstart；条件可能立即执行 |

固定默认行为不变；复位或接受外部改动不会自动注册。现有任务要刷新注册快照时，优先按真实 Label 使用下面的 `register`，无需重新输入原命令。

## 扩展路径：生成 → 转换 → 预览 → 导入

例：用户明确要每 300 秒运行指定命令，且固定入口不能表达。以下 `/bin/echo` 仅为无害示例，实际替换为用户命令的准确 argv。

```sh
python3 "/ABS/PATH/macos-launch-agent/scripts/xml_to_plist.py" \
  --input - --output "$PWD/maintenance.plist" <<'__PLIST_XML__'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>ProgramArguments</key><array><string>/bin/echo</string><string>周期任务示例</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><false/>
</dict></plist>
__PLIST_XML__
```

用户只要文件时，到此停止。用户要求安装时，先预览最终配置及自动分配的路径：

```sh
python3 "/ABS/PATH/macos-launch-agent/scripts/launch_agent.py" install-plist \
  --input "$PWD/maintenance.plist" --name 'maintenance-5min' \
  --description '按 300 秒间隔执行指定维护命令' --dry-run
```

核对后去掉 `--dry-run` 导入；用户同意当前会话激活、理解 XML 条件可能立即触发时再加 `--activate`。预览会拒绝未带 `--replace` 的配置变化和未知归属 plist，但不查询 GUI/禁用覆盖，不得声称预览通过就保证注册成功。后续以同名 `install-plist --replace` 更新，或按真实 Label 管理。源文件仅是输入/来源记录；导入后执行不依赖源 plist 的位置，但仍依赖 Program/脚本文件。

## 统一查询与维护

```sh
python3 "/ABS/PATH/macos-launch-agent/scripts/launch_agent.py" list
python3 "/ABS/PATH/macos-launch-agent/scripts/launch_agent.py" status --label '<实际 Label>'
python3 "/ABS/PATH/macos-launch-agent/scripts/launch_agent.py" describe --label '<实际 Label>' --description '新的用途'
python3 "/ABS/PATH/macos-launch-agent/scripts/launch_agent.py" register --label '<实际 Label>' --allow-execution
python3 "/ABS/PATH/macos-launch-agent/scripts/launch_agent.py" run --label '<实际 Label>'
python3 "/ABS/PATH/macos-launch-agent/scripts/launch_agent.py" uninstall --label '<实际 Label>'
```

`register` 重新读取已托管磁盘配置，在锁内复核，必要时停止原实例再注册并更新快照；**可能执行，不是无副作用检查**。`run` 只触发已加载且本地可信注册快照与磁盘一致的任务；快照缺失、未知或漂移会拒绝，不以 `reset-snapshot` 绕过。已运行时不保证创建新实例。

## 管理边界与记录

- `list` 对无管理记录但名称符合托管样式的**普通文件**仅逐项打印一行（路径、Label、归属未知），末尾集中给出 `status`/操作手册入口；不再为每项重复完整停服或删除建议。对于同样命名的目录、符号链接、FIFO 等，采用 `lstat` 不跟随链接，显示“异常条目（非普通文件）”及类型，不把它们当作 plist，也不提供普通文件删除指引。`status` 对普通文件提供完整人工核对步骤，对非普通文件仅提醒核实路径类型；无记录时不查询或控制同名服务。

- plist：`~/Library/LaunchAgents/<Label>.plist`。
- 记录：`~/Library/Application Support/macOSLaunchAgentSkill/Agents/<Label>.json`，用途、类型、来源、路径、磁盘哈希与注册快照；自定义还记录来源 plist 路径/哈希和原 Label。
- 日志：`~/Library/Logs/LaunchAgents/<Label>.stdout.log` / `.stderr.log`；卸载保留日志与外部源文件。
- **LaunchAgents 目录必须是实体目录**：不支持 `~/Library/LaunchAgents` 为符号链接，即使链接目标可用；`list/status` 也会拒绝。请先人工核对原路径及链接目标，改为当前用户所有、群组和其他用户不可写的实体目录（建议 `0700`）后重试；不得让 Agent 自动改动该目录或链接。
- 专属元数据父/子目录均为 `0700`，plist、JSON、日志为 `0600`。`.local.my.agents.lock` 是 LaunchAgents 下常驻的 0 字节 flock 锁文件，不代表存在任务，不应当作 plist 删除。
- 转换审计：专属目录中的 `conversions.log`，`0600` JSONL，只追加 prepared/written/failed 事件；记录路径、时间、程序名和参数摘要，不保存完整参数。它不授权管理任务。
- 转换器的 `source_label` 仅为源 XML 中的 Label（可能缺失），不是安装后才分配的托管 Label。
- 归属须同时满足 **包含所有必填字段且不含未定义字段的完整管理记录**、受控 Label、相应配置类型校验、文件权限及记录哈希；字段不合法、缺字段或无记录均不自动接管。同用户恶意篡改文件/记录不属于安全隔离保证。
- 登录命名身份为 `name:`；固定日历为 `calendar-name:`；自定义为 `custom-name:` 并使用独立 custom 子前缀。未命名固定任务按命令文本与模式派生；改写命令可生成新任务，不自动清理旧任务。
- 命名和未命名是不同任务身份；给现有未命名任务补上 `--name`，或修改已命名任务名称，会创建新 Label。安装器只针对本工具可验证的“同模式、同命令、不同 Label”任务提示可能重复，不自动合并、替换或卸载；应先 `list/status` 确认。
- **持久禁用覆盖不是任务文件**：卸载只删除托管 plist/JSON，不会清理系统保存的 `Label => disabled`。卸载后如明确发现禁用覆盖将输出提醒，不自动 `enable`。日历任务**同名重装只写盘须加 `--defer`**，更新配置另加 `--replace`；登录模式 `install` 默认只写盘；自定义 `install-plist` 不带 `--activate`。`register` 必然尝试注册，不支持这些只写盘参数。注册前须用户明确同意恢复启用。`list` 仅显示短状态，`status` 给完整指引。

## 验证与限制

操作成功必须以实际工具结果为准。XML 能解析只说明格式合法；plutil 通过不证明 launchd 接受全部字段；注册成功不证明业务成功。先做静态预览，不擅自执行用户命令验证。

日历睡眠补跑的说明只适用于对应日历触发，不套用到所有自定义触发；精确调度、重叠、次数、关机/未登录行为不能做超出证据的保证。`launchctl print` 的运行态是已加载/未加载/无法确认三态；`print-disabled` 独立判断持久禁用覆盖，能明确识别时显示已禁用，未知格式不猜测。两者不可混为一谈。未知运行态拒绝破坏性操作，不把任意非零退出视为不存在。回滚不能撤销业务动作或恢复原进程；有旧漂移时恢复以操作前磁盘版本为准。

**固定模板外部改动：** 若只是修改 `ProgramArguments` 等键的值、重新排版或转换 XML/binary 格式，只要仍符合完整托管模板，`status` / `install --replace` / `uninstall` 会优先提示 `accept-plist --trust-current-plist`；该动作只更新管理基线，不重载服务，按需使用 `register --allow-execution`（可能执行命令）。只有确认不要保留时才考虑显式删除。添加 `ProcessType` 等其他有效 launchd 键后，固定模式仍视为异常，不会自动将其接管成托管扩展模式。`status` 会列出与固定模板不同的键。若用户明确要保留业务配置，核对后应经 XML 扩展路径重新设计并执行显式迁移；若用户明确要丢弃该异常任务，先确认 Label/进程，再使用 `repair --action drop-unrecognized --confirm-delete --label '<实际 Label>'`。该清理仅针对存在有效管理记录、文件为安全普通文件且内部 Label 一致的可解析 plist；运行态无法确认时拒绝删除，失败后不自动启动已修改配置。**若想关闭登录自启，不要直接把 `RunAtLoad` 改为 false**：应在配置有效时执行 `uninstall`；若已经改坏，请先恢复有效模板再卸载，或经明确确认后显式清理。日志与持久禁用覆盖仍保留。

**恢复状态矩阵：** 有效记录+plist 缺失 → `remove-orphan`；损坏记录+可识别 plist → `rebuild-record`；损坏记录+plist 缺失 → 核对运行态后 `remove-orphan`（已加载时先人工核实并停止）；有效记录+不可解析 plist → 人工核对、停止、删除已核实文件后 `remove-orphan`；仅同前缀 plist 而无记录 → `list` 仅标记“未管理”，人工核实并处理，不允许自动接管。所有自动清理保持权限、所有权、文件类型与服务状态检查，记录和 plist 不可信时不能调用普通卸载。

详细恢复、跨类型调整与测试方法见 [管理与恢复](references/operations.md)；XML 语义与字段核对见 [自定义 XML 流程](references/custom-xml.md)；参考来源见 [技术依据](references/sources.md)。
