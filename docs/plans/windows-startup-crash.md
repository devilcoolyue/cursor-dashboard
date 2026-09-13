# Windows 后台启动崩溃调查

日期：2026-09-13。状态：已修复可复现的异常退出和错误隐藏问题；用户设备最初的初始化异常尚未确定，Windows 安装包复测待完成。

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

## 验证边界

macOS 上验证了启动失败时父管道保持打开、目录锁冲突、正常 HTTP/系统密钥持久化/归档/退出、Rust 就绪协议解析，以及 Chromium 和 WebKit 的错误展示和重试流程。所有后台测试使用自有临时数据。

这些测试证明应用自身存在并已修复掩盖原始错误的路径，不证明用户机器的最初初始化失败一定是权限、目录占用或任何特定原因。需用重新构建的 Windows 安装包读取实际错误分类和启动日志后继续定位；不以此宣称 Windows 实机已修复。
