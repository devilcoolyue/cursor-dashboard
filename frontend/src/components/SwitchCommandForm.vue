<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import UiSelect from './UiSelect.vue'
import { api, accountPath, message, type Account, type Schema } from '../api'
import { native } from '../platform'
import { timeText } from '../format'
const props = defineProps<{ account: Account; local?: boolean; initialPlatform?: string }>()
const platform = ref<'macos' | 'windows'>(props.initialPlatform === 'windows' || (!props.initialPlatform && /Windows/i.test(navigator.userAgent)) ? 'windows' : 'macos')
const confirmed = ref(false), busy = ref(false), error = ref(''), copied = ref(false)
const result = ref<Schema['SwitchCommand']>()
const controller = new AbortController()
let timer: ReturnType<typeof setTimeout> | undefined
onBeforeUnmount(() => { controller.abort(); clearTimeout(timer); result.value = undefined })
async function generate() {
  if (busy.value || !confirmed.value) return
  clearTimeout(timer)
  busy.value = true; error.value = ''; result.value = undefined; copied.value = false
  try {
    const command = props.local
      ? await native<Schema['SwitchCommand']>('switch_command', { workspace_id: props.account.workspace_id, account_id: props.account.id, platform: platform.value, confirmed: confirmed.value })
      : await api.request<Schema['SwitchCommand']>(accountPath(props.account) + '/switch-command', 'POST', { platform: platform.value }, controller.signal)
    if (controller.signal.aborted) return
    if (command.expires_at * 1000 <= Date.now()) { error.value = '命令已过期，请重新生成。'; return }
    result.value = command
    timer = setTimeout(() => { result.value = undefined; error.value = '命令已过期，请重新生成。' }, Math.max(0, command.expires_at * 1000 - Date.now()))
  } catch (reason) { if (!controller.signal.aborted) error.value = message(reason) }
  finally { if (!controller.signal.aborted) busy.value = false }
}
async function copy() {
  if (!result.value || result.value.expires_at * 1000 <= Date.now()) return
  try { await navigator.clipboard.writeText(result.value.command); if (!controller.signal.aborted) copied.value = true }
  catch { if (!controller.signal.aborted) error.value = '无法写入剪贴板，请手动选择并复制命令。' }
}
</script>
<template>
  <p>先保存 Cursor 中的工作。{{ local ? '命令使用本地生成的脚本，请保持桌面应用打开。' : '在本机终端执行命令后，会从此服务下载脚本。' }}脚本将正常退出 Cursor、备份数据库、更新登录并重启。</p>
  <p class="notice">{{ local ? '本地脚本包含该账号的登录凭证，执行后会自动删除。' : '链接最多 5 分钟有效，只能下载一次；命令下载完整脚本后才执行。' }}请勿分享命令。切换结果请在 Cursor 中核对。</p>
  <form v-if="!result" class="form-stack" @submit.prevent="generate">
    <label>操作系统<UiSelect v-model="platform" aria-label="操作系统" :disabled="busy || local" :options="[{ value: 'macos', label: 'macOS · 终端' }, { value: 'windows', label: 'Windows · PowerShell' }]" /></label>
    <label class="check-label"><input v-model="confirmed" type="checkbox" required />我已保存工作，理解脚本会更改本机 Cursor 登录。</label>
    <div class="actions switch-command-actions"><slot name="secondary-action" /><button class="primary" :disabled="busy || !confirmed">{{ busy ? '正在生成…' : '生成终端命令' }}</button></div>
  </form>
  <template v-else>
    <p class="muted">有效至 {{ timeText(result.expires_at) }} · {{ result.platform === 'macos' ? '粘贴到 macOS 系统终端' : '粘贴到独立 PowerShell，不要使用 cmd' }}</p>
    <label>切换命令<textarea :value="result.command" readonly rows="4" spellcheck="false" @focus="($event.target as HTMLTextAreaElement).select()" /></label>
    <div class="actions switch-command-actions"><slot name="secondary-action" /><button class="primary" @click="copy">{{ copied ? '已复制' : '复制命令' }}</button></div>
  </template>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
</template>
