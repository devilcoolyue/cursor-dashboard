<script setup lang="ts">
import type { Schema } from '../api'
import { percent, quotaTone } from '../quota'
import UiIcon from './UiIcon.vue'
import { computed } from 'vue'
const props = defineProps<{ name: string; slot?: Schema['QuotaSlot'] | null; interactive?: boolean }>()
const emit = defineEmits<{ detail: [] }>()
const used = computed(() => props.slot?.used_pct ?? (props.slot?.remaining_pct == null ? null : 100 - props.slot.remaining_pct))
</script>
<template>
  <component :is="interactive ? 'button' : 'div'" class="quota-cell" :class="quotaTone(slot?.remaining_pct)" :type="interactive ? 'button' : undefined" :aria-label="interactive ? `查看${name}明细` : undefined" @click="interactive && emit('detail')">
    <span class="quota-label"><span>{{ name }} <small v-if="slot?.limit_usd != null" class="quota-limit" :title="`${slot.limit_inferred ? '估算' : '推算'}上限，来自 Cursor 额度百分比`">${{ Number(slot.limit_usd.toFixed(2)) }}<span class="sr-only">（{{ slot.limit_inferred ? '估算' : '推算' }}上限）</span></small></span><strong>{{ slot?.remaining_pct == null ? '暂无数据' : `剩 ${percent(slot.remaining_pct)}` }}</strong><UiIcon v-if="interactive" name="chevron" :size="13" /></span>
    <span class="quota-track" role="progressbar" :aria-label="`${name}已用额度`" :aria-valuenow="used == null ? undefined : Math.max(0, Math.min(100, used))" :aria-valuetext="used == null ? '暂无数据' : `已用 ${percent(used)}`" :aria-valuemin="0" :aria-valuemax="100"><span v-if="used != null" class="quota-fill" :style="{ width: `${Math.max(0, Math.min(100, used))}%` }" /></span>
  </component>
</template>
