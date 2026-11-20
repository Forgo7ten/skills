# 技术依据与适用性

本 Skill 的行为约定以随包代码和测试为准。下面为核对 launchd/属性列表语义使用的第一方来源；Apple 归档手册不是当前每个系统版本的保证，新增字段优先核对目标 Mac 上的 `man launchd.plist`、`man launchctl`。

1. Apple：Creating Launch Daemons and Agents。
   https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html
   用于核对用户级 LaunchAgents 位置、Label/参数及服务模型。目录决定运行层级，而不是简单“XML 已通过”就代表正确安装。

2. Apple：Scheduling Timed Jobs。
   https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html
   用于核对 StartCalendarInterval、组合日历字段、日历任务睡眠/关机区别。不能把日历补跑说明推广成所有触发方式的保证。

3. Apple 开源归档：launchd.plist(5)。
   https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5
   归档日期为 2009 年，供对照键类型、Program/ProgramArguments、KeepAlive、RunAtLoad、用户/系统字段。旧文档与后续系统可能不同，不能仅据此宣称当前 StartInterval 的睡眠行为或每个字段都可用。

4. Python 官方：plistlib（3.10 文档）。
   https://docs.python.org/3.10/library/plistlib.html
   标准库支持 XML/binary plist 及基本数据类型；XML 解析会忽略未知 XML 元素。因此转换器另做结构/重复键/深度校验，而不是只调用 loads 后即认为输入完全有效。

5. Apple：launchctl(1)（手册）。
   https://manp.gs/mac/1/launchctl
   用于核对 `enable/disable` 的持久启用状态与 `print-disabled <domain-target>` 的诊断入口。`print` 与 `print-disabled` 是不同视角，诊断文本格式不保证稳定；本工具只保守识别明确匹配 Label 的状态，不依赖其不可保证的完整结构。

核对日期：2026-10-21。没有在当前 Linux 验证环境中执行真实 macOS launchctl；保留版本、系统权限、实际业务行为的实机验证边界。
