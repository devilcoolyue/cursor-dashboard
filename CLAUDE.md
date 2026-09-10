# 仓库维护指引

Cursor 多账号额度面板，Python 3.10+，FastAPI + SQLite，原生 HTML/CSS/JS。业务与使用说明见 [README](README.md)，实现和测试见[维护文档](docs/maintenance.md)，配置见[部署文档](docs/operations.md)。

V2 核心逐步实施中：`domain/`、`application/`、`infrastructure/`、`runtime/` 为新分层，使用显式配置、SQLAlchemy/Alembic 和加密凭证。当前 `cursor-panel`/`cursor-quota` 保持原兼容入口，`cursor-core` 是独立本地运维入口，P2 `cursor-api` 提供已认证 `/api/v1`（[运行说明](docs/v2-api-operations.md)）；说明见 [新核心运行文档](docs/core-operations.md)。不要把旧共享鉴权直接接到 V2 数据库。

## 工作约束

- 修改说明前核对实现和调用方；历史现象不能直接写成当前能力。阶段记录放 `docs/archive/`，避免把调试过程持续堆入 README。
- 不读取、复制或输出真实 Cookie、AT/RT、数据库及切换命令。验证使用临时库和模拟数据；真实会话实验的用途与副作用见维护文档。
- 常规额度、明细和切换使用桌面凭证。`ENDPOINTS` / `collect()` 是保留的旧网页接口兼容层，当前两个入口均使用 `DESKTOP_ENDPOINTS` / `assemble_desktop()`。
- 凭证轮换保留数据库租约与条件更新，防止旧请求覆盖新授权；临时错误不撤销凭证。AT/RT 轮换不改变快照 fingerprint。
- 列表只读快照。失败不覆盖最后成功数据，限流优先于认证错误；保留认证失效确认和出站节流，不新增批量强制回源入口。
- 百分比使用上游口径，美元上限仅作估算；保留退化、触顶保护及 `limit_inferred`，不写死套餐额度。不把套餐包含金额当作综合池上限。
- 模型明细按需查询，以 `tier` 分类，缓存读写分开，不内嵌价目表重算花费。保留 Grok 查询及无额度过滤，不通过显示开关改变请求集合。
- 账号普通接口与管理员接口的权限不同。切换命令在出站前和返回前校验权限；常规响应不返回凭证，管理列表只返回时间与状态。
- 本地切换脚本只由使用者执行；保留过期检查、正常退出、含 WAL 的备份和事务回滚。预览命令必须在访问本机 Cursor 前停止。
- 前端使用 `PanelUI`；保留异步响应代次校验、焦点恢复、弹窗滚动约束和用户文本转义。皮肤与明暗保持独立维度，修改默认皮肤需同步 HTML 引导脚本。
- 不提交真实运行数据、截图缓存、生成命令和会话实验文件；不因文档归档删除业务功能或改版本号。
- 新核心的账号查询必须带空间上下文，快照/明细使用 UUID 与授权代次；轮换增加凭证版本但不改变授权代次。凭证保存必须验证旧版本及有效租约，升级/运行必须持有数据目录锁；密钥不可用时拒绝操作，不自动换新或保存明文。

- V2 HTTP 只能从已验证会话构造 Actor；角色/授权/会话变更必须实时生效，成功审计与业务变更同事务。切换票据由可信适配消费，P2 不提供浏览器裸凭证接口；Web 脚本与远程设备身份分别在 P3/P5 接入。

## 常用检查

```bash
uv sync --locked
uv run --frozen python -m unittest discover -s tests -v
node --check cursor_dashboard/web/js/app.js
node --check cursor_dashboard/web/js/admin.js
node --check cursor_dashboard/web/js/ui.js
node --check cursor_dashboard/web/js/glass-motion.js
```

完整测试含 Node SQLite 集成测试，需带 `node:sqlite` 的 Node 22.13+，否则相关用例跳过。浏览器预览优先使用 `dev/preview-admin.py`；旧 `dev/preview.py` 与当前页面模板的失配见[阶段归档](docs/archive/2026-09-07.md)。
