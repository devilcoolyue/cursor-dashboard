# P0 验证报告

日期：2026-09-09。状态：验证执行中。实施分支：`feat/v2-p0`。

## 基线

legacy/main 基线提交为 `52d0a9255b798dce21d97efbc3739bb46d100b96`，包版本 `1.4.0`。P0 新增代码在 `desktop/`、`frontend/` 和 CI 中，未改动现有 `cursor_dashboard/` 业务实现与根目录运行依赖。

本机：macOS 15.6.1 arm64；Python 3.12.13、Node 25.1.0、npm 11.6.2、Rust/Cargo 1.95.0、uv 0.11.3。根目录 `uv sync --locked` 成功。

完整旧测试：181 项，172 通过，9 跳过，无失败，耗时 18.483 秒。跳过项为 `PowerShellDiscoveryTest` 的 6 项、`PowerShellProgressTest` 的 2 项及 PowerShell 下载命令测试的 1 项，原因是本机缺少 PowerShell。Node SQLite 集成测试已执行。现有 4 个 JS 文件语法检查通过。

测试通过环境变量和测试 fixture 使用临时库。日志保存在被忽略的 `output/p0/baseline.txt`。后续可运行 `python desktop/scripts/legacy-baseline.py` 复现，Windows CI 运行上述 9 项 PowerShell 测试，macOS CI 运行完整测试，避免把旧测试里的 `/bin/bash` 当作 Windows 可用路径。

## 新增可验证结果

- `frontend/`：Vue 3 + TypeScript，随包展示两个 `.test` 模拟账号；类型检查与生产构建通过。
- `desktop/sidecar/`：PyInstaller 单文件 FastAPI/Uvicorn 后台，含 Python、SQLite 与 JSON 资源，独立锁定依赖。
- `desktop/src-tauri/`：私有 stdin 握手、随机 loopback 端口、有限 IPC、系统凭证库探测和退出清理。
- `desktop/tests/`：资源独立性、认证/Host/Origin 拒绝、未开放接口、父管道 EOF 清理等 3 项契约测试；源码模式已通过。
- `desktop/scripts/smoke.py`：安装产物重定位、无开发工具 PATH、Vue DOM 回执、进程清理及性能记录。
- `.github/workflows/p0-desktop.yml`：macOS/Windows 构建、平台基线、冻结后台测试与安装产物探测。

## 平台实测

安装包、系统凭证库、耗时、内存和 CI 结果在执行完成后填入。暂不据此宣称 Windows 或全新系统已经验证。

## 架构与上游结论

- [ADR 0001](../adr/0001-v2-core-boundaries.md) 固化 API v1、固定角色、目录边界、UUID/空间隔离与旧数据映射。
- [ADR 0002](../adr/0002-desktop-runtime.md) 固化桌面私有通道、随包运行时与进程生命周期；Tauri 采用结论由平台实测收口。
- [会话矩阵](p0-session-matrix.md) 对应已有模拟竞争测试，明确独立设备会话、RT 重放、客户端退出与撤销仍待 P5 真实上游验证。

P0 不运行旧的真实会话实验工具，不读取真实凭证或客户端数据库。新版身份/权限和实际切换业务分别属于 P1–P5。
