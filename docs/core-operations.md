# V2 核心运行与迁移

P3 增加 `backup`、`restore` 离线命令及 `cursor-remote` 已认证查询入口，见 [Web 备份恢复与 CLI](v2-web-operations.md)。恢复必须指定原密钥和空目标目录，并撤销旧会话/票据。

P1 建立本地运维 CLI 和可调用核心；P2 已增加独立用户认证与 `cursor-api`，见 [P2 API 运行文档](v2-api-operations.md)。本说明覆盖本地运维及旧版导入，命令只操作显式指定的 V2 目录。

`cursor-core` 是拥有文件访问权限的运维者入口。`--actor` 选择数据库中的操作者记录供业务规则检查；它不是远程登录凭证，不能将此命令直接包装为接受任意用户 ID 的公开 API。P2 HTTP 入口从已验证会话构造 Actor，并在业务操作中复查当前会话与权限。

## 初始化

在项目目录完成 `uv sync --locked` 后，准备仅当前运维用户可访问的密钥目录。下例路径是新建示例，正式部署替换为自己的路径。

```bash
mkdir -p ./v2-secrets
chmod 700 ./v2-secrets
uv run --frozen cursor-core --key-file ./v2-secrets/master.json keygen
uv run --frozen cursor-core --data-dir ./v2-data --key-file ./v2-secrets/master.json \
  init --owner owner@example.test --name '导入的共享账号' --kind team
```

`init` 输出 `user_id` 和 `workspace_id`，后续命令使用这两个 ID。初始化创建没有登录密码的身份记录；需要登录时再运行 P2 `server-init --login 同一邮箱`，保留该用户及空间并设置密码。重复 `init` 或 `keygen` 会拒绝覆盖现有库或密钥。

也可同时设置 `CURSOR_CORE_DATA_DIR` 和 `CURSOR_CORE_KEY_FILE`，命令省略路径参数。`CURSOR_CORE_MODE` 接受 `local`/`server`，`cursor-core` 本身不监听 HTTP；`cursor-api` 要求显式 server 模式及已初始化认证。新核心不沿用旧 `DATABASE_PATH` 或 `ACCOUNTS_PATH`。

文件结构：数据目录包含 `core.db`、SQLite WAL/SHM 和 `.core.lock`；密钥文件单独存放。POSIX 密钥文件必须限制为 owner 访问，SQLite 文件设为 0600。Windows 使用目录继承的 ACL，部署时将数据与密钥目录设为仅运行用户可访问；系统凭证库的正式接入仍在 P4。

## 预检与导入

先停止旧服务和 CLI，使用 SQLite backup API 或 `.backup` 生成独立备份。不要仅复制正在使用的主库文件。以下 `legacy-backup.db` 指已准备好的独立备份，命令不会自动访问当前账号库。

```bash
uv run --frozen cursor-core preflight ./legacy-backup.db
uv run --frozen cursor-core --data-dir ./v2-data --key-file ./v2-secrets/master.json \
  import-legacy ./legacy-backup.db --actor USER_ID --workspace WORKSPACE_ID
uv run --frozen cursor-core --data-dir ./v2-data --key-file ./v2-secrets/master.json verify
```

预检只输出源摘要、schema 版本、账号/标签/可迁移快照数量和跳过原因。Cookie、AT/RT、邮箱与姓名不会出现在预检报告。支持现有旧 schema v1–v4 的账号格式，未知版本或规范化邮箱/subject 冲突会拒绝。

目标实例必须没有账号或其他导入记录，目标空间必须是当前操作者拥有的 team 空间。所有旧账号归该空间，部门变为标签，旧 Cookie/AT/RT 加密保存；旧共享访问和管理员会话不继承。

导入在一个事务中完成，失败后可以修复问题重试。同一份来源再次导入返回 `already_imported: true`，不会覆盖后续编辑或恢复已删除账号。来源文件内容发生变化视为另一份备份，不会自动合并进已有实例。

`verify` 检查 schema、SQLite 完整性、外键和每个账号的凭证可解密性；不请求上游验证账号是否仍有效。不能把可解密性检查解释成上游授权成功。

## 查看、刷新与明细

```bash
uv run --frozen cursor-core --data-dir ./v2-data --key-file ./v2-secrets/master.json \
  list --actor USER_ID --workspace WORKSPACE_ID
uv run --frozen cursor-core --data-dir ./v2-data --key-file ./v2-secrets/master.json \
  refresh --actor USER_ID --workspace WORKSPACE_ID --account ACCOUNT_ID
uv run --frozen cursor-core --data-dir ./v2-data --key-file ./v2-secrets/master.json \
  detail --actor USER_ID --workspace WORKSPACE_ID --account ACCOUNT_ID
```

`list` 仅读取快照，不查询上游。`refresh` 和 `detail` 会调用 Cursor，必要时续期并更新加密凭证；失败状态保留最后成功快照。新核心没有“一次强制刷新全部”的入口。

所有命令都获取数据目录锁。另一个 V2 运行实例或维护进程正在使用该目录时，命令明确拒绝；不要通过删除锁文件绕过正在持有的 OS 锁。

## 升级、备份和恢复

`upgrade` 要求当前运行实例已停止。它通过数据目录锁执行 Alembic，并检查结果；应用正常打开不会隐式迁移未知旧库。

```bash
uv run --frozen cursor-core --data-dir ./v2-data --key-file ./v2-secrets/master.json upgrade
```

备份数据时使用 SQLite 一致性备份，并同时备份独立密钥文件。恢复到新目录后，使用匹配的数据库与密钥执行 `verify`。缺少密钥、密钥错误、密文被篡改或密钥版本缺失都会明确失败，不生成新密钥替代旧密钥，不降级保存明文。

`FileKeyProvider` 支持读取带多个历史 key ID 的文件，供未来轮换工具使用；P1 未提供生产密钥轮换命令，运维者不应手工删除仍被数据引用的旧 key ID。测试覆盖的是原密钥恢复和解密完整性。

回退旧版时停止 V2，恢复导入前的旧库副本并运行 `legacy` 对应程序。旧版不能读取 `core.db`，新版编辑不会自动写回旧库，已经发生的上游续期可能需要重新授权。

## 开发验证

```bash
uv run --frozen python -m unittest discover -s tests -p test_core.py -v
uv build --wheel --out-dir output/p1/wheels
python dev/verify-core-wheel.py output/p1/wheels/cursor_dashboard-1.4.0-py3-none-any.whl
```

测试和 wheel 验证创建自己的临时库与模拟凭证，不使用生产账号，也不发起 Cursor 授权/续期。wheel 验证会在临时环境安装构建产物，随后清理环境。
