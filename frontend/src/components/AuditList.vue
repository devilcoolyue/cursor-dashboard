<script setup lang="ts">
import type { Schema } from '../api'
import { timeText } from '../format'
import UiIcon from './UiIcon.vue'
defineProps<{ events: Schema['AuditView'][]; loading?: boolean; actors?: Record<string, string> }>()
const actions: Record<string, string> = {
  'instance.initialize': '初始化实例', 'session.login': '登录账号', 'session.revoke': '撤销登录会话', 'session.login_failed': '登录失败',
  'user.password': '更新密码', 'user.enable': '启用用户', 'user.disable': '停用用户', 'operator.password_recovery': '恢复账号密码',
  'workspace.create': '创建空间', 'workspace.transfer': '转移空间所有权', 'workspace.delete': '删除空间',
  'invitation.create': '创建成员邀请', 'invitation.revoke': '撤销成员邀请', 'invitation.accept': '接受成员邀请',
  'membership.role': '调整成员角色', 'membership.remove': '移除空间成员', 'grant.set': '设置账号授权', 'grant.revoke': '撤销账号授权',
  'account.create': '添加账号', 'account.reauthorize': '重新授权账号', 'account.edit': '编辑账号资料', 'account.delete': '删除账号', 'account.refresh': '刷新账号额度',
  'switch.issue': '创建切换票据', 'switch.consume': '领取切换票据', 'switch.result': '账号切换结果',
  'device.approve': '授权桌面设备', 'device.login': '桌面设备登录', 'device.revoke': '撤销设备授权',
}
const results: Record<string, string> = { success: '成功', failure: '失败', failed: '失败', denied: '已拒绝', error: '失败' }
</script>
<template>
  <div class="audit-list" :aria-busy="loading">
    <p v-if="loading" class="settings-empty" role="status">正在载入操作记录…</p>
    <p v-else-if="!events.length" class="settings-empty"><UiIcon name="clock" :size="18" />暂无操作记录</p>
    <div v-for="event in events" :key="event.id" class="audit-row">
      <div class="audit-event-heading"><strong>{{ actions[event.action] || event.action }}</strong><span class="audit-result" :class="{ 'is-success': event.result === 'success' }">{{ results[event.result] || event.result }}</span><time>{{ timeText(event.created_at) }}</time></div>
      <details><summary><UiIcon name="chevron" :size="12" /><span>{{ event.actor_id ? actors?.[event.actor_id] || '用户操作' : '系统' }}</span><span class="audit-detail-label">查看详情</span></summary><dl class="audit-details"><div><dt>事件</dt><dd>{{ event.action }}</dd></div><div><dt>操作者</dt><dd>{{ event.actor_id || '系统' }}</dd></div><div v-if="event.resource_id"><dt>资源</dt><dd>{{ event.resource_id }}</dd></div><div v-if="event.request_id"><dt>请求</dt><dd>{{ event.request_id }}</dd></div></dl><pre v-if="event.changes">{{ JSON.stringify(event.changes, null, 2) }}</pre></details>
    </div>
  </div>
</template>
