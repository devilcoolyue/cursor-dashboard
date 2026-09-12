import { Channel, invoke } from '@tauri-apps/api/core'
import { ref } from 'vue'
import { api, message, type Schema } from './api'
import { isDesktop } from './platform'
import { appVersion } from './version'

export type ReleaseInfo = Schema['ReleaseInfo']
export type UpdateStatus = Schema['UpdateStatus']
export const release = ref<ReleaseInfo>()
export const checkingUpdate = ref(false)
export const installingUpdate = ref(false)
export const updateError = ref('')
export const downloadProgress = ref<{ stage: string; downloaded: number; total?: number | null }>()
export const lastUpdateCheck = ref<number | null>(null)
export const automaticCheckHours = 4
const checkInterval = automaticCheckHours * 60 * 60 * 1000
const cacheKey = `cursor.v2.updates.${isDesktop ? 'desktop' : 'web'}`
let lastAttempt = 0, restoredRelease = false, automaticChecks = false
let timer: ReturnType<typeof setTimeout> | undefined
let pendingCheck: Promise<void> | undefined

function saveCheck() {
  try { localStorage.setItem(cacheKey, JSON.stringify({ version: appVersion, attemptedAt: lastAttempt, checkedAt: lastUpdateCheck.value, release: release.value })) }
  catch { /* Checking still works when local storage is unavailable. */ }
}
try {
  const cached = JSON.parse(localStorage.getItem(cacheKey) || 'null')
  const now = Date.now()
  if (cached?.version === appVersion && Number.isFinite(cached.attemptedAt) && cached.attemptedAt > 0 && cached.attemptedAt <= now) {
    lastAttempt = cached.attemptedAt
    const value = cached.release
    if (Number.isFinite(cached.checkedAt) && cached.checkedAt > 0 && cached.checkedAt <= now
      && value && /^\d+\.\d+\.\d+$/.test(value.current_version)
      && (value.latest_version === null || /^\d+\.\d+\.\d+$/.test(value.latest_version))
      && typeof value.available === 'boolean' && typeof value.installable === 'boolean'
      && typeof value.notes === 'string' && value.notes.length <= 12000) {
      release.value = value
      lastUpdateCheck.value = cached.checkedAt
      restoredRelease = true
    }
  }
} catch { /* Ignore expired or damaged metadata. */ }

function scheduleCheck() {
  clearTimeout(timer)
  if (!automaticChecks || document.visibilityState === 'hidden' || !navigator.onLine) return
  timer = setTimeout(() => {
    if (!automaticChecks || document.visibilityState === 'hidden' || !navigator.onLine) return
    if (installingUpdate.value) { scheduleCheckAfterInstall(); return }
    void checkUpdate()
  }, Math.max(0, lastAttempt + checkInterval - Date.now()))
}
function scheduleCheckAfterInstall() { timer = setTimeout(scheduleCheck, 60000) }
export function startAutomaticUpdateChecks() {
  automaticChecks = true
  document.addEventListener('visibilitychange', scheduleCheck)
  window.addEventListener('online', scheduleCheck)
  window.addEventListener('offline', scheduleCheck)
  scheduleCheck()
  return () => {
    automaticChecks = false
    clearTimeout(timer)
    document.removeEventListener('visibilitychange', scheduleCheck)
    window.removeEventListener('online', scheduleCheck)
    window.removeEventListener('offline', scheduleCheck)
  }
}

function updateMessage(error: unknown) {
  return isDesktop && typeof error === 'string' ? error : message(error)
}
export async function openReleases(event: MouseEvent) {
  if (!isDesktop) return
  event.preventDefault()
  try { await invoke('open_releases') }
  catch (error) { updateError.value = updateMessage(error) }
}
export function checkUpdate(): Promise<void> {
  if (pendingCheck) return pendingCheck
  if (installingUpdate.value) return Promise.resolve()
  checkingUpdate.value = true
  updateError.value = ''
  lastAttempt = Date.now()
  saveCheck()
  pendingCheck = (async () => {
    try {
      release.value = isDesktop ? await invoke<ReleaseInfo>('check_update') : await api.request<ReleaseInfo>('/updates')
      lastUpdateCheck.value = Date.now()
      restoredRelease = false
      saveCheck()
    } catch (error) { updateError.value = updateMessage(error) }
    finally { checkingUpdate.value = false; pendingCheck = undefined; scheduleCheck() }
  })()
  return pendingCheck
}
export async function installDesktopUpdate() {
  if (!isDesktop || installingUpdate.value) return
  if (restoredRelease || updateError.value) {
    await checkUpdate()
    if (restoredRelease || updateError.value) return
  }
  const current = release.value
  if (!current?.available || !current.installable || !current.latest_version) return
  installingUpdate.value = true
  updateError.value = ''
  const progress = new Channel<{ stage: string; downloaded: number; total?: number | null }>()
  progress.onmessage = value => { downloadProgress.value = value }
  try { await invoke('install_update', { version: current.latest_version, progress }) }
  catch (error) { updateError.value = updateMessage(error) }
  finally { installingUpdate.value = false; downloadProgress.value = undefined }
}
