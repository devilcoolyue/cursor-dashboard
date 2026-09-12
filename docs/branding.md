# Cursor Panel 图标

![Cursor Panel 图标](../frontend/src/icon.svg)

图标以“开放的 C + 分层面板”为主题。等距透视、斜切边与明暗折面呼应 Cursor 的立方几何；左侧折叠成 C，右侧三条面板表达多个账号、额度与空间的集中管理。中间保留留白，让轮廓在浏览器标签与侧栏的小尺寸下仍能辨认。

石墨绿底色 `#171D1B`，顶部雾白 `#F1F3EE`，侧面灰绿 `#B8C7BE`，面板薄荷绿 `#93DBB5`。固定品牌配色适用于各皮肤的明暗背景；三条面板是品牌图形，不代表实时状态或具体额度。

## 维护

唯一手工编辑的源文件是 [`frontend/src/icon.svg`](../frontend/src/icon.svg)，使用 256 × 256 画布，无字体、外部图片或滤镜依赖。Vue 侧栏、登录入口和网页 favicon 直接引用它。

修改源文件后执行：

```bash
npm --prefix desktop ci # 首次安装依赖
npm --prefix desktop run icons
```

脚本将 SVG 同步到 `desktop/icon.svg` 和 `cursor_dashboard/web/icon.svg`，并使用项目锁定的 Tauri CLI 生成已有的 PNG、macOS ICNS 与 Windows ICO。其他平台的临时导出会自动清理。

macOS ICNS 在导出时将整个图标（含深色圆角底板）居中缩放到画布的 80%，四周各留 10% 透明边距，使 Dock 和启动台的视觉大小与常见 macOS 应用一致。留白通过临时 SVG 的 `viewBox="-32 -32 320 320"` 生成；网页、侧栏、托盘 PNG 和 Windows ICO 继续使用原始画布。

核对 16、32、42 和 128 像素显示，以及浅色、深色背景。桌面系统图标需重新构建应用后生效。

## 启动等待

Web 文档壳和桌面启动页共用 `frontend/src/startup.css`，以居中的品牌图标、状态和加载线展示等待状态。Vue 启动页复用 SVG 源文件，依次动画显示右侧三条面板；减少动态效果时停用动画。加载失败时停止动画并提供重试。

`frontend/public/assets/startup-theme.js` 在界面脚本前读取明暗偏好，使用单独文件以兼容 Web 与 Tauri 的内容安全策略。

以下检查使用合成数据，覆盖两种浏览器中的启动、键盘焦点、12 项卡片开关、缺失上限与提示浮层：

```bash
npx --prefix frontend playwright install chromium webkit
npm --prefix frontend run build
npm --prefix frontend run test:startup-cards
```
