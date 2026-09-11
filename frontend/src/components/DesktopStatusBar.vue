<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute } from 'vue-router'
import { native, connectionAction, type DesktopStatus } from '../platform'
import { desktopStatus, activeConnection, connections } from '../state'
import UiIcon from './UiIcon.vue'
import UiPopover from './UiPopover.vue'
defineProps<{ updated: string }>()
const route = useRoute()
const failed = ref(false)
const status = computed(() => {
  if (failed.value) return { label: '后台连接中断', tone: 'invalid', detail: '当前显示已载入的快照，请重新打开面板。' }
  if (desktopStatus.value?.switch?.busy) return { label: '正在切换 Cursor', tone: 'stale', detail: '切换正在进行，可查看进度与恢复选项。' }
  if (activeConnection.value) return activeConnection.value.phase === 'offline'
    ? { label: '远程实例离线', tone: 'invalid', detail: `${activeConnection.value.name} 暂时无法连接，当前显示已载入的快照。` }
    : { label: '远程已连接', tone: '', detail: `${activeConnection.value.name} · 额度由服务端查询。` }
  return { label: '本地就绪', tone: '', detail: '本地账号可用，最新额度需要连接 Cursor。' }
})
let timer: ReturnType<typeof setTimeout> | undefined, alive = true
async function poll() {
  try {
    const state = await native<DesktopStatus>('status')
    const instances = await connectionAction('list')
    if (alive) { desktopStatus.value = state; connections.value = instances; failed.value = false }
  }
  catch { if (alive) failed.value = true }
  finally { if (alive) timer = setTimeout(poll, 5000) }
}
onMounted(() => void poll())
onBeforeUnmount(() => { alive = false; clearTimeout(timer) })
</script>
<template>
  <div class="desktop-status">
    <UiPopover :key="route.path" label="桌面运行状态" placement="top" :width="280">
      <template #trigger="{ toggle, open, id }">
        <button type="button" class="desktop-status-trigger" :class="status.tone" :title="`${status.label} · 查看运行状态`" :aria-label="`${status.label} · 查看运行状态`" :aria-expanded="open" :aria-controls="id" aria-haspopup="dialog" @click="toggle">
          <span class="sidebar-icon-slot"><span :class="['status-dot', status.tone]" /></span>
          <span class="desktop-status-label" role="status">{{ status.label }}</span>
          <span class="desktop-status-updated">{{ updated }}</span>
        </button>
      </template>
      <template #default="{ close }">
        <div class="desktop-status-details">
          <header class="sidebar-popover-header"><strong>{{ status.label }}</strong><button type="button" class="popover-close" aria-label="关闭运行状态" @click="close"><UiIcon name="close" :size="15" /></button></header>
          <p>{{ status.detail }}</p>
          <p>{{ desktopStatus?.background ? '已开启托盘驻留与定期刷新。' : '关闭窗口即退出。' }}</p>
          <p class="desktop-status-freshness">账号列表 · {{ updated }}</p>
          <RouterLink to="/settings" @click="close">{{ desktopStatus?.switch?.busy ? '查看进度与恢复' : '桌面运行设置' }}<UiIcon name="chevron" :size="13" /></RouterLink>
        </div>
      </template>
    </UiPopover>
  </div>
</template>
