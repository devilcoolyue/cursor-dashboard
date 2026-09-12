<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { connectionAction, connectionId, type InstanceConnection } from '../platform'
import { connections, selectConnection, me, activeConnection, startupError, connectionNotice as notice } from '../state'
import { message } from '../api'
import SettingsLayout from '../components/SettingsLayout.vue'
import SettingsSection from '../components/SettingsSection.vue'
import UiDialog from '../components/UiDialog.vue'
import UiIcon from '../components/UiIcon.vue'
import UiPopover from '../components/UiPopover.vue'
const router = useRouter(), name = ref(''), origin = ref(''), busy = ref(false), error = ref('')
const adding = ref(false)
const phases: Record<string, string> = { saved: '尚未连接', connected: '已登录', awaiting_browser: '等待浏览器授权',
  login_required: '需要重新登录', login_failed: '登录失败，请重试', login_expired: '登录已超时，请重试', offline: '实例暂时离线', incompatible: '协议不兼容' }
let timer: ReturnType<typeof setTimeout> | undefined, alive = true, waiting: string | undefined
onBeforeUnmount(() => { alive = false; clearTimeout(timer) })
async function run(action: () => Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; notice.value = ''; try { await action() } catch (reason) { if (alive) error.value = message(reason) } finally { busy.value = false } }
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
    if (!busy.value && waiting && result.items.find(c => c.id === waiting)?.phase === 'connected') {
      const id = waiting; waiting = undefined; await run(() => open(id))
    }
  } catch (reason) { if (alive) error.value = message(reason) }
  finally { if (alive) timer = setTimeout(poll, 1500) }
}
onMounted(() => void poll())
function showAdd() { name.value = ''; origin.value = ''; error.value = ''; adding.value = true }
function closeAdd() { adding.value = false; error.value = '' }
const canOpen = (row: InstanceConnection) => ['connected', 'offline', 'incompatible'].includes(row.phase)
async function add() { await run(async () => { connections.value = await connectionAction('add', undefined, { name: name.value.trim(), origin: origin.value.trim() }); name.value = ''; origin.value = ''; adding.value = false }) }
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
<template>
  <SettingsLayout title="实例连接" :context="activeConnection?.name || '本地'" :error="adding ? '' : error || startupError" class="connections-page" :class="{ 'connections-standalone': !me }">
    <template #actions><button type="button" class="primary" :disabled="busy" @click="showAdd"><UiIcon name="plus" :size="14" />添加实例</button></template>
    <SettingsSection title="本地账号" description="保存在这台设备上的个人空间。">
      <div class="settings-list connection-list">
        <div class="setting-row connection-entry">
          <div class="setting-identity"><span class="settings-avatar"><UiIcon name="monitor" :size="18" /></span><div><strong>本地<span v-if="!connectionId && me" class="settings-badge accent">当前连接</span></strong><small>这台设备上的个人账号</small></div></div>
          <button type="button" :disabled="busy" @click="run(() => open(null))">{{ connectionId ? '返回本地账号' : '打开本地账号' }}<UiIcon name="chevron" :size="13" /></button>
        </div>
      </div>
    </SettingsSection>
    <SettingsSection title="远程实例" description="连接团队部署的服务，登录后查看获授权的空间与账号。" :count="connections.items.length">
      <div v-if="!connections.items.length" class="connections-empty">
        <span class="settings-avatar neutral"><UiIcon name="server" :size="20" /></span>
        <h3>尚未添加远程实例</h3><p>点击右上角「添加实例」，填写名称和服务地址。</p>
      </div>
      <div v-else class="settings-list connection-list">
        <div v-for="row in connections.items" :key="row.id" class="setting-row connection-entry">
          <div class="setting-identity"><span class="settings-avatar neutral"><UiIcon name="server" :size="18" /></span><div><strong><span>{{ row.name }}</span><span v-if="connectionId === row.id" class="settings-badge accent">当前连接</span></strong><small class="connection-origin">{{ row.origin }}</small><p role="status" class="connection-phase" :class="row.phase"><i />{{ phases[row.phase] || '尚未连接' }}</p></div></div>
          <div class="actions setting-row-actions connection-actions">
            <button v-if="canOpen(row)" type="button" :disabled="busy" @click="run(() => open(row.id))">打开实例<UiIcon name="chevron" :size="13" /></button>
            <button v-else type="button" :disabled="busy || row.phase === 'awaiting_browser'" @click="login(row)"><UiIcon :name="row.phase === 'awaiting_browser' ? 'refresh' : 'external'" :class="{ spinning: row.phase === 'awaiting_browser' }" :size="13" />{{ row.phase === 'awaiting_browser' ? '等待授权…' : '浏览器登录' }}</button>
            <UiPopover label="实例操作" align="end" :width="190">
              <template #trigger="{ id, open: menuOpen, toggle }"><button type="button" class="subtle-button connection-more" aria-label="更多实例操作" :aria-expanded="menuOpen" :aria-controls="id" :disabled="busy" @click="toggle"><UiIcon name="more" :size="16" /></button></template>
              <template #default="{ close }"><div class="sidebar-menu connection-menu">
                <button v-if="canOpen(row)" type="button" :disabled="busy" @click="close(); login(row)"><UiIcon name="external" :size="14" />浏览器登录</button>
                <button type="button" :disabled="busy" @click="close(); disconnect(row)"><UiIcon name="logout" :size="14" />断开登录</button>
                <button type="button" class="danger-text" :disabled="busy" @click="close(); disconnect(row, true)"><UiIcon name="trash" :size="14" />移除连接</button>
              </div></template>
            </UiPopover>
          </div>
        </div>
      </div>
    </SettingsSection>
    <template #after><p v-if="notice" role="status" class="notice connection-notice">{{ notice }}</p><p class="connections-hint"><UiIcon name="switch" :size="13" />本地与远程实例分别管理，切换后只显示当前连接的空间。</p></template>
    <template #dialogs>
      <UiDialog v-if="adding" title="添加实例" class="connection-add-dialog" :dismiss-backdrop="!busy" @close="closeAdd">
        <template #subtitle>连接已部署的 Cursor Panel 服务。</template>
        <form id="connection-add-form" class="form-stack settings-form" @submit.prevent="add">
          <label>实例名称<input v-model="name" :disabled="busy" required maxlength="128" placeholder="例如：研发团队" autofocus /></label>
          <label>实例地址<input v-model="origin" :disabled="busy" type="url" required maxlength="2048" placeholder="https://panel.example.com" autocomplete="url" spellcheck="false" aria-describedby="connection-origin-hint" /></label>
          <p id="connection-origin-hint" class="field-hint">填写 HTTPS 根地址；本机开发可使用 HTTP 回环地址。</p>
          <div class="form-note"><UiIcon name="external" :size="16" /><p>添加后通过系统浏览器登录，授权此客户端访问实例。</p></div>
          <p v-if="error" role="alert" class="error">{{ error }}</p>
        </form>
        <template #footer><button type="button" @click="closeAdd">取消</button><button type="submit" form="connection-add-form" class="primary" :disabled="busy"><UiIcon v-if="busy" name="refresh" :size="14" class="spinning" />{{ busy ? '正在添加…' : '添加实例' }}</button></template>
      </UiDialog>
    </template>
  </SettingsLayout>
