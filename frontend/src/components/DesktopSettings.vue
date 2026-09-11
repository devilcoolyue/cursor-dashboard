<script setup lang="ts">
import { connectionId } from '../platform'
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { archive, native, openBackups, type DesktopStatus } from '../platform'
import { desktopStatus, workspaceId } from '../state'
import { message } from '../api'
import { timeText } from '../format'
import NativeSwitchDialog from './NativeSwitchDialog.vue'
import UiSelect from './UiSelect.vue'
import UiIcon from './UiIcon.vue'
import SettingsSection from './SettingsSection.vue'
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
  <SettingsSection title="桌面运行" description="设置关闭窗口后的运行方式。">
    <label class="check-label settings-check"><input type="checkbox" :checked="desktopStatus?.background" :disabled="busy" @change="background" />关闭窗口后驻留托盘并定期刷新</label>
    <p class="field-hint">默认关闭窗口即退出。后台刷新会保留最近成功的额度快照。</p><p v-if="desktopStatus?.last_refresh" class="field-hint">最近后台尝试 {{ timeText(desktopStatus.last_refresh) }}</p><p v-if="desktopStatus?.refresh_error" class="notice">最近刷新失败，已保留上次成功数据。</p>
  </SettingsSection>
  <SettingsSection v-if="!connectionId" title="加密归档" description="导出账号、标签和额度快照，或从归档恢复。">
    <form class="form-stack settings-form" @submit.prevent="transfer">
      <label class="archive-operation">操作<UiSelect v-model="action" aria-label="归档操作" :disabled="busy" :options="[{ value: 'export', label: '导出加密归档' }, { value: 'import', label: '导入到空的个人空间' }]" /></label><p v-if="action === 'import'" class="notice">仅支持导入到没有账号的个人空间；不会合并或覆盖已有账号。</p>
      <div class="settings-form-grid"><label>归档口令<input v-model="password" :disabled="busy" type="password" minlength="12" maxlength="256" required autocomplete="off" placeholder="至少 12 个字符" /></label><label v-if="action === 'export'">再次输入口令<input v-model="confirmation" :disabled="busy" type="password" minlength="12" maxlength="256" required autocomplete="off" placeholder="再次输入归档口令" /></label></div>
      <p class="field-hint">归档包含本机密钥恢复材料，请妥善保存口令。导出时请使用新文件名。</p><div class="settings-form-actions"><button class="primary" :disabled="busy">{{ busy ? '处理中…' : action === 'export' ? '选择位置并导出' : '选择归档并导入' }}</button></div>
    </form><p v-if="result" role="status" class="notice">{{ result }}</p>
  </SettingsSection>
  <SettingsSection title="Cursor 切换与备份" description="查看切换记录，恢复之前的 Cursor 登录状态。" :count="backups.length">
    <div class="settings-list-toolbar"><button class="subtle-button" @click="folder">打开备份目录<UiIcon name="external" :size="13" /></button><button class="subtle-button" @click="loadBackups"><UiIcon name="refresh" :size="14" />重载备份</button></div><p class="field-hint">备份包含 Cursor 登录数据，请勿分享。恢复前会再备份当前状态。</p><button v-if="desktopStatus?.switch?.stage !== 'idle'" class="subtle-button" @click="showProgress = true">查看最近操作</button>
    <div class="settings-list"><div v-for="backup in backups" :key="backup.id" class="setting-row"><div class="setting-identity"><span class="settings-avatar neutral"><UiIcon name="clock" :size="18" /></span><div><strong>Cursor 数据备份</strong><small>{{ timeText(backup.created_at) }}</small></div></div><button :disabled="desktopStatus?.switch?.busy" @click="restoreId = backup.id">恢复此备份</button></div><p v-if="!backups.length" class="settings-empty">尚无切换备份。</p></div>
  </SettingsSection>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
  <NativeSwitchDialog v-if="restoreId || showProgress" :restore-id="restoreId || undefined" @close="closeProgress" />
</template>
