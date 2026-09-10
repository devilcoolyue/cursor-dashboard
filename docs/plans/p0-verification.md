# P0 验证报告

实施日期：2026-09-09；完成：2026-09-10。状态：P0 已完成。实施分支：`feat/v2-p0`。

结论：采用 Tauri 2 + Vue 3 + 随包 Python 继续实施 V2。macOS arm64 和 Windows x64 的构建、安装产物运行、私有通道、系统凭证库以及正常/异常退出均已通过。Windows CI 首次界面就绪约 18.8 秒，作为 P4 启动性能优化基线；尚未对其他平台和正式签名发行作出支持承诺。

## 基线

legacy/main 基线提交为 `52d0a9255b798dce21d97efbc3739bb46d100b96`，包版本 `1.4.0`。P0 新增代码在 `desktop/`、`frontend/` 和 CI 中，未改动现有 `cursor_dashboard/` 业务实现与根目录运行依赖。

本机：macOS 15.6.1 arm64；Python 3.12.13、Node 25.1.0、npm 11.6.2、Rust/Cargo 1.95.0、uv 0.11.3。根目录 `uv sync --locked` 成功。

完整旧测试：181 项，172 通过，9 跳过，无失败，耗时 18.483 秒。跳过项为 `PowerShellDiscoveryTest` 的 6 项、`PowerShellProgressTest` 的 2 项及 PowerShell 下载命令测试的 1 项，原因是本机缺少 PowerShell。Node SQLite 集成测试已执行。现有 4 个 JS 文件语法检查通过。

测试通过环境变量和测试 fixture 使用临时库。日志保存在被忽略的 `output/p0/baseline.txt`。后续可运行 `python desktop/scripts/legacy-baseline.py` 复现，Windows CI 运行上述 9 项 PowerShell 测试，macOS CI 运行完整测试，避免把旧测试里的 `/bin/bash` 当作 Windows 可用路径。

## 新增可验证结果

- `frontend/`：Vue 3 + TypeScript，随包展示两个 `.test` 模拟账号；类型检查与生产构建通过。
- `desktop/sidecar/`：PyInstaller 单文件 FastAPI/Uvicorn 后台，含 Python、SQLite 与 JSON 资源，独立锁定依赖。
- `desktop/src-tauri/`：私有 stdin 握手、随机 loopback 端口、有限 IPC、系统凭证库探测和退出清理。
- `desktop/tests/`：资源独立性、认证/Host/Origin 拒绝、未开放接口、父管道 EOF 清理等 3 项契约测试；源码和冻结产物模式各 3 项全部通过。
- `desktop/scripts/smoke.py`：安装产物重定位、无开发工具 PATH、Vue DOM 回执、进程清理及性能记录。
- `.github/workflows/p0-desktop.yml`：macOS/Windows 构建、平台基线、冻结后台测试与安装产物探测。

## 平台实测

### macOS 15.6.1 / arm64 本机

| 检查 | 结果 |
| --- | --- |
| release 应用 + DMG | 构建成功，首次 Rust release 编译 3 分 30 秒 |
| .app 目录逻辑大小 | 补齐安装图标后的产物本体 27,179,641 字节，约 25.92 MiB |
| DMG | 补齐安装图标后的产物 19,671,243 字节，约 18.76 MiB |
| 随包后台 | Python 3.12.13、SQLite 3.50.4，`frozen=true` |
| 后台就绪 | 最终图标配置产物 890 ms；此前样本 760–873 ms |
| Vue 界面就绪 | 最终图标配置产物 1,858 ms；此前样本 1,385–1,923 ms，均渲染 2 个账号 |
| 启动到外部报告被读到 | 最终图标配置产物 2,201 ms（含 200 ms 轮询误差） |
| 正常模式进程树 RSS 样本 | 最终图标配置产物 122,732,544 字节，约 117.05 MiB |
| Keychain | 随机合成条目 set/read/delete 通过 |
| 路径与运行时 | 中文/空格临时目录、移除开发工具 PATH、清理 Python/DYLD 环境后通过 |
| 退出 | 正常退出无残留；强制结束壳进程后 Python/引导进程自行退出 |
| DMG 验证 | 只读挂载 DMG、复制应用到临时目录、运行同一探测，再卸载镜像，通过 |

时间来自少量独立启动样本，不是性能分位数或稳定性能保证。RSS 统计壳及观察到的子进程，共享页可能重复计数，系统托管的 WebView 进程可能未包含，不代表整台系统的应用增量内存。强制结束模式不采集空闲内存。JSON 中的 `relocated_bundle_bytes` 按整个探测目录统计，包含小于 1 KiB 的原生报告；上表应用本体大小另按 `.app` 目录核算。

