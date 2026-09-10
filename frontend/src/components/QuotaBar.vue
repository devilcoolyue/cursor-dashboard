<script setup lang="ts">
import type { Schema } from '../api'
defineProps<{ name: string; slot?: Schema['QuotaSlot'] | null }>()
const pct = (value: number) => `${Number(value.toFixed(1))}%`
</script>
<template>
  <div class="quota-cell">
    <div class="quota-label"><span>{{ name }}</span><strong>{{ slot?.remaining_pct == null ? '—' : pct(slot.remaining_pct) }}</strong></div>
    <progress v-if="slot?.remaining_pct != null" :value="Math.max(0, Math.min(100, slot.remaining_pct))" max="100" :aria-label="`${name}剩余`" :class="{ low: slot.remaining_pct < 15 }" />
    <span v-else class="empty-track" />
    <small v-if="slot?.limit_usd != null">{{ slot.limit_inferred ? '估算上限' : '推算上限' }} ${{ slot.limit_usd.toFixed(2) }}</small>
  </div>
</template>
