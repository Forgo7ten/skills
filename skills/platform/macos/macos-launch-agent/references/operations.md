# 统一管理、恢复与行为边界

## 身份、记录与状态

固定 login、固定 calendar、自定义 custom 是独立身份。命名登录沿用 `name:`，日历 `calendar-name:`；custom 使用 `custom-name:` 且 Label 带 `local.my.agents.custom.`。稳定名称与模式决定命名任务身份；未命名固定任务由命令文本派生。修改命令文本、名称或切换配置类型可能是新任务，不能隐瞒旧任务仍存在。

所有任务的 Label 使用 `local.my.agents.` 前缀。Agent 必须提供语义明确的简短英文 `--name`，中文名称先翻译；没有名称时根据用途生成并报告。脚本中文 `--name` 仍会退化为 `job-<hash>`，但不作为 Agent 的默认行为。命名和未命名任务是不同身份，名称变化不会自动替换原任务。对同模式同命令的其他有效托管记录，安装器只打印重复提醒；用户需决定是否保留并存、卸载原任务。

**LaunchAgents 路径限制**：`~/Library/LaunchAgents` 必须为当前用户所有的实体目录；即使符号链接指向正常目录，本工具也会拒绝 `list/status/install` 等入口。目录不能向群组或其他用户开放写权限，建议权限 `0700`。若出现“拒绝使用符号链接目录”，先人工核实原路径、真实目标及已有任务，再用实体目录重试；工具不会自动解除链接、移动原有 plist 或调整系统状态。

管理授权依赖有效 JSON、受控 Label、相应 plist 校验、文件所有者/权限和磁盘哈希。新 custom 记录包含 `source_type=plist`、源文件路径/哈希、原 Label、稳定名称；原文件不属于管理器的删改范围。没有记录的任务，即使长得一样也不自动接管。

`list` 首先列出有效记录，针对孤立文件给出 `remove-orphan`；坏 JSON 的 stderr 显示路径、实际解析原因及按 plist 状态选择的操作出口。它还单列文件名符合 `local.my.agents.` 前缀、却没有管理记录的外部 plist，明确标记“未管理”，不查询、接管或停止同名服务。`status` 成功读取记录退出 0；当前是否加载另看三态结果。三态为已加载、明确未加载、无法确认，不把查询失败当作不存在。无法确认时，不进行停止、触发或需要可靠状态的重注册。

**系统禁用覆盖与运行状态是两回事。** `launchctl print-disabled "gui/$(id -u)"` 可能输出 `"<Label>" => disabled`。`status/list` 会独立显示该状态；若服务还在运行，不应说进程已经退出；解析失败只能报告“无法确认”。自定义 XML 中的 `Disabled`（true/false 均拒绝）属于管理状态，不是 `RunAtLoad` 或 `StartInterval` 这样的触发条件。

哈希快照描述“本工具曾成功注册哪一份文件”，不是读取 launchd 内部的当前完整配置。退出登录后系统可能自行重新加载，外部程序也可操作服务；本工具不会伪称已准确还原这些变化。

## 注册与执行

所有类型都可使用：

```sh
python3 scripts/launch_agent.py register --label '<实际 Label>' --allow-execution
```

只允许已托管且磁盘/记录匹配的任务。注册前在锁内重新核对文件，防止查询后又被更改。旧实例可能被停止；重新 bootstrap 按配置触发，可能立即执行业务。不会额外 kickstart，也不自动 enable/disable 服务的持久启用状态。

若用户在“系统设置 → 通用 → 登录项与扩展”里关闭后台项，或用 `launchctl disable gui/<uid>/<Label>` 持久禁用，之后的 `register` / `--activate` 可能收到泛用 EIO（如退出码 5）。管理器会在注册前检查禁用覆盖；能明确识别时拒绝激活并给出恢复提示；bootstrap EIO 时也尝试诊断，无法识别不猜测。只有用户明确同意，才在其 Mac 上执行 `launchctl enable "gui/$(id -u)/<真实 Label>"`，或通过系统设置打开，然后再次 `register --allow-execution`。**enable 会跨重启改变持久状态；本工具绝不自动执行。EIO 并非禁用的充分证据。**

