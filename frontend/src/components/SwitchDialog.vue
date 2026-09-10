<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import UiDialog from './UiDialog.vue'
import { api, accountPath, message, type Account, type Schema } from '../api'
import { timeText } from '../format'
const props = defineProps<{ account: Account }>()
const emit = defineEmits<{ close: [] }>()
const platform = ref<'macos' | 'windows'>('macos'), confirmed = ref(false), busy = ref(false), error = ref(''), copied = ref(false)
const result = ref<Schema['ManualScript']>()
const controller = new AbortController()
let timer: ReturnType<typeof setTimeout> | undefined
onBeforeUnmount(() => { controller.abort(); clearTimeout(timer); result.value = undefined })
async function generate() {
  busy.value = true; error.value = ''; result.value = undefined; copied.value = false
  try {
    const ticket = await api.request<Schema['SwitchIssued']>(accountPath(props.account) + '/manual-switch', 'POST', undefined, controller.signal)
    const script = await api.request<Schema['ManualScript']>('/manual-switch/consume', 'POST', { token: ticket.token, platform: platform.value }, controller.signal)
    if (script.expires_at * 1000 <= Date.now()) throw new Error('expired')
    result.value = script
    timer = setTimeout(() => { result.value = undefined; error.value = '脚本已过期，请重新生成。' }, Math.max(0, script.expires_at * 1000 - Date.now()))
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
async function copy() {
  if (!result.value || result.value.expires_at * 1000 <= Date.now()) return
  try { await navigator.clipboard.writeText(result.value.command); copied.value = true }
  catch { error.value = '无法写入剪贴板，请手动选择并复制命令。' }
}
</script>
<template>
  <UiDialog title="在本机手工切换" wide @close="emit('close')">
    <p><strong>{{ account.label }}</strong> · {{ account.email }}</p>
    <p>先保存 Cursor 中的工作。你需要在本机终端执行脚本，脚本将正常退出 Cursor、备份数据库、更新登录并重启。</p>
    <p class="notice">脚本包含该账号的登录凭证，请勿分享。页面只能确认脚本已领取，无法验证本机是否执行成功。</p>
    <form v-if="!result" class="form-stack" @submit.prevent="generate">
      <label>操作系统<select v-model="platform" :disabled="busy"><option value="macos">macOS · 终端</option><option value="windows">Windows · PowerShell</option></select></label>
      <label class="check-label"><input v-model="confirmed" type="checkbox" required />我已保存工作，理解脚本会更改本机 Cursor 登录。</label>
      <button class="primary" :disabled="busy || !confirmed">{{ busy ? '正在生成…' : '生成一次性领取的脚本' }}</button>
    </form>
    <template v-else><p class="muted">有效至 {{ timeText(result.expires_at) }} · {{ result.platform === 'macos' ? '粘贴到 macOS 终端' : '粘贴到 PowerShell，不要使用 cmd' }}</p>
      <label>切换命令<textarea :value="result.command" readonly rows="4" spellcheck="false" @focus="($event.target as HTMLTextAreaElement).select()" /></label>
      <div class="actions"><button class="primary" @click="copy">{{ copied ? '已复制' : '复制命令' }}</button></div>
      <details><summary>检查固定脚本内容</summary><pre>{{ result.script }}</pre></details>
    </template>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
  </UiDialog>
</template>
