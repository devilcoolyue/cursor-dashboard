<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { archive, native, type DesktopStatus } from '../platform'
import { desktopStatus, initialize } from '../state'
import { message } from '../api'
const password = ref(''), error = ref(''), busy = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined, alive = true
onBeforeUnmount(() => { alive = false; clearTimeout(timer); password.value = '' })
async function poll() {
  try {
    const state = await native<DesktopStatus>('status')
    if (!alive) return
    desktopStatus.value = state
    if (state.phase === 'ready') await initialize()
    else if (state.phase === 'starting') timer = setTimeout(poll, 500)
  } catch (reason) { if (alive) error.value = message(reason) }
}
onMounted(() => void poll())
async function retry() {
  busy.value = true; error.value = ''
  try { await native('unlock'); await poll() } catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
async function recover() {
  busy.value = true; error.value = ''
  try {
    const result = await archive('recover', password.value)
    if (!result.cancelled && result.phase !== 'ready') error.value = '归档与当前数据库不匹配，或系统凭证库仍不可用。请确认归档来自本机实例，并解锁凭证库。'
    await poll()
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = false; password.value = '' }
}
</script>
<template><main class="auth-page desktop-gate"><span class="wordmark">CURSOR PANEL<span class="brand-dot">.</span></span>
  <template v-if="desktopStatus?.phase === 'starting'"><h1>正在打开本地账号</h1><p role="status">首次启动正在准备本地数据与系统凭证库…</p></template>
  <template v-else><h1>{{ desktopStatus?.phase === 'locked' ? '本地账号已锁定' : '本地后台暂不可用' }}</h1>
    <p>{{ desktopStatus?.phase === 'locked' ? '请解锁系统凭证库后重试。若原密钥丢失，可使用此前导出的加密归档恢复。' : desktopStatus?.phase === 'in_use' ? '此数据目录正在被其他进程使用。关闭其他 Cursor Panel 或维护命令后重试。' : '请重新打开 Cursor Panel。若仍失败，请检查安装完整性与数据目录访问权限。' }}</p>
    <button :disabled="busy" @click="retry">重试连接</button>
    <form v-if="desktopStatus?.phase === 'locked'" class="form-stack narrow" @submit.prevent="recover"><h2>从归档恢复密钥</h2><label>归档口令<input v-model="password" type="password" required minlength="12" maxlength="256" autocomplete="off" /></label><button :disabled="busy" class="primary">选择加密归档并恢复</button><p class="muted">恢复原密钥不会覆盖账号数据。没有原密钥或可用归档时，已有凭证无法解密。</p></form>
  </template><p v-if="error" role="alert" class="error">{{ error }}</p>
</main></template>