`otool -L` 检查桌面可执行文件与 Python 引导文件未发现 Homebrew、用户虚拟环境或开发目录的动态库依赖；应用实测覆盖了打包内部模块加载。产物为开发验证用途，未完成正式签名/公证和最低系统版本测试。

本机结果文件：`desktop/output/normal-icons.json`；此前样本 `normal.json`、`crash.json`、`dmg-install.json`，均在忽略目录。

### GitHub Actions 平台验证

最终通过的运行：[34421962497](https://github.com/devilcoolyue/cursor-dashboard/actions/runs/34421962497)，实现提交 `433fd0fbd53a53a9dccdd6676dcd4089d31fdd2d`。两个 job 的依赖安装、基线、源码契约、打包、冻结契约和安装后探测全部成功；后续收尾仅更新说明文档。

| 检查 | macOS runner / arm64 | Windows runner / x64 |
| --- | --- | --- |
| 旧测试 | 完整 181 项通过，无跳过，20.300 秒 | PowerShell 相关 9 项通过，无跳过，12.738 秒 |
| 新后台契约 | 源码 3 项、冻结产物 3 项全部通过 | 源码 3 项、冻结产物 3 项全部通过 |
| 运行来源 | 构建的 .app，复制到中文/空格临时目录 | NSIS 静默安装到含空格目录，再复制安装出的程序到中文/空格临时目录 |
| 随包 Python / SQLite | 3.12.10 / 3.49.1 | 3.12.10 / 3.49.1 |
| 正常模式后台就绪 | 761 ms | 3,232 ms |
| 正常模式 Vue 界面就绪 | 5,874 ms | 18,764 ms |
| 随后异常退出模式的界面就绪 | 3,128 ms | 1,825 ms |
| 正常模式进程树 RSS 样本 | 126,877,696 字节，约 121 MiB | 397,037,568 字节，约 379 MiB |
| 探测目录大小（含小报告） | 28,070,261 字节 | 27,902,727 字节 |
| 系统凭证库 | Keychain 合成条目 set/read/delete 成功 | Windows Credential Manager 合成条目 set/read/delete 成功 |
| Vue 页面 | DOM 渲染 2 个模拟账号 | DOM 渲染 2 个模拟账号 |
| 正常退出与强制结束 | 均无残留进程 | 均无残留进程 |

Windows NSIS 产物为 `Cursor Panel P0_0.0.0_x64-setup.exe`，18,766,422 字节，约 17.90 MiB。运行使用系统 WebView2；未验证 WebView2 缺失时的引导安装。Windows 完整旧测试含 `/bin/bash` 调用，因此只在 Windows 执行对应的 PowerShell 类，完整旧回归在 macOS 执行。

Windows 首次探测比随后一次慢很多；当前没有足够数据区分 WebView2 初始化、Python 解包、杀毒扫描和 runner 负载的影响。P4 需要分段测量多次冷/热启动并优化，不能以第二次 1.8 秒掩盖首次 18.8 秒。内存统计范围不同，不能直接用这两列比较平台效率。

本轮修复并验证了：旧测试的 UTF-8 编码、8.3 短路径比较、Windows 命令行长度，Tauri 安装图标声明，npm 可选平台依赖的完整锁定，以及 NSIS `/D` 的最后一个未加引号参数要求。实际账号切换脚本未修改。

完整产物和 JSON 报告附于上述 CI 运行；本地下载分别在 `output/p0/ci-433fd0f-macos/`、`output/p0/ci-433fd0f-windows/`。验证排除了应用对开发工具 PATH 的依赖，但 CI runner 本身安装有开发工具，因此不将它等同于所有纯净用户系统、最低系统版本或正式签名发行验证。

## 架构与上游结论

- [ADR 0001](../adr/0001-v2-core-boundaries.md) 固化 API v1、固定角色、目录边界、UUID/空间隔离与旧数据映射。
- [ADR 0002](../adr/0002-desktop-runtime.md) 采用 Tauri + 随包 Python，固化桌面私有通道、随包运行时与进程生命周期，并保留 Windows 首次启动优化项。
- [会话矩阵](p0-session-matrix.md) 对应已有模拟竞争测试，明确独立设备会话、RT 重放、客户端退出与撤销仍待 P5 真实上游验证。

P0 不运行旧的真实会话实验工具，不读取真实凭证或客户端数据库。新版身份/权限和实际切换业务分别属于 P1–P5。

## P0 验收与后续项

P0 五项任务均已完成：基线记录、双平台模拟程序、体积/内存/时间与凭证库实测、会话风险矩阵，以及 API/权限/目录/迁移 ADR。技术路线具备进入 P1 的条件。

后续范围明确如下：P1/P2 实现实际数据与权限模型；P4 优化冷启动、验证最低系统版本和更多架构；P5 验证真实 Cursor 多设备续期与撤销；P6 完成签名、公证及正式发行。P0 原型不接触真实账号，不能用来管理或切换生产账号。
