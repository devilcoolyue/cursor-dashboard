<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { api, spacePath, message, type Schema } from '../api'
import { activeSpace, loadMe, me, activeConnection } from '../state'
import { roleText, timeText } from '../format'
import UiDialog from '../components/UiDialog.vue'
import UiSelect from '../components/UiSelect.vue'
import AuditList from '../components/AuditList.vue'
import SettingsLayout from '../components/SettingsLayout.vue'
import SettingsSection from '../components/SettingsSection.vue'
import UiIcon from '../components/UiIcon.vue'
const router = useRouter()
const members = ref<Schema['MemberView'][]>([]), invitations = ref<Schema['InvitationView'][]>([]), events = ref<Schema['AuditView'][]>([])
const login = ref(''), role = ref('member'), link = ref(''), error = ref(''), busy = ref(false), confirm = ref<{ title: string; text: string; action: () => Promise<void> }>()
const roleOptions = computed(() => [
  { value: 'member', label: '成员' }, { value: 'viewer', label: '只读成员' },
  ...(activeSpace.value?.capabilities.manage_admins ? [{ value: 'admin', label: '管理员' }] : []),
])
const controller = new AbortController()
const base = spacePath(activeSpace.value!.id)
const auditOffset = ref(0), loaded = ref(false), copied = ref(false)
onBeforeUnmount(() => { controller.abort(); link.value = '' })
async function load() {
  const space = activeSpace.value
  if (!space) return
  const result = await Promise.all([
    space.capabilities.manage_members ? api.request<Schema['MemberView'][]>(base + '/members', 'GET', undefined, controller.signal) : Promise.resolve([]),
    space.capabilities.manage_members ? api.request<Schema['InvitationView'][]>(base + '/invitations', 'GET', undefined, controller.signal) : Promise.resolve([]),
    space.capabilities.audit ? api.request<Schema['AuditView'][]>(base + `/audit?limit=25&offset=${auditOffset.value}`, 'GET', undefined, controller.signal) : Promise.resolve([]),
  ])
  ;[members.value, invitations.value, events.value] = result
  loaded.value = true
}
async function run(action: () => Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; try { await action() } catch (reason) { error.value = message(reason) } finally { busy.value = false } }
onMounted(() => void run(load))
async function invite() {
  await run(async () => { const result = await api.request<Schema['InvitationIssued']>(base + '/invitations', 'POST', { login: login.value.trim(), role: role.value }, controller.signal); link.value = `${activeConnection.value?.origin || location.origin}/#/join?token=${encodeURIComponent(result.token)}`; login.value = ''; copied.value = false; await load() })
}
async function changeRole(member: Schema['MemberView'], next: string) {
  await run(async () => { await api.request(base + '/members/' + member.id, 'PUT', { role: next }, controller.signal); await load(); await loadMe() })
}
function ask(title: string, text: string, action: () => Promise<void>) { error.value = ''; confirm.value = { title, text, action } }
async function approve() { const action = confirm.value?.action; if (action) await run(async () => { await action(); confirm.value = undefined }) }
async function removeMember(member: Schema['MemberView']) { await api.request(base + '/members/' + member.id, 'DELETE', undefined, controller.signal); await load() }
async function transfer(member: Schema['MemberView']) { await api.request(base + '/owner', 'PUT', { user_id: member.id }, controller.signal); await loadMe(); await load() }
async function leave() { await api.request(base + '/members/' + me.value!.id, 'DELETE', undefined, controller.signal); await loadMe(); await router.replace('/accounts') }
async function deleteSpace() { await api.request(base, 'DELETE', undefined, controller.signal); await loadMe(); await router.replace('/accounts') }
async function revoke(id: string) { await run(async () => { await api.request(base + '/invitations/' + id, 'DELETE', undefined, controller.signal); await load() }) }
async function copyLink() { error.value = ''; try { await navigator.clipboard.writeText(link.value); copied.value = true } catch { error.value = '请手动选择并复制邀请链接。' } }
async function turnPage(delta: number) { auditOffset.value = Math.max(0, auditOffset.value + delta); await run(load) }
</script>
<template>
  <SettingsLayout v-if="activeSpace" title="空间设置" :context="activeSpace.kind === 'personal' ? '个人空间' : activeSpace.name" :error="!confirm && !link ? error : ''">
    <template #actions><button class="subtle-button" :disabled="busy" @click="run(load)"><UiIcon name="refresh" :class="{ spinning: busy }" :size="14" />重载设置</button></template>
    <template v-if="activeSpace.capabilities.manage_members">
      <SettingsSection title="邀请成员" description="通过邮箱邀请同事加入当前空间。">
        <form class="form-stack settings-form" @submit.prevent="invite">
          <div class="invite-form-fields"><label>受邀邮箱<input v-model="login" :disabled="busy" type="email" required maxlength="320" placeholder="name@company.com" autocomplete="email" /></label><label>空间角色<UiSelect v-model="role" aria-label="空间角色" :options="roleOptions" :disabled="busy" /></label><button class="primary" :disabled="busy"><UiIcon name="plus" :size="14" />创建邀请</button></div>
          <p class="field-hint">邀请有效期 7 天。加入后，在账号菜单中分配查看或使用权限。</p>
        </form>
      </SettingsSection>
      <SettingsSection title="成员" description="管理成员角色与空间权限。" :count="loaded ? members.length : undefined">
        <p v-if="!loaded && busy" class="settings-empty" role="status">正在载入成员…</p>
        <div class="settings-list" :aria-busy="busy"><div v-for="member in members" :key="member.id" class="setting-row member-row">
          <div class="setting-identity"><span class="settings-avatar">{{ member.login.charAt(0).toUpperCase() }}</span><div><strong>{{ member.login }}<span v-if="member.id === me?.id" class="settings-badge">你</span></strong><small>{{ roleText(member.role) }}<span v-if="!member.active" class="danger-text"> · 已停用</span></small></div></div>
          <div class="actions setting-row-actions"><UiSelect v-if="member.role !== 'owner' && (activeSpace.capabilities.manage_admins || member.role !== 'admin')" :aria-label="`${member.login}的角色`" :model-value="member.role" :options="roleOptions" :disabled="busy" @update:model-value="changeRole(member, $event)" /><button v-if="activeSpace.capabilities.transfer_owner && member.role !== 'owner' && member.active" class="subtle-button" :disabled="busy" @click="ask('转移所有权', `将空间所有权交给 ${member.login}？你将成为管理员。`, () => transfer(member))">转移所有权</button><button v-if="member.role !== 'owner' && (activeSpace.capabilities.manage_admins || member.role !== 'admin') && member.id !== me?.id" class="subtle-button danger-text" :disabled="busy" @click="ask('移除成员', `移除 ${member.login} 并撤销其账号权限？`, () => removeMember(member))">移除</button></div>
        </div></div>
      </SettingsSection>
      <SettingsSection title="待接受邀请" description="查看尚未加入的成员，或撤销邀请。" :count="loaded ? invitations.length : undefined">
        <p v-if="loaded && !invitations.length" class="settings-empty"><UiIcon name="mail" :size="18" />暂无有效邀请</p>
        <div class="settings-list"><div v-for="invitation in invitations" :key="invitation.id" class="setting-row"><div class="setting-identity"><span class="settings-avatar neutral"><UiIcon name="mail" :size="17" /></span><div><strong>{{ invitation.login }}</strong><small>{{ roleText(invitation.role) }} · 到期 {{ timeText(invitation.expires_at) }}</small></div></div><button v-if="invitation.role !== 'admin' || activeSpace.capabilities.manage_admins" class="subtle-button" :disabled="busy" @click="revoke(invitation.id)">撤销邀请</button></div></div>
      </SettingsSection>
    </template>
    <SettingsSection v-if="activeSpace.capabilities.audit" title="操作记录" :description="activeSpace.kind === 'personal' ? '个人空间仅本人可见，记录账号与授权的变更。' : '追踪当前空间的账号、成员及授权变更。'">
      <AuditList :events="events" :loading="!loaded && busy" :actors="Object.fromEntries(members.map(member => [member.id, member.login]))" />
      <div class="settings-pagination"><span>第 {{ Math.floor(auditOffset / 25) + 1 }} 页</span><div class="actions"><button :disabled="busy || !auditOffset" @click="turnPage(-25)">上一页</button><button :disabled="busy || events.length < 25" @click="turnPage(25)">下一页</button></div></div>
    </SettingsSection>
    <SettingsSection v-if="activeSpace.kind === 'team'" title="空间管理" :description="activeSpace.capabilities.delete ? '删除空间会清除其中的账号和授权。' : '退出后将失去当前空间的账号权限。'">
      <div class="settings-management"><p>{{ activeSpace.capabilities.delete ? '此操作无法撤销，请确认团队已不再使用该空间。' : '如需重新加入，请联系空间管理员发送邀请。' }}</p><button v-if="activeSpace.capabilities.delete" class="danger-text" :disabled="busy" @click="ask('删除团队空间', `删除 ${activeSpace.name} 及其中的全部账号和授权？此操作不能撤销。`, deleteSpace)"><UiIcon name="trash" :size="14" />删除团队空间</button><button v-else class="danger-text" :disabled="busy" @click="ask('退出团队', '退出后将失去本空间的全部账号权限。', leave)">退出团队空间</button></div>
    </SettingsSection>
    <template #dialogs>
      <UiDialog v-if="link" title="邀请已创建" dismiss-backdrop @close="link = ''">
        <template #subtitle>将邀请链接发送给受邀邮箱的使用者。</template>
        <div class="form-stack settings-form"><label>邀请链接<textarea :value="link" readonly rows="3" @focus="($event.target as HTMLTextAreaElement).select()" /></label><p class="field-hint">链接有效期 7 天，关闭后无法再次查看，请及时复制。</p><p v-if="copied" class="form-success" role="status"><UiIcon name="check" :size="14" />邀请链接已复制</p><p v-if="error" class="error" role="alert">{{ error }}</p></div>
        <template #footer><button @click="link = ''">完成</button><button class="primary" @click="copyLink"><UiIcon :name="copied ? 'check' : 'copy'" :size="14" />{{ copied ? '已复制' : '复制链接' }}</button></template>
      </UiDialog>
      <UiDialog v-if="confirm" :title="confirm.title" @close="confirm = undefined">
        <p class="confirmation-copy">{{ confirm.text }}</p><p v-if="error" class="error" role="alert">{{ error }}</p>
        <template #footer><button @click="confirm = undefined">取消</button><button class="danger" :disabled="busy" @click="approve">{{ busy ? '正在处理…' : '确认' }}</button></template>
      </UiDialog>
    </template>
  </SettingsLayout>
</template>
