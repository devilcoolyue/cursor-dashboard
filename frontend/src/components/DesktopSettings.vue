<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { archive, native, openBackups, type DesktopStatus } from '../platform'
import { desktopStatus, workspaceId } from '../state'
import { message } from '../api'
import { timeText } from '../format'
import NativeSwitchDialog from './NativeSwitchDialog.vue'
const password = ref(''), confirmation = ref(''), action = ref<'export' | 'import'>('export'), busy = ref(false), error = ref(''), result = ref('')
const backups = ref<{ id: string; created_at: number }[]>([]), restoreId = ref(''), showProgress = ref(false)
let alive = true
onBeforeUnmount(() => { alive = false; password.value = ''; confirmation.value = '' })
async function loadBackups() { try { const items = await native<typeof backups.value>('backups'); if (alive) backups.value = items } catch (reason) { if (alive) error.value = message(reason) } }
onMounted(() => void loadBackups())
async function background(event: Event) {
  busy.value = true; error.value = ''
  try { desktopStatus.value = await native<DesktopStatus>('background', { enabled: (event.target as HTMLInputElement).checked }) }
  catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
async function transfer() {
  busy.value = true; error.value = ''; result.value = ''
  try {
    if (action.value === 'export' && password.value !== confirmation.value) { error.value = '两次口令不一致。'; return }
    const outcome = await archive(action.value, password.value, workspaceId.value)
    if (!outcome.cancelled && alive) result.value = `${action.value === 'export' ? '已导出' : '已导入'} ${outcome.count} 个账号。`
  } catch (reason) { if (alive) error.value = message(reason) }
  finally { password.value = ''; confirmation.value = ''; busy.value = false }
}
async function folder() { try { await openBackups() } catch { error.value = '无法打开备份目录。' } }
async function closeProgress() { restoreId.value = ''; showProgress.value = false; await loadBackups() }
</script>
<template>
  <section class="settings-section"><h2>桌面运行</h2><label class="check-label"><input type="checkbox" :checked="desktopStatus?.background" :disabled="busy" @change="background" />关闭窗口后驻留托盘并定期刷新</label><p class="muted">默认关闭窗口即退出。启用后逐个刷新账号，休眠恢复后分散查询；断网保留最后成功快照。</p><p v-if="desktopStatus?.last_refresh" class="muted">最近后台尝试 {{ timeText(desktopStatus.last_refresh) }}</p><p v-if="desktopStatus?.refresh_error" class="notice">最近刷新失败，已保留上次成功数据。</p></section>
  <section class="settings-section"><h2>加密归档</h2><p>导出账号、标签和额度快照，并保留本机密钥恢复材料。请妥善保存归档口令。</p><form class="form-stack narrow" @submit.prevent="transfer"><label>操作<select aria-label="归档操作" v-model="action" :disabled="busy"><option value="export">导出加密归档</option><option value="import">导入到空的个人空间</option></select></label><p v-if="action === 'import'" class="notice">仅支持导入到没有账号的个人空间；不会合并或覆盖已有账号。</p><label>归档口令<input v-model="password" type="password" minlength="12" maxlength="256" required autocomplete="off" /></label><label v-if="action === 'export'">再次输入口令<input v-model="confirmation" type="password" minlength="12" maxlength="256" required autocomplete="off" /></label><small>至少 12 个字符。文件由系统对话框选择，导出请使用新文件名。</small><button class="primary" :disabled="busy">{{ busy ? '处理中…' : action === 'export' ? '选择位置并导出' : '选择归档并导入' }}</button></form><p v-if="result" role="status" class="notice">{{ result }}</p></section>
  <section class="settings-section"><div class="section-heading"><h2>Cursor 切换与备份</h2><button @click="folder">打开备份目录</button></div><p class="muted">备份包含 Cursor 登录数据，请勿分享。恢复前会再备份当前状态。</p><button v-if="desktopStatus?.switch?.stage !== 'idle'" @click="showProgress = true">查看最近操作</button><div v-for="backup in backups" :key="backup.id" class="setting-row"><div><strong>Cursor 数据备份</strong><small>{{ timeText(backup.created_at) }}</small></div><button :disabled="desktopStatus?.switch?.busy" @click="restoreId = backup.id">恢复此备份</button></div><p v-if="!backups.length" class="muted">尚无切换备份。</p><button @click="loadBackups">重载备份</button></section>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
  <NativeSwitchDialog v-if="restoreId || showProgress" :restore-id="restoreId || undefined" @close="closeProgress" />
</template>
