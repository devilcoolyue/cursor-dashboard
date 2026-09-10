<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import UiDialog from './UiDialog.vue'
import { api, accountPath, spacePath, message, type Account, type Schema } from '../api'
import { roleText } from '../format'
const props = defineProps<{ account: Account }>()
const emit = defineEmits<{ close: [] }>()
const members = ref<Schema['MemberView'][]>([]), grants = ref<Record<string, string>>({}), error = ref(''), busy = ref(false)
const controller = new AbortController()
onBeforeUnmount(() => controller.abort())
async function load() {
  const [people, access] = await Promise.all([
    api.request<Schema['MemberView'][]>(spacePath(props.account.workspace_id) + '/members', 'GET', undefined, controller.signal),
    api.request<Schema['GrantView'][]>(accountPath(props.account) + '/grants', 'GET', undefined, controller.signal),
  ])
  members.value = people.filter(m => ['member', 'viewer'].includes(m.role))
  grants.value = Object.fromEntries(access.map(g => [g.user_id, g.level]))
}
onMounted(async () => { busy.value = true; try { await load() } catch (reason) { error.value = message(reason) } finally { busy.value = false } })
async function change(userId: string, event: Event) {
  const level = (event.target as HTMLSelectElement).value
  busy.value = true; error.value = ''
  try { await api.request(accountPath(props.account) + '/grants/' + userId, level ? 'PUT' : 'DELETE', level ? { level } : undefined, controller.signal); await load() }
  catch (reason) { error.value = message(reason); (event.target as HTMLSelectElement).value = grants.value[userId] || '' }
  finally { busy.value = false }
}
</script>
<template>
  <UiDialog :title="`${account.label} · 账号授权`" @close="emit('close')">
    <p class="muted">查看权限允许查看额度和明细；使用权限还允许刷新与手工切换。所有者和管理员自动管理全部账号。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="!members.length && !busy">暂无普通成员，请先在空间设置中邀请成员。</p>
    <div v-for="member in members" :key="member.id" class="setting-row"><div><strong>{{ member.login }}</strong><small>{{ roleText(member.role) }}{{ member.active ? '' : ' · 已停用' }}</small></div>
      <select :aria-label="`${member.login}的账号权限`" :value="grants[member.id] || ''" :disabled="busy" @change="change(member.id, $event)"><option value="">无权限</option><option value="view">查看</option><option v-if="member.role !== 'viewer'" value="use">使用</option></select>
    </div>
  </UiDialog>
</template>
