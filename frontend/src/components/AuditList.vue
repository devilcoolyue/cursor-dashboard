<script setup lang="ts">
import type { Schema } from '../api'
import { timeText } from '../format'
defineProps<{ events: Schema['AuditView'][] }>()
</script>
<template><div class="audit-list"><p v-if="!events.length" class="muted">暂无操作记录。</p><div v-for="event in events" :key="event.id" class="audit-row"><div><strong>{{ event.action }}</strong><span>{{ event.result }}</span></div><small>{{ timeText(event.created_at) }} · 操作者 {{ event.actor_id || '系统' }}</small><details v-if="event.changes || event.resource_id"><summary>事件详情</summary><p>资源 {{ event.resource_id || '—' }}</p><pre v-if="event.changes">{{ JSON.stringify(event.changes, null, 2) }}</pre><small>请求 {{ event.request_id || '—' }}</small></details></div></div></template>
