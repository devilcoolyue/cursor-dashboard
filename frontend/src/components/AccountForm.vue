<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import UiDialog from './UiDialog.vue'
import { api, accountPath, spacePath, message, type Account } from '../api'
const props = defineProps<{ workspaceId: string; account?: Account; authorize?: boolean }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const label = ref(props.account?.label || ''), tags = ref(props.account?.tags.join(', ') || ''), cookie = ref(''), busy = ref(false), error = ref('')
const controller = new AbortController()
onBeforeUnmount(() => { controller.abort(); cookie.value = '' })
async function save() {
  busy.value = true; error.value = ''
  try {
    const changes = { label: label.value.trim() || null, tags: tags.value.split(/[,，]/).map(s => s.trim()).filter(Boolean) }
    const path = props.account ? accountPath(props.account) + (props.authorize ? '/authorization' : '') : `${spacePath(props.workspaceId)}/accounts`
    await api.request<Account>(path, props.account && !props.authorize ? 'PATCH' : 'POST', props.authorize || !props.account ? { ...changes, cookie: cookie.value } : changes, controller.signal)
    emit('saved')
  } catch (reason) { error.value = message(reason) }
  finally { cookie.value = ''; busy.value = false }
}
</script>
<template>
  <UiDialog :title="account ? (authorize ? '重新授权账号' : '编辑账号') : '添加 Cursor 账号'" @close="emit('close')">
    <form class="form-stack" @submit.prevent="save">
      <p v-if="account" class="muted">{{ account.email }}</p>
      <label>账号名称<input v-model="label" maxlength="256" :required="!!account && !authorize" placeholder="例如：日常开发" /></label>
      <label>标签<input v-model="tags" maxlength="4000" placeholder="用逗号分隔，例如：开发, 设计" /></label>
      <template v-if="authorize || !account"><label>Cursor 网页会话 Cookie<input v-model="cookie" type="password" required maxlength="16384" autocomplete="off" spellcheck="false" /></label><p class="muted">粘贴 WorkosCursorSessionToken 的完整值，用于验证账号并申请桌面授权。提交后输入框会清空。</p></template>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="actions"><button type="button" @click="emit('close')">取消</button><button class="primary" :disabled="busy">{{ busy ? '正在处理…' : '保存' }}</button></div>
    </form>
  </UiDialog>
</template>
