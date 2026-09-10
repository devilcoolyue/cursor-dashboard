<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { api, spacePath, message, type Schema } from '../api'
import { activeSpace, loadMe, me } from '../state'
import { roleText, timeText } from '../format'
import UiDialog from '../components/UiDialog.vue'
import AuditList from '../components/AuditList.vue'
const router = useRouter()
const members = ref<Schema['MemberView'][]>([]), invitations = ref<Schema['InvitationView'][]>([]), events = ref<Schema['AuditView'][]>([])
const login = ref(''), role = ref('member'), link = ref(''), error = ref(''), busy = ref(false), confirm = ref<{ title: string; text: string; action: () => Promise<void> }>()
const controller = new AbortController()
const base = spacePath(activeSpace.value!.id)
const auditOffset = ref(0)
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
}
async function run(action: () => Promise<void>) { busy.value = true; error.value = ''; try { await action() } catch (reason) { error.value = message(reason) } finally { busy.value = false } }
onMounted(() => void run(load))
async function invite() {
  await run(async () => { const result = await api.request<Schema['InvitationIssued']>(base + '/invitations', 'POST', { login: login.value, role: role.value }, controller.signal); link.value = `${location.origin}/#/join?token=${encodeURIComponent(result.token)}`; login.value = ''; await load() })
}
async function changeRole(member: Schema['MemberView'], event: Event) {
  const next = (event.target as HTMLSelectElement).value
  await run(async () => { await api.request(base + '/members/' + member.id, 'PUT', { role: next }, controller.signal); await load(); await loadMe() })
  ;(event.target as HTMLSelectElement).value = members.value.find(m => m.id === member.id)?.role || member.role
}
function ask(title: string, text: string, action: () => Promise<void>) { confirm.value = { title, text, action } }
async function approve() { const action = confirm.value?.action; if (action) await run(async () => { await action(); confirm.value = undefined }) }
async function removeMember(member: Schema['MemberView']) { await api.request(base + '/members/' + member.id, 'DELETE', undefined, controller.signal); await load() }
async function transfer(member: Schema['MemberView']) { await api.request(base + '/owner', 'PUT', { user_id: member.id }, controller.signal); await loadMe(); await load() }
async function leave() { await api.request(base + '/members/' + me.value!.id, 'DELETE', undefined, controller.signal); await loadMe(); await router.replace('/accounts') }
async function deleteSpace() { await api.request(base, 'DELETE', undefined, controller.signal); await loadMe(); await router.replace('/accounts') }
async function revoke(id: string) { await run(async () => { await api.request(base + '/invitations/' + id, 'DELETE', undefined, controller.signal); await load() }) }
async function copyLink() { try { await navigator.clipboard.writeText(link.value) } catch { error.value = '请手动选择并复制邀请链接。' } }
async function turnPage(delta: number) { auditOffset.value = Math.max(0, auditOffset.value + delta); await run(load) }
</script>
<template>
  <section v-if="activeSpace" class="workspace-page settings-page"><header class="page-heading"><div><p class="eyebrow">{{ activeSpace.name }}</p><h1>空间设置</h1><p class="muted">{{ activeSpace.kind === 'personal' ? '个人空间仅本人可见。' : '管理团队成员、邀请和操作记录。' }}</p></div><button :disabled="busy" @click="run(load)">重载设置</button></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <template v-if="activeSpace.capabilities.manage_members">
      <section class="settings-section"><h2>邀请成员</h2><form class="inline-form" @submit.prevent="invite"><label>受邀邮箱<input v-model="login" type="email" required maxlength="320" /></label><label>空间角色<select v-model="role"><option value="member">成员</option><option value="viewer">只读成员</option><option v-if="activeSpace.capabilities.manage_admins" value="admin">管理员</option></select></label><button class="primary" :disabled="busy">创建邀请</button></form><p class="muted">邀请有效期 7 天。加入后仍需在账号菜单中单独分配查看或使用权限。</p></section>
      <section class="settings-section"><h2>成员 <span class="muted">{{ members.length }}</span></h2><div v-for="member in members" :key="member.id" class="setting-row"><div><strong>{{ member.login }}</strong><small>{{ roleText(member.role) }}{{ member.id === me?.id ? ' · 你' : '' }}{{ member.active ? '' : ' · 已停用' }}</small></div><div class="actions"><select v-if="member.role !== 'owner' && (activeSpace.capabilities.manage_admins || member.role !== 'admin')" :aria-label="`${member.login}的角色`" :value="member.role" :disabled="busy" @change="changeRole(member, $event)"><option value="member">成员</option><option value="viewer">只读成员</option><option v-if="activeSpace.capabilities.manage_admins" value="admin">管理员</option></select><button v-if="activeSpace.capabilities.transfer_owner && member.role !== 'owner' && member.active" :disabled="busy" @click="ask('转移所有权', `将空间所有权交给 ${member.login}？你将成为管理员。`, () => transfer(member))">转移所有权</button><button v-if="member.role !== 'owner' && (activeSpace.capabilities.manage_admins || member.role !== 'admin') && member.id !== me?.id" class="danger-text" :disabled="busy" @click="ask('移除成员', `移除 ${member.login} 并撤销其账号权限？`, () => removeMember(member))">移除</button></div></div></section>
      <section class="settings-section"><h2>待接受邀请</h2><p v-if="!invitations.length" class="muted">暂无有效邀请。</p><div v-for="invitation in invitations" :key="invitation.id" class="setting-row"><div><strong>{{ invitation.login }}</strong><small>{{ roleText(invitation.role) }} · 到期 {{ timeText(invitation.expires_at) }}</small></div><button v-if="invitation.role !== 'admin' || activeSpace.capabilities.manage_admins" :disabled="busy" @click="revoke(invitation.id)">撤销邀请</button></div></section>
    </template>
    <section v-if="activeSpace.capabilities.audit" class="settings-section"><div class="section-heading"><h2>操作记录</h2><div class="actions"><button :disabled="busy || !auditOffset" @click="turnPage(-25)">上一页</button><button :disabled="busy || events.length < 25" @click="turnPage(25)">下一页</button></div></div><AuditList :events="events" /></section>
    <section v-if="activeSpace.kind === 'team'" class="settings-section"><h2>空间管理</h2><button v-if="activeSpace.capabilities.delete" class="danger-text" :disabled="busy" @click="ask('删除团队空间', `删除 ${activeSpace.name} 及其中的全部账号和授权？此操作不能撤销。`, deleteSpace)">删除团队空间</button><button v-else class="danger-text" :disabled="busy" @click="ask('退出团队', '退出后将失去本空间的全部账号权限。', leave)">退出团队空间</button></section>
    <UiDialog v-if="link" title="邀请已创建" @close="link = ''"><p>将链接交给受邀邮箱的使用者。关闭后无法再次查看原票据。</p><label>邀请链接<textarea :value="link" readonly rows="3" @focus="($event.target as HTMLTextAreaElement).select()" /></label><div class="actions"><button class="primary" @click="copyLink">复制链接</button></div></UiDialog>
    <UiDialog v-if="confirm" :title="confirm.title" @close="confirm = undefined"><p>{{ confirm.text }}</p><p v-if="error" class="error" role="alert">{{ error }}</p><div class="actions"><button @click="confirm = undefined">取消</button><button class="danger" :disabled="busy" @click="approve">确认</button></div></UiDialog>
  </section>
</template>
