<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import UiDialog from './UiDialog.vue'
import { message, type Account } from '../api'
import { native, openBackups, connectionId, type Detection, type SwitchState } from '../platform'
const props = defineProps<{ account?: Account; restoreId?: string }>()
const emit = defineEmits<{ close: [] }>()
const detection = ref<Detection>(), state = ref<SwitchState>(), confirmed = ref(false), busy = ref(false), error = ref(''), started = ref(false)
const stages = ['authorizing', 'checking', 'quitting', 'backing_up', 'writing', 'restarting', 'complete']
const labels: Record<string, string> = { authorizing: '验证账号授权', checking: '检查 Cursor', quitting: '等待 Cursor 正常退出', backing_up: '备份本地数据', writing: '更新登录', restarting: '重新打开 Cursor', complete: '操作完成', failed: '操作未完成', interrupted: '上次操作已中断' }
let timer: ReturnType<typeof setTimeout> | undefined, alive = true
onBeforeUnmount(() => { alive = false; clearTimeout(timer) })
async function poll() {
  try {
    const result = await native<SwitchState>('switch_status')
    if (!alive) return
    state.value = result
    if (result.busy) timer = setTimeout(poll, 600)
  } catch (reason) { if (alive) error.value = message(reason) }
}
onMounted(async () => {
  try { const result = await native<Detection>('detect'); if (alive) detection.value = result; await poll() }
  catch (reason) { if (alive) error.value = message(reason) }
})
async function start() {
  busy.value = true; error.value = ''
  try {
    const result = props.restoreId
      ? await native<SwitchState>('restore', { backup_id: props.restoreId, confirmed: confirmed.value })
      : await native<SwitchState>('switch', { connection_id: connectionId.value, workspace_id: props.account?.workspace_id, account_id: props.account?.id, confirmed: confirmed.value })
    if (!alive) return
    state.value = result; started.value = true
    await poll()
  } catch (reason) { if (alive) error.value = message(reason) }
  finally { busy.value = false }
}
async function folder() { try { await openBackups() } catch { error.value = '无法打开备份目录。请重试。' } }
</script>
<template><UiDialog :title="restoreId ? '恢复 Cursor 备份' : '切换本机 Cursor'" @close="emit('close')">
  <p v-if="account"><strong>{{ account.label }}</strong> · {{ account.email }}</p>
  <p v-if="!started">保存 Cursor 中的工作后继续。应用将正常退出 Cursor、备份当前数据、{{ restoreId ? '恢复所选备份' : '更新登录' }}并重启。</p>
  <p v-if="detection && !detection.available" role="alert" class="notice">未检测到支持的 Cursor 安装与默认用户数据库。请安装并打开一次 Cursor，然后重试。当前支持 macOS 与 Windows 的默认目录。</p>
  <form v-if="!started && !state?.busy && (account || restoreId)" class="form-stack" @submit.prevent="start"><label class="check-label"><input v-model="confirmed" type="checkbox" required />我已保存工作，允许退出并{{ restoreId ? '恢复 Cursor 数据' : '更改 Cursor 登录' }}。</label><button class="primary" :disabled="!confirmed || !detection?.available || busy">{{ restoreId ? '备份当前状态并恢复' : '开始切换' }}</button></form>
  <template v-if="started || state?.busy || (!account && !restoreId)"><ol class="switch-progress" aria-label="切换进度"><li v-for="(stage, index) in stages.slice(0, -1)" :key="stage" :class="{ current: state?.stage === stage, done: stages.indexOf(state?.stage || '') > index }"><span>{{ stages.indexOf(state?.stage || '') > index ? '✓' : index + 1 }}</span>{{ labels[stage] }}</li></ol><p role="status">{{ labels[state?.stage || ''] }}</p><p v-if="state?.stage === 'quitting'" class="muted">请处理 Cursor 的保存提示或系统自动化权限提示。等待超时会停止操作。</p><p v-if="state?.stage === 'complete'" class="notice">Cursor 已重新打开，请在 Cursor 中核对当前账号。</p></template>
  <p v-if="state?.error && (started || state.stage === 'interrupted')" role="alert" class="error">{{ state.written ? '数据已写入，但重启未完成。请手动打开 Cursor 核对，或从备份恢复。' : '操作未完成。请保存并退出 Cursor 后重试；备份可在个人设置中恢复。' }}</p>
  <div v-if="state?.backup_id" class="actions"><button @click="folder">打开备份目录</button><RouterLink to="/settings" @click="emit('close')">管理与恢复备份</RouterLink></div>
  <p v-if="state?.busy" class="muted">关闭此对话框后，操作会继续。进度可在个人设置中查看。</p>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
</UiDialog></template>
