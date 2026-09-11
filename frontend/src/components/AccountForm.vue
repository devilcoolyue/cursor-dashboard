<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import UiDialog from './UiDialog.vue'
import UiIcon from './UiIcon.vue'
import AccountTagPicker from './AccountTagPicker.vue'
import { api, accountPath, spacePath, message, type Account } from '../api'
const props = defineProps<{ workspaceId: string; account?: Account; authorize?: boolean; tagCatalog: Record<string, number> }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const label = ref(props.account?.label || ''), tags = ref([...(props.account?.tags || [])]), cookie = ref(''), busy = ref(false), error = ref('')
const controller = new AbortController()
onBeforeUnmount(() => { controller.abort(); cookie.value = '' })
async function save() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const changes = { label: label.value.trim() || null, tags: [...new Set(tags.value)] }
    const path = props.account ? accountPath(props.account) + (props.authorize ? '/authorization' : '') : `${spacePath(props.workspaceId)}/accounts`
    await api.request<Account>(path, props.account && !props.authorize ? 'PATCH' : 'POST', props.authorize || !props.account ? { ...changes, cookie: cookie.value } : changes, controller.signal)
    emit('saved')
  } catch (reason) { error.value = message(reason) }
  finally { cookie.value = ''; busy.value = false }
}
</script>
<template>
  <UiDialog class="account-form-dialog" :title="account ? (authorize ? '重新授权账号' : '编辑账号') : '添加 Cursor 账号'" dismiss-backdrop @close="emit('close')">
    <template #subtitle>{{ account?.email || '连接账号，查看用量与剩余额度。' }}</template>
    <form id="account-form" class="form-stack account-form" @submit.prevent="save">
      <label>账号名称<input v-model="label" :disabled="busy" maxlength="256" :required="!!account && !authorize" placeholder="例如：日常开发" autofocus /></label>
      <AccountTagPicker v-model="tags" :catalog="tagCatalog" :disabled="busy" />
      <div v-if="authorize || !account" class="cookie-field">
        <label>Cursor 网页会话 Cookie<textarea v-model="cookie" :disabled="busy" rows="4" required maxlength="16384" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="粘贴 WorkosCursorSessionToken 的完整 Value…" aria-describedby="cookie-hint" /></label>
        <p id="cookie-hint" class="field-hint">用于验证账号并申请桌面授权，提交后自动清空。</p>
        <details class="cookie-help"><summary><UiIcon name="chevron" :size="13" />如何获取 Cookie</summary><p>登录 <a href="https://cursor.com/dashboard" target="_blank" rel="noopener noreferrer">cursor.com/dashboard ↗</a>，打开开发者工具 → Application → Cookies → https://cursor.com，复制 <code>WorkosCursorSessionToken</code> 的 Value。</p></details>
      </div>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </form>
    <template #footer><button type="button" @click="emit('close')">取消</button><button type="submit" form="account-form" class="primary" :disabled="busy"><UiIcon v-if="busy" name="refresh" class="spinning" :size="14" />{{ busy ? '正在处理…' : '保存' }}</button></template>
  </UiDialog>
</template>
