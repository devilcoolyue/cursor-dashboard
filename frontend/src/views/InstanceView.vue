<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { api, message, type Schema } from '../api'
import { me } from '../state'
import AuditList from '../components/AuditList.vue'
import UiDialog from '../components/UiDialog.vue'
import UiIcon from '../components/UiIcon.vue'
import SettingsLayout from '../components/SettingsLayout.vue'
import SettingsSection from '../components/SettingsSection.vue'
const users = ref<Schema['UserView'][]>([]), events = ref<Schema['AuditView'][]>([]), error = ref(''), busy = ref(false), target = ref<Schema['UserView']>(), offset = ref(0), loaded = ref(false)
const controller = new AbortController()
onBeforeUnmount(() => controller.abort())
async function load() { const result = await Promise.all([api.request<Schema['UserView'][]>('/instance/users', 'GET', undefined, controller.signal), api.request<Schema['AuditView'][]>(`/instance/audit?limit=25&offset=${offset.value}`, 'GET', undefined, controller.signal)]); [users.value, events.value] = result; loaded.value = true }
async function run(action: () => Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; try { await action() } catch (reason) { error.value = message(reason) } finally { busy.value = false } }
onMounted(() => void run(load))
async function setActive() { if (!target.value) return; const user = target.value; await run(async () => { await api.request('/instance/users/' + user.id, 'PUT', { active: !user.active }, controller.signal); target.value = undefined; await load() }) }
async function turnPage(delta: number) { offset.value = Math.max(0, offset.value + delta); await run(load) }
</script>
<template>
  <SettingsLayout title="实例设置" :error="!target ? error : ''">
    <template #actions><button class="subtle-button" :disabled="busy" @click="run(load)"><UiIcon name="refresh" :class="{ spinning: busy }" :size="14" />重载设置</button></template>
    <SettingsSection title="用户" description="管理登录权限。账号内容仍按空间成员关系与授权访问。" :count="loaded ? users.length : undefined">
      <p v-if="!loaded && busy" class="settings-empty" role="status">正在载入用户…</p>
      <div v-else-if="loaded" class="settings-list" :aria-busy="busy">
        <div class="settings-list-head instance-user-row" aria-hidden="true"><span>登录邮箱</span><span>实例角色</span><span>状态</span><span>操作</span></div>
        <div v-for="user in users" :key="user.id" class="setting-row instance-user-row">
          <div class="setting-identity"><span class="settings-avatar">{{ user.login.charAt(0).toUpperCase() }}</span><div><strong>{{ user.login }}<span v-if="user.id === me?.id" class="settings-badge">你</span></strong></div></div>
          <span class="settings-role">{{ user.instance_admin ? '实例管理员' : '普通用户' }}</span>
          <span class="settings-status" :class="{ inactive: !user.active }"><i />{{ user.active ? '已启用' : '已停用' }}</span>
          <div class="settings-user-action"><button v-if="user.id !== me?.id" class="subtle-button" :class="{ 'danger-text': user.active }" :disabled="busy" @click="error = ''; target = user">{{ user.active ? '停用用户' : '启用用户' }}</button><span v-else class="field-hint">当前账号</span></div>
        </div>
        <p v-if="!users.length" class="settings-empty">暂无用户。</p>
      </div>
    </SettingsSection>
    <SettingsSection title="实例操作记录" description="查看用户登录、设备授权和实例管理记录。">
      <AuditList :events="events" :loading="!loaded && busy" :actors="Object.fromEntries(users.map(user => [user.id, user.login]))" />
      <div class="settings-pagination"><span>第 {{ Math.floor(offset / 25) + 1 }} 页</span><div class="actions"><button :disabled="busy || !offset" @click="turnPage(-25)">上一页</button><button :disabled="busy || events.length < 25" @click="turnPage(25)">下一页</button></div></div>
    </SettingsSection>
    <template #dialogs>
      <UiDialog v-if="target" :title="target.active ? '停用用户' : '启用用户'" @close="target = undefined">
        <template #subtitle>{{ target.login }}</template>
        <p class="confirmation-copy">{{ target.active ? '停用后将立即撤销该用户的全部登录会话和未领取票据。' : '启用后该用户可以重新登录，旧会话不会恢复。' }}</p><p v-if="error" class="error" role="alert">{{ error }}</p>
        <template #footer><button @click="target = undefined">取消</button><button :class="target.active ? 'danger' : 'primary'" :disabled="busy" @click="setActive">{{ busy ? '正在处理…' : '确认' }}</button></template>
      </UiDialog>
    </template>
  </SettingsLayout>
</template>