卸载成功只意味着托管 plist 和管理记录已删除，不代表 launchctl 的持久 `Label => disabled` 覆盖已清除。卸载后只在明确识别到禁用覆盖时输出提醒；同名新任务可以重新只写盘，但注册前需由用户明确同意改变持久状态。**日历同名重装必须用 `install --defer` 才仅写盘**；已有日历仅更新配置时用 `install --replace --defer`。登录 `install` 默认只写盘，自定义 `install-plist` 不带 `--activate` 只写盘。`register` 始终尝试注册，本身没有上述只写盘参数。`list` 仅展示简短状态，`status` 提供完整指引。工具不会自动 `enable/disable`。

`--dry-run` 对固定与扩展任务都检查当前磁盘/管理记录和 `--replace` 要求；它不创建文件、不查询 launchctl，因此**不能检查 GUI 域和持久禁用覆盖，也不能保证 bootstrap 成功**。实际提交会在锁内重新检查。

固定 login 安装默认只写盘；calendar 默认注册、`--defer` 只写盘；custom 默认只写盘、`--activate` 注册。只写盘不改变当前已注册实例，旧命令/旧日程可能继续运行。只有用户明确需要立即执行才使用固定 `--start-now` 或后续 `run`。

`run` 拒绝无可信快照、unknown、哈希漂移或未加载任务；不以复位记录绕过。已运行时不保证新建第二个实例，不使用 `-k` 强行杀死旧实例。

## 恢复操作

每一步先核对 `status`、真实 Label、用途和当前 plist，不直接删除 JSON 或手写 launchctl 命令绕过管理器。下面示例在 Skill 根目录执行；Agent 使用绝对脚本路径。

### 外部改动，但配置仍可信

```sh
python3 scripts/launch_agent.py repair --label '<实际 Label>' \
  --action accept-plist --trust-current-plist
```

只用于有有效管理记录、plist 存在且仍符合相应托管模板的任务（包括修改了固定命令、调整了合法日历触发键，或仅由 XML 转为 binary plist 导致哈希改变）。`status`、`install --replace` / `--dry-run`、`uninstall` 在发现此类哈希漂移时会优先展示该恢复入口，而不是只建议删除。用户必须先核对磁盘配置并明确接受，不能自动信任修改。更新磁盘基线，不注册。原有可信注册快照被改变时记 `accepted-plist`；固定模式改变记 `mode-changed`。此前没有注册快照时不凭空制造 unknown。custom 接受改动后不再声称当前配置仍等于原来源文件。

要刷新注册，用 `register --allow-execution`，不需要重复原命令/源 XML。登录任务可能因 RunAtLoad 执行；自定义可能因自身触发条件执行。单独 `install --replace`（login 不带 --start-now）不是重注册。

### 不想执行，只放弃旧注册快照

```sh
python3 scripts/launch_agent.py repair --label '<实际 Label>' \
  --action reset-snapshot --trust-current-plist
```

清除 `last_registered_sha256`、时间和 unknown 标记；只改记录，不调用 launchctl、不停止服务。describe/常规更新继续可用，但 run 仍因无可信快照而拒绝。不能把这个操作报告成“服务已恢复同步”。

### plist 丢失，只剩有效记录

```sh
python3 scripts/launch_agent.py repair --label '<实际 Label>' \
  --action remove-orphan --confirm-stop
```

先检查已注册状态；需要时先停止并确认已停止，再删孤立记录。状态不明时保留记录。日志不删；不是清理任意同名服务的入口。

### JSON 损坏、plist 仍可识别

```sh
python3 scripts/launch_agent.py repair --label '<实际 Label>' \
  --action rebuild-record --trust-current-plist --description '重新确认的真实用途'
```

