<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, message, type Schema } from '../api'
import { loadMe, me } from '../state'
import UiDialog from './UiDialog.vue'
import UiIcon from './UiIcon.vue'

const props = defineProps<{ mode: 'create' | 'join' }>()
const emit = defineEmits<{ close: [] }>()
const router = useRouter()
const name = ref(''), invitation = ref(''), error = ref(''), busy = ref(false)
const controller = new AbortController()
onBeforeUnmount(() => { controller.abort(); invitation.value = '' })

function invitationToken(value: string) {
  const text = value.trim()
  if (!text.includes('://')) return text
  try {
    const url = new URL(text)
    return new URLSearchParams(url.hash.split('?')[1] || '').get('token') || ''
  } catch { return '' }
}
async function submit() {
  if (busy.value) return
  error.value = ''
  const token = invitationToken(invitation.value)
  if (props.mode === 'create' && !name.value.trim()) { error.value = '请输入空间名称。'; return }
  if (props.mode === 'join' && (token.length < 20 || token.length > 128)) { error.value = '请输入完整的邀请链接或邀请票据。'; return }
  busy.value = true
  try {
    const workspace = props.mode === 'create'
      ? (await api.request<Schema['WorkspaceCreated']>('/workspaces', 'POST', { name: name.value.trim() }, controller.signal)).id
      : (await api.request<Schema['Joined']>('/invitations/accept', 'POST', { token }, controller.signal)).workspace_id
    await loadMe(workspace)
    await router.push('/accounts')
    emit('close')
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
</script>
<template>
  <UiDialog class="team-form-dialog" :title="mode === 'create' ? '创建团队空间' : '接受团队邀请'" dismiss-backdrop @close="emit('close')">
    <template #subtitle>{{ mode === 'create' ? '与团队共享账号，按成员分配使用权限。' : '使用受邀账号，加入已有的团队空间。' }}</template>
    <form id="team-form" class="form-stack settings-form" @submit.prevent="submit">
      <template v-if="mode === 'create'">
        <label>空间名称<input v-model="name" :disabled="busy" required maxlength="128" placeholder="例如：产品研发团队" autofocus aria-describedby="team-name-hint" /></label>
        <p id="team-name-hint" class="field-hint">你将成为空间所有者，可邀请成员并管理账号授权。</p>
        <div class="form-note"><UiIcon name="users" :size="17" /><p>个人账号仅自己可见。创建后，在团队空间添加需要共享的账号。</p></div>
      </template>
      <template v-else>
        <label>邀请链接或票据<textarea v-model="invitation" :disabled="busy" required maxlength="2048" rows="3" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="粘贴管理员发来的邀请链接或票据…" autofocus aria-describedby="team-invitation-hint" /></label>
        <p id="team-invitation-hint" class="field-hint">邀请有效期为 7 天，请确认当前邮箱与受邀邮箱一致。</p>
        <div class="form-note"><UiIcon name="user" :size="17" /><p>当前账号 <strong>{{ me?.login }}</strong></p></div>
      </template>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </form>
    <template #footer><button type="button" @click="emit('close')">取消</button><button type="submit" form="team-form" class="primary" :disabled="busy"><UiIcon v-if="busy" name="refresh" class="spinning" :size="14" />{{ busy ? '正在处理…' : mode === 'create' ? '创建空间' : '接受邀请' }}</button></template>
  </UiDialog>
</template>
