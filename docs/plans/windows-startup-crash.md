# Windows 后台启动崩溃调查

日期：2026-09-13。状态：启动修复已完成 Windows 构建与自动化验证；用户反馈同事安装测试包后初步恢复可用。最初触发初始化失败的具体原因尚未确定，未据此认定为特定系统、硬件或权限故障。

## 现场证据

- Windows 11 Pro 25H2，构建 26200.9445，Intel i7-14700KF，x64。
- v0.0.3 界面提示本地后台不可用，点击重试仍报连接中断。
- Windows Application Error 1000：`cursor-local.exe`，故障模块 `python312.dll`，版本 `3.12.10150.1013`，时间戳 `0x67f5153a`，异常 `0xc0000005`，模块偏移 `0x272d6b`。

从 Python 官方 3.12.10 amd64 `core.msi` 提取的 DLL 时间戳与现场一致。该偏移的反汇编是在读取字符串长度，所在函数与 CPython 的 `fatal_output_debug()` 实现对应。CPython 3.12.10 的 `_Py_FatalErrorFormat()` 会先写 stderr，再以空消息调用 `fatal_error()`；Windows 分支继续把消息交给未检查空指针的 `fatal_output_debug()`。因此现场访问违规可能是输出原始致命错误时的二次故障，不能据此确定第一次失败原因。

参考：

- [官方 3.12.10 Windows 构建文件](https://www.python.org/ftp/python/3.12.10/amd64/)
- [CPython 3.12.10 pylifecycle.c](https://github.com/python/cpython/blob/v3.12.10/Python/pylifecycle.c#L2708)

## 已复现的问题与修复

使用临时普通文件作为数据目录，保持父进程 stdin 管道打开：旧后台初始化失败后执行 `sys.exit(1)`，监听线程仍阻塞在 `sys.stdin.buffer.read(1)`。解释器退出时获取不到 BufferedReader 锁，本机稳定出现 `_enter_buffered_busy` 致命错误并以 SIGABRT 退出。该路径会调用 `_Py_FatalErrorFormat()`，与 Windows 事件的二次故障位置相符。

监听线程改用原始文件描述符 `os.read()`，避免占用 Python 缓冲流锁。初始化失败现在通过就绪管道返回固定错误分类、白名单异常类型与数字系统错误码，再正常以状态 1 退出；不传递原始异常文本。Rust 保存并展示错误，写入覆盖式启动日志；重试会重新启动已停止的后台。启动中的并发重试不会重复启动进程，后台存活时继续调用原有解锁接口。

重试直接返回就绪状态时，先启动账号身份加载，再离开恢复页面，避免组件提前卸载导致账号未加载而停留在登录页。

## Windows 测试包与验证结果

最终测试包来自提交 `5b782ebea1d4ab8f252cad6f6125c1d7938659e9`，包含启动修复提交 `5130825` 和重试界面修复提交 `5b782eb`。Windows 工作流 [34741403581](https://github.com/devilcoolyue/cursor-panel/actions/runs/34741403581) 全部成功。

- Python 3.12.10 / PyInstaller 6.22.2，Windows x64 NSIS 完整安装包。
- 源码后台 8 项、冻结后台 4 项、Rust 5 项通过；完整回归 347 项，339 通过、8 跳过。
- 实际安装后的首次启动、再次启动、壳异常退出清理及启动日志检查通过；界面就绪分别为 4526 / 2281 / 2367 ms。
- 桌面、Web 和连接实例页面流程通过，包括重试解锁后的账号加载。
- 安装程序 SHA-256：`9fc71889a58f5e0ab039a3380005e7f0773c3fe982de2a5610433eb3e9f4ed7f`。

该测试包内版本仍为 `0.0.3`，以 `windows-startup-fix-5b782eb` 区分。用户随后反馈同事“好像可以了”，记作实机初步恢复，不替代完整业务验收，也不能据此倒推最初初始化异常的原因。

正式分发应使用更高版本号（建议 `v0.0.4`）重新构建，验证后发布新 Release 及对应签名更新包、`latest.json`；不覆盖已有 `v0.0.3` 附件。同版本测试包不会触发旧客户端的版本更新提示。

## 验证边界

macOS 上验证了启动失败时父管道保持打开、目录锁冲突、正常 HTTP/系统密钥持久化/归档/退出、Rust 就绪协议解析，以及 Chromium 和 WebKit 的错误展示和重试流程。所有后台测试使用自有临时数据。

这些测试证明应用自身存在并已修复掩盖原始错误的路径，不证明用户机器的最初初始化失败一定是权限、目录占用或任何特定原因。若实机再次失败，可保存界面错误分类与 `%LOCALAPPDATA%\dev.cursor-panel.desktop\logs\startup.json` 后继续定位；日志每次启动或重试覆盖上一条记录。