</template>

<style>
.connections-page .settings-content { max-width: 1200px; }
.connections-standalone { max-width: 1200px; margin: 0 auto; }
.connection-entry .setting-identity { align-items: flex-start; }
.connection-entry .settings-avatar { margin-top: 2px; }
.connection-entry .connection-origin { overflow-wrap: anywhere; }
.connection-phase { display: flex; align-items: center; gap: 6px; color: var(--dimmer); font-size: 11px; margin: 6px 0 0; }
.connection-phase > i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex: none; }
.connection-phase.connected { color: var(--ok); }
.connection-phase.awaiting_browser { color: var(--accent); }
.connection-phase.login_required, .connection-phase.offline { color: var(--warn); }
.connection-phase.login_failed, .connection-phase.login_expired, .connection-phase.incompatible { color: var(--bad); }
.settings-list .connection-entry { padding-block: 20px; }
.connection-actions { gap: 7px; }
.connection-more { width: 30px; height: 30px; padding: 0; }
.connection-menu { padding: 5px; }
.connection-menu button { width: 100%; font-size: 12px; }
.connections-empty { padding: 25px 16px; text-align: center; border: 1px dashed var(--line); border-radius: var(--radius-ctl); }
.connections-empty > .settings-avatar { margin-bottom: 12px; width: 40px; height: 40px; }
.connections-empty h3 { margin-bottom: 5px; font-size: 12px; font-weight: 500; }
.connections-empty p { color: var(--dim); font-size: 11.5px; margin: 0; }
.connections-hint { display: flex; align-items: flex-start; gap: 8px; margin: 16px 2px 0; color: var(--dim); font-size: 11px; }
.connections-hint > svg { margin-top: 2px; }
.connection-notice { margin: 16px 0 0; }
.connection-add-dialog .error { margin: 0; }
@media (max-width: 760px) { .connections-page .settings-context { display: none; } }
</style>
