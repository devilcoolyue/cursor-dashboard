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
const referenceFields = [{ key: 'cursor_models', label: 'Cursor Models' }, { key: 'other_models', label: 'Other Models' }, { key: 'overall', label: '综合' }] as const
const references = ref(Object.fromEntries(referenceFields.map(({ key }) => {
  const slot = props.account?.data?.quota?.[key]
  return [key, slot?.limit_source === 'reference' ? String(slot.limit_usd) : '']
})))
const referencesChanged = ref(false)
onBeforeUnmount(() => { controller.abort(); cookie.value = '' })
async function save() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const limits = Object.fromEntries(referenceFields.map(({ key }) => [key, references.value[key] ? Number(references.value[key]) : null]))
    if (referencesChanged.value && Object.values(limits).every(value => value != null) && Math.abs(limits.cursor_models! + limits.other_models! - limits.overall!) > .01) {
      error.value = '综合参考上限应等于两个模型分池之和。'; return
    }
    const changes = { label: label.value.trim() || null, tags: [...new Set(tags.value)],
      ...(referencesChanged.value ? { quota_reference: { cycle_start: props.account?.data?.cycle?.start, ...limits } } : {}) }
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
      <details v-if="account && !authorize && account.data?.cycle?.start" class="quota-reference-fields">
        <summary>额度上限参考值（可选）</summary>
        <p class="field-hint">额度已用尽且历史缺失时，可填写旧版已知的上限（美元）。仅补齐当前账号本账期缺失的金额，不改变剩余百分比；账期或套餐变化后失效。清空可移除手动参考值。</p>
        <div class="reference-inputs"><label v-for="field in referenceFields" :key="field.key">{{ field.label }}<input v-model="references[field.key]" :aria-label="`${field.label} 参考上限`" type="number" min="0.01" max="1000000000" step="0.01" :disabled="busy" placeholder="自动" @input="referencesChanged = true" /></label></div>
      </details>
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
<style scoped>
.quota-reference-fields summary { cursor: pointer; font-size: 12px; color: var(--dim); }
.quota-reference-fields p { margin: 10px 0; }
.reference-inputs { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.reference-inputs label { font-size: 11px; min-width: 0; }
</style>
