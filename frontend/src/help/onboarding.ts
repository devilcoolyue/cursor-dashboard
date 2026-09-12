import { reactive, ref } from 'vue'

export const guideSteps = [
  { title: '认识客户端与服务端', caption: '先确认账号保存在何处', icon: 'monitor', doc: 'overview' },
  { title: '完成客户端设置', caption: '外观、后台运行与备份', icon: 'sliders', doc: 'desktop-setup' },
  { title: '添加账号，查看额度', caption: '授权、快照与用量明细', icon: 'grid', doc: 'accounts' },
  { title: '切换本机 Cursor', caption: '保存工作，确认后切换', icon: 'switch', doc: 'switching' },
  { title: '连接 Linux 实例', caption: '添加地址，在浏览器授权', icon: 'server', doc: 'connections' },
  { title: '设置空间与权限', caption: '邀请成员，逐账号授权', icon: 'shield', doc: 'permissions' },
] as const

const storageKey = 'cursor.v2.onboarding'
interface Progress { seen: boolean; active: boolean; step: number }
function read(): Progress {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || 'null')
    if (value?.version === 1 && typeof value.seen === 'boolean' && typeof value.active === 'boolean'
      && Number.isInteger(value.step) && value.step >= 0 && value.step < guideSteps.length) {
      return { seen: value.seen, active: value.active, step: value.step }
    }
  } catch { /* An unavailable or old preference must never prevent opening the app. */ }
  return { seen: false, active: false, step: 0 }
}
export const guideProgress = reactive(read())
export const guideOpen = ref(false)
function save() {
  try { localStorage.setItem(storageKey, JSON.stringify({ version: 1, ...guideProgress })) } catch { /* Keep progress in memory. */ }
}
export function openGuide() { guideProgress.seen = true; guideProgress.active = true; guideOpen.value = true; save() }
export function selectGuideStep(step: number) {
  guideProgress.step = Math.max(0, Math.min(guideSteps.length - 1, Math.trunc(step) || 0)); save()
}
export function pauseGuide() { guideOpen.value = false; save() }
export function dismissGuide() { guideProgress.active = false; guideProgress.seen = true; guideOpen.value = false; save() }
