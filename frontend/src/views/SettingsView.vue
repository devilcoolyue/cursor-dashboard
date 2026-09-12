<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { api, message, type Schema } from '../api'
import { clearIdentity, me } from '../state'
import { timeText } from '../format'
import { isDesktop, connectionId } from '../platform'
import DesktopSettings from '../components/DesktopSettings.vue'
import SettingsLayout from '../components/SettingsLayout.vue'
import SettingsSection from '../components/SettingsSection.vue'
import UiIcon from '../components/UiIcon.vue'
const sessions = ref<Schema['SessionView'][]>([]), oldPassword = ref(''), newPassword = ref(''), error = ref(''), busy = ref(false), loaded = ref(false)
const controller = new AbortController()
onBeforeUnmount(() => { controller.abort(); oldPassword.value = ''; newPassword.value = '' })
async function load() { sessions.value = await api.request<Schema['SessionView'][]>('/auth/sessions', 'GET', undefined, controller.signal); loaded.value = true }
async function run(action: () => Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; try { await action() } catch (reason) { error.value = message(reason) } finally { busy.value = false } }
onMounted(() => { if (!isDesktop || connectionId.value) void run(load) })
async function revoke(session: Schema['SessionView']) { await run(async () => { await api.request('/auth/sessions/' + session.id, 'DELETE', undefined, controller.signal); if (session.current) clearIdentity(); else await load() }) }
async function changePassword() { await run(async () => { try { await api.request('/auth/password', 'PUT', { current_password: oldPassword.value, new_password: newPassword.value }, controller.signal); clearIdentity() } finally { oldPassword.value = ''; newPassword.value = '' } }) }
</script>
<template>
  <SettingsLayout title="个人设置" :context="isDesktop && !connectionId ? '本地用户' : me?.login" :error="error">
    <template #actions><RouterLink to="/about" class="about-release-link"><UiIcon name="info" :size="14" />关于与更新</RouterLink></template>
    <SettingsSection v-if="!isDesktop || connectionId" title="更改密码" description="定期更新密码，保护你的账号。">
      <form class="form-stack settings-form password-form" @submit.prevent="changePassword">
        <div class="settings-form-grid"><label>当前密码<input v-model="oldPassword" :disabled="busy" type="password" autocomplete="current-password" required maxlength="256" placeholder="输入当前密码" /></label><label>新密码<input v-model="newPassword" :disabled="busy" type="password" autocomplete="new-password" required minlength="12" maxlength="256" placeholder="至少 12 个字符" aria-describedby="password-hint" /></label></div>
        <p id="password-hint" class="field-hint">修改后所有设备都会退出登录，请使用新密码重新登录。</p>
        <div class="settings-form-actions"><button class="primary" :disabled="busy"><UiIcon name="key" :size="14" />更新密码并退出登录</button></div>
      </form>
    </SettingsSection>
    <SettingsSection v-if="!isDesktop || connectionId" title="登录会话" description="查看已登录的设备，撤销不再使用的会话。" :count="loaded ? sessions.length : undefined">
      <div class="settings-list-toolbar"><span class="field-hint">{{ loaded ? '当前账号的所有登录设备' : busy ? '正在载入会话…' : '暂未载入会话' }}</span><button class="subtle-button" :disabled="busy" @click="run(load)"><UiIcon name="refresh" :class="{ spinning: busy }" :size="14" />重载会话</button></div>
      <div :aria-busy="busy" class="settings-list">
        <div v-for="session in sessions" :key="session.id" class="setting-row session-row">
          <div class="setting-identity"><span class="settings-avatar neutral"><UiIcon :name="session.kind === 'device' ? 'monitor' : 'panels'" :size="18" /></span><div><strong>{{ session.kind === 'device' ? (session.device_name || '桌面设备') : session.current ? '当前网页会话' : '其他网页会话' }}<span v-if="session.current" class="settings-badge accent">当前{{ session.kind === 'device' ? '设备' : '会话' }}</span></strong><small>登录 {{ timeText(session.created_at) }}<span class="session-expiry">到期 {{ timeText(session.expires_at) }}</span></small></div></div>
          <button class="subtle-button" :disabled="busy" @click="revoke(session)">{{ session.current ? '退出本次登录' : '撤销会话' }}</button>
        </div>
        <p v-if="loaded && !sessions.length" class="settings-empty">暂无登录会话。</p>
      </div>
    </SettingsSection>
    <DesktopSettings v-if="isDesktop" />
  </SettingsLayout>
</template>
