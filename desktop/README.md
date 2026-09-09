# P0 桌面技术验证

这是 V2 的 Tauri + Vue + Python 打包验证程序，只显示随包模拟数据。现有 Web/CLI 入口保持 legacy 行为；本程序不读取账号数据库、不联网查询 Cursor、不执行账号切换。

开发工具：Node 24+、npm、Rust stable、uv，以及 Tauri 对应平台构建工具（macOS Command Line Tools；Windows MSVC C++ Build Tools 与 WebView2）。Python 3.12 由 uv 准备，最终安装包带有 Python 后台。

在仓库根目录执行：

```bash
npm --prefix frontend ci
npm --prefix desktop ci
npm --prefix desktop test
npm --prefix desktop run build
```

开发窗口使用 `npm --prefix desktop run dev`。后端依赖锁在 `sidecar/uv.lock`，前端与桌面 CLI 分别使用 npm lockfile，Rust 使用 Cargo.lock。

macOS 产物位于 `desktop/src-tauri/target/release/bundle/macos/Cursor Panel P0.app` 与相邻 `dmg/`。Windows 安装程序位于 `bundle/nsis/`，发布可执行文件及 `p0-backend.exe` 位于 release 目录。P0 产物尚未配置正式签名、公证或自动更新。

macOS 可从仓库根目录运行完整安装包探测：

```bash
uv run --project desktop/sidecar --frozen python desktop/scripts/smoke.py \
  'desktop/src-tauri/target/release/bundle/macos/Cursor Panel P0.app/Contents/MacOS/cursor-panel-p0' \
  --output desktop/output/normal.json
```

增加 `--kill-shell` 验证壳进程异常结束后的后台清理。Windows 将可执行文件参数替换成 `desktop/src-tauri/target/release/cursor-panel-p0.exe`。

探测脚本把产物复制到临时中文/空格目录，移除 PATH 中的开发工具，等待随包 Vue 渲染真实探测结果，测试随机合成系统凭证条目的写/读/删除并记录时间、体积和有限范围的进程树内存。正常模式自动退出，异常模式只终止该次测试的壳进程。输出不含密钥。

`npm --prefix desktop test` 测试私有 API、资源路径和 EOF 清理；设置 `P0_BACKEND_BINARY` 为冻结后台绝对路径，可对打包产物运行相同契约测试。CI 位于 [.github/workflows/p0-desktop.yml](../.github/workflows/p0-desktop.yml)。

完成范围及平台限制见 [P0 报告](../docs/plans/p0-verification.md)，设计依据见 [ADR 0002](../docs/adr/0002-desktop-runtime.md)。
