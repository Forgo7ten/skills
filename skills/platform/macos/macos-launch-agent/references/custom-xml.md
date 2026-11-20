# 自定义 XML：配置扩展，不是管理旁路

## 何时切换

固定命令行能完整表达需求时继续用 `install`。下面情况适合 XML：StartInterval、路径/队列触发、不同星期对应不同时间、直接的 Program/argv、指定环境变量、条件 KeepAlive、应用明确支持的 sockets/MachServices。授权失败、坏记录和无 GUI 不是能力缺口，不能借 XML 解决。

只要用户已经明确表达扩展需求，Agent 可自行生成最小 XML，不必再要求用户手写 XML。但命令、时间/条件和安装意图必须明确。遇到未验证的字段，先查目标 Mac `man launchd.plist`；本 Skill 不复制一份长期不更新的全量 launchd schema。

## 四步流程

1. 根据用户语义编排完整 Apple 属性列表 XML，不编造命令、账号、年份字段或环境依赖。省略 Label、StandardOutPath、StandardErrorPath、Umask。
2. 用独立 `xml_to_plist.py --input - --output <普通目录中的文件.plist>` 校验并导出。字符串用 `--xml`，文件用 `--input`；输入为 Apple plist XML，不是任意 XML。输入上限 2 MiB、dict/array 最多 64 层。重复键、实体声明、非法结构会拒绝。
3. 用户需要安装时，以稳定 `--name` 和真实用途调用 `install-plist --dry-run`，逐项核对最终命令、触发、Label、工作目录和日志。只导出时不进行本步。
4. 去掉 `--dry-run` 导入；明确同意注册副作用时加 `--activate`。以后用同一名称 + `--replace` 更新，统一 `list/status/register/describe/run/repair/uninstall`。不直接复制进 LaunchAgents。

不要在待导入 XML 里填写 `Disabled`（即使是 false 也会拒绝），因为它是启用状态而非任务触发条件。任务如被系统设置或 `launchctl disable` 关闭，须由用户明确同意后重新启用，再注册；Agent 不自动修改持久禁用覆盖。

导入器直接读取 XML/binary plist，但仍复用转换器的结构验证；不能绕过转换器检查。源路径不允许来自名为 LaunchAgents/LaunchDaemons 的目录树（包含解析后的符号链接路径），防止把已存在的任务误当新配置接管。导出文件是内容快照，不是运行依赖。

## 管理外壳与自由字段

管理器唯一分配 Label，并固定 stdout/stderr 日志和私有 Umask。工作目录可以指定已有绝对目录，未指定则为用户主目录。冲突日志/Umask 会报错，Agent 不应为了让错误消失而擅自改变用户明确要求；应说明可用的应用日志配置或当前边界。

其余合法业务字段保持原样，不统一套 `/bin/zsh -lc`，不补 RunAtLoad/KeepAlive 默认字段，不把多个日历字典重排成错误的组合。未知键不会被静默丢弃；库只检查常见键类型，不保证系统认识未知键。解析合法不等于 launchd 合法，launchd 合法也不等于任务程序支持该激活方式。

Program 或 ProgramArguments 至少一个存在，真实可执行程序必须是绝对路径。提供 Program 时，argv[0] 可为用户明确提供的程序显示名。参数中的空格不加 shell 引号，`$HOME` 不会自动展开。需要 shell 操作符时，只有用户的命令确实需要才显式使用 shell 解释器及单独命令参数。

当前用户安装拒绝 UserName、GroupName、RootDirectory、BundleProgram 等身份/系统服务字段；不能靠 XML 变成系统任务。不得自动定义监听服务来“补足”原本不支持该协议的脚本。

## 示例一：周一 09:00 与周五 18:00

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>ProgramArguments</key>
  <array><string>/bin/echo</string><string>定时示例</string></array>
  <key>RunAtLoad</key><false/>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
  </array>
</dict>
</plist>
```

这是两个独立触发规则，不是两个星期乘以两个时间。缺失的日历字段有其通配含义；例如只给 Minute 不能解释成“每天某时刻”。不要随意补成 09:00，也不要用 Year 模拟一次性某年任务。

## 示例二：间隔、参数和环境

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>ProgramArguments</key>
  <array><string>/bin/echo</string><string>含空格的一个参数</string></array>
  <key>EnvironmentVariables</key><dict><key>APP_MODE</key><string>maintenance</string></dict>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
```

这里的 `/bin/echo` 只是文档示例，不是用户的真实业务命令；不要把它当成已实现备份或维护。

## 激活、边界与风险

`install-plist --activate` 只注册配置，不再额外 kickstart。即使 RunAtLoad=false，KeepAlive 条件或其他触发也可能运行；不要承诺注册期间绝不执行。只需要文件、不允许下次登录执行时，只导出，不做托管导入。

日历补跑规则不能自动套给 StartInterval 或 WatchPaths；不同触发字段/系统版本须分别核对。WatchPaths 不是可靠文件变更队列，也不会自动把变化的文件路径作为参数传给任务；业务去重、扫描、持久队列、长任务互斥由任务程序负责，不能靠 XML 虚构。

无论普通目录还是自动加载目录，转换器都拒绝托管前缀 Label。转换器始终拒绝直接写自动加载目录。手工移动文件或事后改 XML 不在这些防误用检查的保证内；已安装任务仍需有效管理记录与哈希。

从已托管固定任务升级到扩展 XML 是跨身份迁移。先展示旧 Label、新预览与是否允许并存，明确处理旧任务；不要把独立 `--replace` 当成跨身份事务。没有有效管理记录的外部任务不自动接管或停止。
