<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import UiDialog from './UiDialog.vue'
import UiIcon from './UiIcon.vue'
import SwitchCommandForm from './SwitchCommandForm.vue'
import { message, type Account } from '../api'
import { native, openBackups, connectionId, type Detection, type SwitchState } from '../platform'
const props = defineProps<{ account?: Account; restoreId?: string }>()
const emit = defineEmits<{ close: [] }>()
const detection = ref<Detection>(), state = ref<SwitchState>(), confirmed = ref(false), busy = ref(false), error = ref(''), started = ref(false)
const terminal = ref(false)
const checking = ref(false), pathsOpen = ref(false), pathDirty = ref(false), pathsSaved = ref(false)
const executablePath = ref(''), userDataPath = ref('')
const supportsPaths = computed(() => ['macos', 'windows'].includes(detection.value?.platform || ''))
const canChooseMethod = computed(() => props.account && !props.restoreId && !connectionId.value && !started.value && !state.value?.busy)
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
  try { await poll(); if (!state.value?.busy) await inspectPaths('scan') }
  catch (reason) { if (alive) error.value = message(reason) }
})
async function inspectPaths(action: 'scan' | 'save' | 'reset') {
  if (checking.value || busy.value || state.value?.busy) return
  checking.value = true; confirmed.value = false; error.value = ''; pathsSaved.value = false
  try {
    const result = action === 'scan'
      ? await native<Detection>('detect')
      : await native<Detection>('cursor_paths', {
        executable_path: action === 'reset' ? '' : executablePath.value.trim(),
        user_data_path: action === 'reset' ? '' : userDataPath.value.trim(),
      })
    if (!alive) return
    detection.value = result
    if (action === 'scan' || result.saved) {
      executablePath.value = result.configured_executable_path || ''
      userDataPath.value = result.configured_user_data_path || ''
      pathDirty.value = false
    }
    pathsSaved.value = result.saved === true
    if (!result.available) pathsOpen.value = true
    else if (result.saved) pathsOpen.value = false
  } catch (reason) { if (alive) error.value = message(reason) }
  finally { checking.value = false }
}
function editPaths() { pathDirty.value = true; confirmed.value = false; pathsSaved.value = false }
async function start() {
  if (busy.value || checking.value || pathDirty.value || !confirmed.value || !detection.value?.available || state.value?.busy) return
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
<template><UiDialog :title="restoreId ? '恢复 Cursor 备份' : '切换本机 Cursor'" class="native-switch-dialog" @close="emit('close')">
  <div v-if="account" class="switch-target">
    <span class="switch-target-icon"><UiIcon name="user" :size="20" /></span>
    <div><span class="switch-target-caption">切换到</span><strong>{{ account.label }}</strong><span class="switch-target-email">{{ account.email || '邮箱未知' }}</span></div>
  </div>
  <div v-if="canChooseMethod" class="switch-methods" role="group" aria-label="切换方式">
    <button type="button" :aria-pressed="!terminal" :disabled="busy || checking" @click="terminal = false"><UiIcon name="switch" :size="16" />直接切换</button>
    <button type="button" :aria-pressed="terminal" :disabled="busy || checking || !supportsPaths" @click="terminal = true; confirmed = false"><UiIcon name="terminal" :size="16" />终端执行</button>
  </div>
  <div v-if="terminal && account" class="switch-method-content">
    <SwitchCommandForm :account="account" local :initial-platform="detection?.platform">
      <template #secondary-action><button type="button" @click="emit('close')">取消</button></template>
    </SwitchCommandForm>
  </div>
  <div v-else class="switch-method-content">
  <template v-if="!started && !state?.busy">
    <p class="switch-method-description">{{ restoreId ? '恢复前会先备份当前数据。请保存 Cursor 中的工作后继续。' : '由客户端自动完成切换。请先保存 Cursor 中的工作。' }}</p>
    <ol class="switch-workflow" aria-label="操作步骤"><li><UiIcon name="monitor" :size="16" /><span>退出 Cursor</span></li><li><UiIcon name="shield" :size="16" /><span>备份当前数据</span></li><li><UiIcon name="refresh" :size="16" /><span>{{ restoreId ? '恢复并重启' : '更新登录并重启' }}</span></li></ol>
  </template>
  <section v-if="!started && !state?.busy" class="cursor-paths" aria-label="Cursor 本机路径" :aria-busy="checking">
    <div class="cursor-paths-heading"><span role="status">{{ checking ? '正在检测 Cursor…' : detection?.available ? '已找到本机 Cursor' : '检查本机 Cursor' }}</span><div class="actions"><button type="button" :disabled="checking || busy" @click="inspectPaths('scan')">重新检测</button><button v-if="supportsPaths" type="button" :aria-expanded="pathsOpen" :disabled="checking || busy" @click="pathsOpen = !pathsOpen">{{ pathsOpen ? '收起路径' : '设置路径' }}</button></div></div>
    <p v-if="detection && !detection.available" role="alert" class="notice">{{ detection.reason || '未找到可用的 Cursor，请重新检测或手动填写路径。' }}</p>
    <dl v-if="detection?.executable_path || detection?.database_path" class="cursor-detected-paths"><template v-if="detection.executable_path"><dt>程序</dt><dd>{{ detection.executable_path }}</dd></template><template v-if="detection.database_path"><dt>用户数据库</dt><dd>{{ detection.database_path }}</dd></template></dl>
    <form v-if="pathsOpen && supportsPaths" class="form-stack cursor-path-form" @submit.prevent="inspectPaths('save')">
      <label>Cursor 程序路径<input v-model="executablePath" :disabled="checking || busy" :placeholder="detection?.platform === 'windows' ? '例如 D:\\软件\\Cursor\\Cursor.exe' : '/Applications/Cursor.app'" autocomplete="off" spellcheck="false" @input="editPaths" /></label>
      <p class="muted">可填写安装文件夹或程序完整路径。Windows 可右键 Cursor 快捷方式，在“属性”中复制“目标”。留空则自动查找。</p>
      <label>用户数据目录（可选）<input v-model="userDataPath" :disabled="checking || busy" :placeholder="detection?.platform === 'windows' ? '%APPDATA%\\Cursor' : '~/Library/Application Support/Cursor'" autocomplete="off" spellcheck="false" @input="editPaths" /></label>
      <p class="muted">填写包含 User 文件夹的目录。使用 --user-data-dir 启动时填写对应目录；便携版通常为安装目录下的 data/user-data。留空则自动检测。</p>
      <div class="actions"><button type="button" :disabled="checking || busy" @click="inspectPaths('reset')">恢复自动检测</button><button type="submit" :disabled="checking || busy">验证并保存路径</button></div>
      <p v-if="pathDirty" role="status" class="muted">路径已修改，请先验证并保存。</p>
    </form>
    <p v-if="pathsSaved && !pathDirty" role="status" class="muted cursor-path-saved">路径设置已保存在本机。</p>
  </section>
  <form v-if="!started && !state?.busy && (account || restoreId)" class="form-stack" @submit.prevent="start"><label class="check-label"><input v-model="confirmed" type="checkbox" required :disabled="checking || pathDirty || busy" />我已保存工作，允许退出并{{ restoreId ? '恢复 Cursor 数据' : '更改 Cursor 登录' }}。</label><div class="actions switch-command-actions"><button type="button" @click="emit('close')">取消</button><button class="primary" :disabled="!confirmed || !detection?.available || busy || checking || pathDirty">{{ busy ? '正在准备…' : restoreId ? '备份当前状态并恢复' : '开始切换' }}</button></div></form>
  <template v-if="started || state?.busy || (!account && !restoreId)"><ol class="switch-progress" aria-label="切换进度"><li v-for="(stage, index) in stages.slice(0, -1)" :key="stage" :class="{ current: state?.stage === stage, done: stages.indexOf(state?.stage || '') > index }"><span>{{ stages.indexOf(state?.stage || '') > index ? '✓' : index + 1 }}</span>{{ labels[stage] }}</li></ol><p role="status">{{ labels[state?.stage || ''] }}</p><p v-if="state?.stage === 'quitting'" class="muted">请处理 Cursor 的保存提示或系统自动化权限提示。等待超时会停止操作。</p><p v-if="state?.stage === 'complete'" class="notice">Cursor 已重新打开，请在 Cursor 中核对当前账号。</p></template>
  <p v-if="state?.error && (started || state.stage === 'interrupted')" role="alert" class="error">{{ state.written ? '数据已写入，但重启未完成。请手动打开 Cursor 核对，或从备份恢复。' : '操作未完成。请保存并退出 Cursor 后重试；备份可在个人设置中恢复。' }}</p>
  <div v-if="state?.backup_id" class="actions"><button @click="folder">打开备份目录</button><RouterLink to="/settings" @click="emit('close')">管理与恢复备份</RouterLink></div>
  <p v-if="state?.busy" class="muted">关闭此对话框后，操作会继续。进度可在个人设置中查看。</p>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
  </div>
</UiDialog></template>

<style>
.native-switch-dialog { width: min(600px, calc(100vw - 32px)); }
.switch-target { display: flex; align-items: center; gap: 12px; margin-bottom: 22px; }
.switch-target-icon { display: grid; place-items: center; width: 42px; height: 42px; flex: none; color: var(--accent); background: var(--accent-bg); border-radius: var(--radius-ctl); }
.switch-target > div { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 1px 9px; min-width: 0; align-items: baseline; }
.switch-target-caption { color: var(--dim); font-size: 11px; }
.switch-target strong { font-size: 14px; overflow-wrap: anywhere; }
.switch-target-email { grid-column: 1 / -1; color: var(--dim); font-size: 12px; overflow-wrap: anywhere; }
.switch-methods { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; padding: 4px; margin-bottom: 22px; background: var(--input-bg); border: 1px solid var(--line); border-radius: var(--radius-ctl); }
.switch-methods button { color: var(--dim); background: transparent; border-color: transparent; font-size: 13px; padding: 9px 12px; }
.switch-methods button[aria-pressed=true] { color: var(--accent); background: var(--accent-bg); border-color: color-mix(in srgb, var(--accent) 35%, var(--line)); }
.switch-methods button:hover:not(:disabled) { color: var(--fg); background: var(--row-hover-bg); }
.switch-method-content { animation: enter .18s ease-out; }
.switch-method-content > p:not(.notice):not(.error), .switch-method-description { font-size: 12px; color: var(--dim); line-height: 1.8; }
.switch-workflow { display: flex; justify-content: space-between; list-style: none; margin: 18px 0 24px; padding: 16px 0; border-block: 1px solid var(--line); }
.switch-workflow li { display: flex; align-items: center; gap: 7px; font-size: 11px; }
.switch-workflow li > svg { color: var(--accent); }
.switch-workflow li + li::before { content: '›'; color: var(--dimmer); margin-right: 14px; }
.native-switch-dialog .check-label { align-items: flex-start; font-size: 12px; line-height: 1.7; }
.native-switch-dialog .check-label input { margin: 2px 0 0; }
.cursor-paths { margin-bottom: 20px; padding-bottom: 18px; border-bottom: 1px solid var(--line); }
.cursor-paths-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 12px; }
.cursor-paths-heading button { padding: 5px 9px; font-size: 11px; }
.cursor-detected-paths { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 7px 12px; margin: 12px 0 0; font-size: 11px; line-height: 1.6; }
.cursor-detected-paths dt { color: var(--dim); }
.cursor-detected-paths dd { margin: 0; overflow-wrap: anywhere; }
.cursor-path-form { margin-top: 16px; }
.cursor-path-form .muted { margin: -5px 0 0; font-size: 11px; line-height: 1.7; }
.cursor-path-form input { font-size: 12px; }
.cursor-path-saved { margin: 10px 0 0; font-size: 11px; }
@media (max-width: 480px) {
  .cursor-paths-heading { align-items: flex-start; flex-direction: column; }
  .switch-workflow { gap: 14px; flex-direction: column; padding: 15px 0; }
  .switch-workflow li + li::before { content: none; }
}
</style>
