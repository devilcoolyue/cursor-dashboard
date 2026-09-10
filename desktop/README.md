# Cursor Panel 独立桌面

P4 的正式 Vue 前端、Tauri 壳与随包 Python 业务核心。本地运行、切换、加密归档和密钥恢复见 [桌面操作文档](../docs/v2-desktop-operations.md)，平台验证边界见 [P4 报告](../docs/plans/p4-verification.md)。

```bash
uv sync --locked
npm --prefix frontend ci
npm --prefix desktop ci
npm --prefix desktop test
npm --prefix desktop run build
```

开发使用 `npm --prefix desktop run dev`。macOS 构建 `.app` 与 DMG，Windows 构建 NSIS，产物位于 `src-tauri/target/release/bundle/`。安装包不要求用户安装 Python、Node 或 uv。

```bash
uv run --project desktop/sidecar --frozen python desktop/scripts/desktop-smoke.py \
  'desktop/src-tauri/target/release/bundle/macos/Cursor Panel.app/Contents/MacOS/cursor-panel-desktop' \
  --output desktop/output/p4-install.json
```

探测使用临时合成数据库与系统密钥条目，执行冷/热启动与壳异常退出，清理自己创建的条目。`verify-native.py` 通过临时 GUI 测试真实系统退出/重启适配，不读取或控制真实 Cursor。CI 见 [.github/workflows/p4-desktop.yml](../.github/workflows/p4-desktop.yml)。

历史 P0 原型使用 `npm --prefix desktop run build:probe`，保留独立配置、入口、固定 fixture 与 `smoke.py`，结论见 [P0 报告](../docs/plans/p0-verification.md)。正式产品不会把 P0 二进制或模拟前端作为附加应用打包。
