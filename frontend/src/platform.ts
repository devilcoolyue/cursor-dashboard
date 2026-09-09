import { invoke, isTauri } from '@tauri-apps/api/core'

export interface Probe {
  backend: { api_version: number; frozen: boolean; python: string; sqlite: string; pid: number }
  accounts: { id: string; label: string; email: string; remaining_pct: number }[]
  fixture: string
  keyring: { ok: boolean; detail: string }
  backend_start_ms: number
}

export async function probe(): Promise<Probe> {
  if (!isTauri()) throw new Error('请使用桌面验证程序运行；浏览器预览不连接本机后台。')
  return invoke<Probe>('probe')
}

export async function reportReady(renderedAccounts: number): Promise<void> {
  if (isTauri()) await invoke('frontend_ready', { renderedAccounts, title: document.title })
}
