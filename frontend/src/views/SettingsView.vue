<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { api, message, type Schema } from '../api'
import { clearIdentity, me } from '../state'
import { timeText } from '../format'
import { skin, theme } from '../theme'
const sessions = ref<Schema['SessionView'][]>([]), oldPassword = ref(''), newPassword = ref(''), error = ref(''), busy = ref(false)
const controller = new AbortController()
onBeforeUnmount(() => { controller.abort(); oldPassword.value = ''; newPassword.value = '' })
async function load() { sessions.value = await api.request<Schema['SessionView'][]>('/auth/sessions', 'GET', undefined, controller.signal) }
async function run(action: () => Promise<void>) { busy.value = true; error.value = ''; try { await action() } catch (reason) { error.value = message(reason) } finally { busy.value = false } }
onMounted(() => void run(load))
async function revoke(session: Schema['SessionView']) { await run(async () => { await api.request('/auth/sessions/' + session.id, 'DELETE', undefined, controller.signal); if (session.current) clearIdentity(); else await load() }) }
async function changePassword() { await run(async () => { try { await api.request('/auth/password', 'PUT', { current_password: oldPassword.value, new_password: newPassword.value }, controller.signal); clearIdentity() } finally { oldPassword.value = ''; newPassword.value = '' } }) }
</script>
<template><section class="workspace-page settings-page"><header class="page-heading"><div><p class="eyebrow">{{ me?.login }}</p><h1>个人设置</h1></div></header><p v-if="error" role="alert" class="error">{{ error }}</p>
  <section class="settings-section"><h2>显示偏好</h2><div class="inline-form"><label>皮肤<select v-model="skin"><option value="classic">经典</option><option value="glass">玻璃</option><option value="graphite">石墨</option><option value="verdant">青绿</option><option value="blueprint">蓝图</option><option value="cyberpunk">赛博</option></select></label><label>明暗<select v-model="theme"><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label></div></section>
  <section class="settings-section"><h2>更改密码</h2><form class="form-stack narrow" @submit.prevent="changePassword"><label>当前密码<input v-model="oldPassword" type="password" autocomplete="current-password" required maxlength="256" /></label><label>新密码<input v-model="newPassword" type="password" autocomplete="new-password" required minlength="12" maxlength="256" /></label><p class="muted">至少 12 个字符。修改后包括本次登录在内的全部会话将撤销。</p><button class="primary" :disabled="busy">更新密码并退出登录</button></form></section>
  <section class="settings-section"><div class="section-heading"><h2>登录会话</h2><button :disabled="busy" @click="run(load)">重载会话</button></div><div v-for="session in sessions" :key="session.id" class="setting-row"><div><strong>{{ session.current ? '当前会话' : '其他会话' }}</strong><small>登录 {{ timeText(session.created_at) }} · 到期 {{ timeText(session.expires_at) }}</small></div><button :disabled="busy" @click="revoke(session)">{{ session.current ? '退出本次登录' : '撤销会话' }}</button></div></section>
</section></template>
