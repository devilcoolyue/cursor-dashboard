<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { native, connectionAction, type DesktopStatus } from '../platform'
import { desktopStatus, activeConnection, connections } from '../state'
const failed = ref(false)
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
<template><div class="desktop-status" role="status"><span :class="['status-dot', { invalid: failed }]" />
  <span v-if="failed">本地后台连接中断 · 当前显示已载入的快照，请重新打开面板。</span>
  <span v-else-if="desktopStatus?.switch?.busy">正在切换 Cursor · <RouterLink to="/settings">查看进度与恢复</RouterLink></span>
  <span v-else-if="activeConnection">远程实例 · {{ activeConnection.name }} · {{ activeConnection.phase === 'offline' ? '实例离线，当前显示已载入快照' : '额度由服务端查询' }}</span>
  <span v-else>本地账号 · {{ desktopStatus?.background ? '已开启托盘驻留与定期刷新' : '关闭窗口即退出' }} · 最新额度需要连接 Cursor</span>
</div></template>