只允许安全普通文件中已有损坏记录，不接管无记录任务。固定 plist 仍按固定模板校验；custom 子前缀按扩展托管外壳校验。源文件关联/原 Label/名称未必能从损坏 JSON 恢复；新快照标 `rebuilt`，run 暂停。可按真实 Label register，或只 reset-snapshot，但后者不代表重新注册。

### 状态缺口：损坏记录与缺失 plist

记录与文件是两项独立状态。`status`、`list`、安装预检及普通管理命令共享以下建议，不应对所有损坏记录一律推荐重建：

- **有效记录 + plist 缺失（整个 LaunchAgents 目录缺失也一样）**：执行 `repair --label '<实际 Label>' --action remove-orphan --confirm-stop`。会确认服务状态；有效记录可供确认归属，仍加载时先停止并验证，无法确认就保留记录。
- **损坏 JSON + 可识别 plist**：记录是安全普通文件、plist 仍符合当前固定或自定义托管结构时，人工核实后用 `repair --label '<实际 Label>' --action rebuild-record --trust-current-plist --description '确认后的用途'`。只有有效记录的场景不能误用该操作。
- **损坏 JSON + plist 缺失**：可用 `remove-orphan --confirm-stop` 清理当前用户所有、权限安全的孤立 JSON，**不会自动重建记录或接管服务**。若同 Label 服务仍加载，损坏记录无法证明运行服务归属，工具拒绝自动 `bootout`；先人工查看 `launchctl print "gui/$(id -u)/<实际 Label>"`，核实并手工停服、确认已卸载后重试。元数据文件若是符号链接、权限不安全或被外部并发修改则拒绝删除。
- **有效记录 + 无法解析 plist**：不能验证内部 Label，因此 `drop-unrecognized` 也不能自动删除。先人工核实文件、运行态，必要时手工 `launchctl bootout "gui/$(id -u)/<实际 Label>"` 并确认服务停止；仅删除确认为该任务的 `~/Library/LaunchAgents/<实际 Label>.plist`，然后通过 `remove-orphan --confirm-stop` 清理剩余记录。手动删除文件之前不要只删除 JSON。
- **同前缀 plist + 无管理记录**：它只具有相似文件名，不构成任何所有权证明。`list` 显示为“未管理”；`status/安装/卸载` 均拒绝接管，必须人工核实来源和服务加载情况。确认废弃后人工停服并删对应文件，不能由本工具代为停用未知服务。


- **未管理路径类型及列表显示**：只有 `lstat` 判断为普通文件的同前缀 `.plist` 才显示为“未管理文件”；`list` 逐项只打印一行，统一在末尾提示通过 `status --label` 或本手册查看详细人工核对流程。若该路径实际为目录、符号链接、管道等，则另标“异常条目（非普通文件）”，不跟随符号链接、不尝试解码，不建议将其按普通 plist 直接删除。`status` 也按路径类型区分提示；没有有效管理记录时绝不查询或操作同名 launchctl 服务。

`remove-orphan` 只有在 plist 确实不存在、管理记录为安全普通文件且运行态可确认时才能完成；文件重新出现或清理期间管理记录被修改就拒绝删除。恢复指引不等于自动授权，Agent 不得自行执行人工清理指令。

### 外部添加字段导致固定模板无法识别：显式清理，不接管

外部在固定 plist 添加 `ProcessType` 等键，即便该键被 launchd 接受，固定模板也会拒绝将它作为本 Skill 的正常固定任务管理。**先确认是否真的无法识别**：符合模板但哈希不同的配置优先走 `accept-plist`，不要用删除代替恢复。`status` 的异常提示会列出多余、缺失和值不同的键；正常 `uninstall` 拒绝该异常文件。

用户**明确选择丢弃此任务、允许停止当前服务并删除托管文件**后，使用：

