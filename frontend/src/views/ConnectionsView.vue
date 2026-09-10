<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { connectionAction, connectionId, type InstanceConnection } from '../platform'
import { connections, selectConnection, me, startupError, connectionNotice as notice } from '../state'
import { message } from '../api'
const router = useRouter(), name = ref(''), origin = ref(''), busy = ref(false), error = ref('')
const phases: Record<string, string> = { saved: '尚未连接', connected: '已登录', awaiting_browser: '等待浏览器授权',
  login_required: '需要重新登录', login_failed: '登录失败，请重试', login_expired: '登录已超时，请重试', offline: '实例暂时离线', incompatible: '协议不兼容' }
let timer: ReturnType<typeof setTimeout> | undefined, alive = true, waiting: string | undefined
onBeforeUnmount(() => { alive = false; clearTimeout(timer) })
async function run(action: () => Promise<void>) { busy.value = true; error.value = ''; notice.value = ''; try { await action() } catch (reason) { if (alive) error.value = message(reason) } finally { busy.value = false } }
async function open(id: string | null) {
  await selectConnection(id)
  if (me.value) await router.replace('/accounts')
  else error.value = startupError.value || '请使用系统浏览器登录此实例。'
}
async function poll() {
  try {
    const result = await connectionAction('list')
    if (!alive) return
    connections.value = result
    if (waiting && result.items.find(c => c.id === waiting)?.phase === 'connected') {
      const id = waiting; waiting = undefined; await run(() => open(id))
    }
  } catch (reason) { if (alive) error.value = message(reason) }
  finally { if (alive) timer = setTimeout(poll, 1500) }
}
onMounted(() => void poll())
async function add() { await run(async () => { connections.value = await connectionAction('add', undefined, { name: name.value, origin: origin.value }); name.value = ''; origin.value = '' }) }
async function login(row: InstanceConnection) { await run(async () => { connections.value = await connectionAction('login', row.id); waiting = row.id }) }
async function disconnect(row: InstanceConnection, remove = false) { await run(async () => {
  waiting = undefined
  const wasActive = connectionId.value === row.id
  connections.value = await connectionAction(remove ? 'remove' : 'disconnect', row.id)
  const revoked = connections.value.revoked
  if (wasActive) await selectConnection(null)
  if (revoked === false) notice.value = '本机设备登录已清除。远端暂时无法确认撤销，请登录实例网页，在个人设置中撤销这台设备。'
}) }
</script>
<template><section class="workspace-page connections-page" :class="{ 'standalone-connections': !me }"><header class="page-heading"><div><p class="eyebrow">CURSOR PANEL · DESKTOP</p><h1>实例连接</h1><p class="muted">本地账号与各远程实例分别管理，切换连接后只显示当前实例的空间。</p></div></header>
  <section class="settings-section"><div class="setting-row"><div><strong>本地</strong><small>这台设备上的个人账号</small></div><button :disabled="busy" @click="run(() => open(null))">{{ connectionId ? '返回本地账号' : '打开本地账号' }}</button></div></section>
  <section class="settings-section"><h2>远程实例</h2><p v-if="!connections.items.length" class="muted">添加实例后，通过系统浏览器登录。</p><div v-for="row in connections.items" :key="row.id" class="connection-row"><div><strong>{{ row.name }}{{ connectionId === row.id ? ' · 当前实例' : '' }}</strong><small>{{ row.origin }}</small><p role="status" class="muted">{{ phases[row.phase] || '尚未连接' }}</p></div><div class="actions"><button :disabled="busy || row.phase === 'awaiting_browser'" @click="login(row)">浏览器登录</button><button :disabled="busy" @click="run(() => open(row.id))">打开实例</button><button :disabled="busy" @click="disconnect(row)">断开登录</button><button class="danger-text" :disabled="busy" @click="disconnect(row, true)">移除连接</button></div></div></section>
  <section class="settings-section"><h2>添加实例</h2><form class="form-stack narrow" @submit.prevent="add"><label>实例名称<input v-model="name" required maxlength="128" placeholder="例如：研发团队" /></label><label>实例地址<input v-model="origin" type="url" required maxlength="2048" placeholder="https://panel.example.com" /></label><p class="muted">填写 HTTPS 根地址；本机开发实例可使用 HTTP 回环地址。</p><button class="primary" :disabled="busy">添加实例</button></form></section>
  <p v-if="notice" role="status" class="notice">{{ notice }}</p><p v-if="error || startupError" role="alert" class="error">{{ error || startupError }}</p>
</section></template>
