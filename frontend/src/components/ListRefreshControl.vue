<script setup lang="ts">
import { listRefreshSeconds, refreshIntervals } from '../list-refresh'
import UiIcon from './UiIcon.vue'
import UiPopover from './UiPopover.vue'
defineProps<{ busy: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const intervalLabel = (seconds: number) => seconds === 60 ? '1 分钟' : `${seconds} 秒`
</script>
<template>
  <UiPopover class="list-refresh-control" label="列表刷新" align="end" :width="260" focus-on-open>
    <template #trigger="{ toggle, open, id }">
      <button type="button" class="toolbar-reload" aria-label="重载列表" :title="`列表每 ${intervalLabel(listRefreshSeconds)} 自动更新 · 点击设置`" :aria-expanded="open" :aria-controls="id" aria-haspopup="dialog" @click="toggle"><UiIcon name="refresh" :class="{ spinning: busy }" :size="15" /></button>
    </template>
    <template #default="{ close }">
      <div class="list-refresh-menu">
        <button type="button" class="refresh-now" :disabled="busy" @click="close(); emit('refresh')"><UiIcon name="refresh" :size="15" />{{ busy ? '正在刷新…' : '立即刷新列表' }}</button>
        <fieldset><legend>自动更新间隔</legend><div class="refresh-intervals">
          <label v-for="seconds in refreshIntervals" :key="seconds" :class="{ selected: listRefreshSeconds === seconds }"><input v-model="listRefreshSeconds" type="radio" name="list-refresh-interval" :value="seconds" />{{ intervalLabel(seconds) }}</label>
        </div></fieldset>
        <p>自动显示最新统计结果。需要立即查询额度时，点击账号卡片上的刷新。</p>
      </div>
    </template>
  </UiPopover>
</template>
<style scoped>
.list-refresh-control { flex: none; }
.list-refresh-menu { padding: 12px; }
.list-refresh-menu .refresh-now { width: 100%; justify-content: flex-start; border: 0; background: var(--accent-bg); color: var(--accent); padding: 8px 10px; }
fieldset { padding: 0; margin: 16px 0 0; border: 0; min-width: 0; }
legend { padding: 0; margin-bottom: 8px; font-size: 12px; color: var(--dim); }
.refresh-intervals { display: flex; gap: 6px; }
.refresh-intervals label { position: relative; display: flex; flex: 1; justify-content: center; padding: 6px 0; font-size: 12px; border: 1px solid var(--line); border-radius: var(--radius-ctl); cursor: pointer; }
.refresh-intervals label.selected { color: var(--accent); background: var(--accent-bg); border-color: var(--accent); }
.refresh-intervals label:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.refresh-intervals input { position: absolute; opacity: 0; width: 1px; height: 1px; }
.list-refresh-menu p { margin: 12px 0 0; color: var(--dim); font-size: 11px; line-height: 1.7; }
</style>
