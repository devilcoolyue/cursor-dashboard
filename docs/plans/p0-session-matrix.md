# P0：Cursor 会话能力与后续集成验证矩阵

日期：2026-09-09。证据范围：legacy `52d0a92` 源码、模拟响应和临时数据库测试。未读取真实账号库，未调用 Cursor 授权/续期，未修改本机 Cursor。

## 当前代码能够证明的行为

`client.py` 的网页回调 `/api/auth/loginDeepCallbackControl` 接收 flow UUID 和 PKCE challenge，`/auth/poll` 用 verifier 领取桌面会话；`/oauth/token` 执行 refresh_token grant。身份匹配依赖上游 `me`，本地 JWT 解码只检查声明与格式。

`sessions.ensure_account()` 使用 SQLite 租约串行化同库账号续期，保存时比较旧记录、授权代次、旧 RT 与 Cookie。`desktop.refreshed_session()` 在缺少独立 RT 时使用返回 AT 作为 RT，这只是现有适配规则，不是上游长期契约。

当前切换脚本把面板保存的 AT/RT 写入客户端。因此服务端和 Cursor 客户端可能持有同一份 RT，客户端不参与面板的数据库租约。

| 场景 | 当前证据/测试 | 结论 |
| --- | --- | --- |
| 12 个面板请求同时续期 | `test_concurrent_requests_refresh_only_once_and_persist_rotation` | 模拟中只调用一次刷新，落库版本一致 |
| AT 被拒绝后续期并重试 | `test_401_forces_one_refresh_then_retries_with_new_at` | 模拟中只强制续期并重试一次 |
| 续期与重新授权竞争 | `test_refresh_cannot_overwrite_concurrent_reauthorization`、`test_refresh_failure_cannot_revoke_concurrent_reauthorization` | 旧成功/失败均不覆盖新授权 |
| 续期与删除竞争 | `test_deleted_account_is_not_recreated_by_inflight_refresh` | 旧请求不重建已删除账号 |
| 临时限流 | `test_transient_refresh_error_preserves_credentials_and_releases_lease` | 保留凭证且释放租约 |
| 正常轮换与快照 | `test_renewal_keeps_snapshot_and_public_views_do_not_leak_tokens` | 快照保留，普通响应不含凭证 |
| 同一 RT 被两个独立设备刷新 | 本仓库没有实际设备实验结论 | 未证实旧 RT 的重放窗口、作废规则和冲突行为 |
| 两次 PKCE 授权是否产生独立会话 | flow 随机值不同不足以证明会话独立 | 待 P5 上游验证 |
| 网页退出、客户端退出或远程撤销的范围 | 有历史实验工具，但本阶段未运行且不推断历史结果 | 待 P5 上游验证 |

## P5 上游集成矩阵

使用专用测试账号和隔离的设备状态。记录平台/客户端版本、测试时间、请求结果分类、凭证是否变化、身份是否匹配、下一次续期是否成功；不在报告输出 AT/RT、Cookie 或完整脚本。

| ID | 实验 | 验收问题 |
| --- | --- | --- |
| S01 | 同账号分别完成 A/B 两次 PKCE 授权 | 刷新/撤销 A 后，B 能否继续查询与续期，能否证实独立会话 |
| S02 | 同一 RT：服务端先刷新，客户端随后刷新 | 旧 RT 是否立即失效，有无重放宽限，客户端能否恢复 |
| S03 | 同一 RT：客户端先刷新，服务端随后刷新 | 面板会否把合法账号误标失效，需要什么重新授权路径 |
| S04 | 同一 RT：两个独立进程并发刷新 | 返回凭证是否都有效，下一轮刷新是否稳定，有无级联撤销 |
| S05 | 两个设备实际使用并跨越续期窗口 | 支持的设备数、会话隔离与长期状态是否一致 |
| S06 | 分别执行网页退出、客户端退出、远程撤销 | 影响哪个会话，服务端和其他设备如何恢复 |
| S07 | use 撤销与签发/领取竞争 | 未领取票据不能交付；已交付凭证的撤销边界被正确告知 |
| S08 | 领取后断网、写入失败、客户端重启失败 | 单次票据不重用，本机备份完整，重新领取/恢复路径可用 |

优先为设备签发经验证独立的会话；若无法隔离，必须定义并验证明确的共享会话限制。仅增加服务端锁不能解决独立客户端的 RT 轮换。S01–S06 没有证据时，远程切换仍不能宣称稳定支持；不影响 P0 模拟验证和本地运行路线的结论。

旧 `dev/verify-session-lifecycle.py` 会读取账号库并创建真实会话，不能作为默认 P0 检查命令。后续实际实验需要明确指定专用测试凭证、状态目录与清理方式。
