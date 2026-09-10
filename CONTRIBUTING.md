# 贡献指南

先阅读 [仓库维护指引](CLAUDE.md)、[V2 实施计划](docs/plans/v2-architecture.md) 和对应阶段报告。贡献应说明具体问题、最终行为、验证结果及平台限制。开发使用独立分支，保持 legacy 与 V2 数据和认证边界。

## 本地环境与检查

需要 Python 3.10+、uv、Node 22.13+；桌面另需 Rust 和目标系统工具链。CI 使用 Node 24，桌面后台使用 Python 3.12。

```bash
uv sync --locked
npm --prefix frontend ci
uv run --frozen python -m unittest discover -s tests -v
npm --prefix frontend run build
uv run --frozen python dev/release.py check
```

修改 API 后运行 `uv run --frozen python dev/export-openapi.py`、`npm --prefix frontend run api:generate`，提交生成契约。影响页面流程时运行 `test:e2e`、`test:desktop` 或 `test:connected`，相关脚本会启动独立临时服务；修改桌面时还需按 [desktop/README](desktop/README.md) 检查后台、原生适配、Rust 和安装产物。测试跳过、缺少平台或工具必须披露。

测试只使用临时数据库、模拟网关和合成账号。不得提交真实 Cookie、AT/RT、主密钥、运行数据库、加密归档、生成切换命令或包含这些内容的日志/截图。不得对正在运行的真实面板或 Cursor 执行 fixture。真实会话实验应另行明确专用测试账号、用途和副作用。

## 提交与评审

每个 PR 围绕可审查的问题，说明触发条件、修复后行为及相关验证。业务变化同时维护文档与阶段勾选；不要将计划、fixture 或配置支持范围写成已实测能力。不要手工编辑生成的 API 类型，避免无关格式化和运行产物。

数据库 schema 使用新 Alembic revision，旧 revision 冻结。升级要覆盖已提交 WAL、错误密钥、进程锁、失败恢复与重新执行；禁止破坏性 downgrade。变更设备、身份或切换边界时验证实时撤权、一次性消费与旧请求拒绝。

发布候选、手动更新、签名方案和版本一致性见 [发行运维](docs/v2-release-operations.md)，已验证平台见 [支持表](docs/supported-platforms.md)。许可证由维护者在发布前确定；仓库中没有 LICENSE 时不默认授予 MIT 或其他开源许可。确定后贡献按该 LICENSE 接受，不覆盖第三方依赖原有许可。

## 报告问题

使用问题模板填写版本/提交、系统架构、使用入口、复现步骤、预期/实际行为及已脱敏错误。不要公开粘贴认证头、设备会话、切换命令、数据库或密钥。涉及未公开凭证泄露或权限绕过时，使用仓库提供的私密安全报告渠道（如已启用），勿将可利用细节和秘密写入公开 issue。
