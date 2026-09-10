<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { api, message, type Schema } from '../api'
import { me } from '../state'
import AuditList from '../components/AuditList.vue'
import UiDialog from '../components/UiDialog.vue'
const users = ref<Schema['UserView'][]>([]), events = ref<Schema['AuditView'][]>([]), error = ref(''), busy = ref(false), target = ref<Schema['UserView']>(), offset = ref(0)
const controller = new AbortController()
onBeforeUnmount(() => controller.abort())
async function load() { const result = await Promise.all([api.request<Schema['UserView'][]>('/instance/users', 'GET', undefined, controller.signal), api.request<Schema['AuditView'][]>(`/instance/audit?limit=25&offset=${offset.value}`, 'GET', undefined, controller.signal)]); [users.value, events.value] = result }
async function run(action: () => Promise<void>) { busy.value = true; error.value = ''; try { await action() } catch (reason) { error.value = message(reason) } finally { busy.value = false } }
onMounted(() => void run(load))
async function setActive() { if (!target.value) return; const user = target.value; await run(async () => { await api.request('/instance/users/' + user.id, 'PUT', { active: !user.active }, controller.signal); target.value = undefined; await load() }) }
async function turnPage(delta: number) { offset.value = Math.max(0, offset.value + delta); await run(load) }
</script>
<template><section class="workspace-page settings-page"><header class="page-heading"><div><p class="eyebrow">实例管理</p><h1>实例设置</h1><p class="muted">管理用户启停。账号内容仍按空间成员关系与授权访问。</p></div><button :disabled="busy" @click="run(load)">重载设置</button></header><p v-if="error" class="error" role="alert">{{ error }}</p>
  <section class="settings-section"><h2>用户</h2><div v-for="user in users" :key="user.id" class="setting-row"><div><strong>{{ user.login }}</strong><small>{{ user.active ? '启用' : '停用' }}{{ user.instance_admin ? ' · 实例管理员' : '' }}</small></div><button v-if="user.id !== me?.id" :disabled="busy" @click="target = user">{{ user.active ? '停用用户' : '启用用户' }}</button></div></section>
  <section class="settings-section"><div class="section-heading"><h2>实例操作记录</h2><div class="actions"><button :disabled="busy || !offset" @click="turnPage(-25)">上一页</button><button :disabled="busy || events.length < 25" @click="turnPage(25)">下一页</button></div></div><AuditList :events="events" /></section>
  <UiDialog v-if="target" :title="target.active ? '停用用户' : '启用用户'" @close="target = undefined"><p>{{ target.login }}：{{ target.active ? '停用后立即撤销其会话和未领取票据。' : '启用后需重新登录，旧会话不会恢复。' }}</p><p v-if="error" class="error" role="alert">{{ error }}</p><div class="actions"><button @click="target = undefined">取消</button><button class="primary" :disabled="busy" @click="setActive">确认</button></div></UiDialog>
</section></template>