```sh
python3 scripts/launch_agent.py repair --label '<实际 Label>' \
  --action drop-unrecognized --confirm-delete
```

此操作需要有效的本 Skill 管理记录，当前 plist 必须为当前用户所有、安全普通文件、可解析且其内部 Label 与记录一致。没有记录、符号链接、其他 Label、解析失败或无法确认加载状态时都会拒绝。若正在加载则先 `bootout` 并确认停止，再删除当前异常 plist 与 JSON；保留日志、源文件和系统禁用覆盖。不自动 `enable` 或重新注册。若删除失败，将尽力恢复文件/记录，但**不会自动 bootstrap 被外部修改的配置**；可能需要人工核对服务状态。与 `accept-plist` 不同，此动作完全不接受修改后的配置为可信基线，也不能用来删除任意外部任务。

如果用户只是想关闭登录自启动，应该对仍符合模板的任务使用 `uninstall`，不要手动修改 `RunAtLoad=false`：这会破坏固定登录模板并封锁正常管理操作。已修改时可先恢复有效模板再正常卸载，或确认丢弃后使用上述异常清理。

权限、所有权、符号链接错误不自动修复；应先由用户核对来源，不得自动 chmod 外部文件或删记录。

## 更新、卸载与回滚

同类型内容更新需要 `--replace`，同名自定义的配置变化不会换 Label。卸载先停止并确认服务不再加载，然后删 plist 与 JSON；仍加载或状态不明就保留文件。卸载保留运行日志、转换日志、输入 plist 和业务脚本。

事务是进程内的尽力回滚，不是崩溃一致性数据库；强制断电、磁盘持续损坏、进程被杀、并行外部修改可能留下需要 repair 的状态。文件与服务的恢复错误分别报告，不伪称完整成功。

如果恢复旧任务需要 bootstrap，登录/自定义条件可能再次执行。**回滚恢复操作前的磁盘版本，不保证恢复之前实际运行的配置或同一个进程**；尤其已有 defer 漂移时，两者并不相同。恢复注册成功后会同步记录实际重新注册的文件哈希，防止快照仍指向更早版本。业务副作用无法用文件回滚撤销。

## 跨类型配置调整

固定与 XML 任务独立，是防止同名跨类型误覆盖的设计，不应被理解为自动去重。同一用户要求把现有固定任务扩展为 XML 时，先识别旧 Label、预览新任务，并明确是否允许并存/停机；不能直接创建后遗忘旧任务。

本版不提供跨 Label 的原子迁移，不自动停止现有的外部任务。迁移前保留旧配置与描述，按用户确认的停用、导入、激活顺序执行；任一步失败就报告两边的实际状态，不继续做可能导致重复执行的动作。不承诺多条命令组成的人工迁移能够自动回滚。

外部应用或人工放入的无管理记录 plist 不属于本工具管理范围；本工具不会自动接管、删除或为其创建记录。开始新任务前应确认没有其他任务执行相同的业务逻辑，避免重复运行。

## 本机验收

在临时用户/隔离 HOME 中用无害测试命令，手动验证 plutil、GUI 域注册、激活风险、查询、卸载和日志，再测试真实业务。退出/重新登录、睡眠唤醒、不同 macOS 版本及系统权限须实机验证。不要对用户真实命令进行未授权测试；本包自动化测试只模拟 launchctl。

## 部分安装且回滚未完成

若新服务已被注册但记录提交失败，并且无法确认服务已停止，管理器会尽量保留本次生成的 plist 与有效恢复记录，而不是删除证据使其变成无记录服务。记录标记 `recovery-failed`，`list/status` 可见，`run` 拒绝。原有任务回滚后服务状态不确定时也设置该原因，不能继续信任旧快照。核对实际 Label 与配置后，可以选择 `register --allow-execution` 重新注册或 `uninstall` 清理；查询未知或磁盘持续不可写时仍会拒绝或报告恢复不完整，不保证错误状态能被自动解决。
