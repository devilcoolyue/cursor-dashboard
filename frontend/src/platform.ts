import { invoke, isTauri } from '@tauri-apps/api/core'

export const isDesktop = isTauri()
export interface NativeResponse<T = unknown> { status: number; body: T }
export interface SwitchState { stage: string; busy: boolean; backup_id: string | null; error: string | null; written?: boolean }
export interface DesktopStatus {
  phase: 'starting' | 'ready' | 'locked' | 'in_use' | 'unavailable'
  error?: string | null; background: boolean; api_version?: number
  last_refresh?: number | null; refresh_error?: string | null; switch?: SwitchState
}
export interface Detection { platform: string; available: boolean; running: boolean; reason: string | null }
export class DesktopError extends Error {}
export function desktopMessage(status: number) {
  return ({ 409: '操作未完成。请检查目标状态；导入需要空的个人空间，导出请使用新的文件名。',
    423: '本地密钥不可用或归档口令不正确，请解锁系统凭证库或使用正确的加密归档恢复。',
    422: '请检查填写的内容。' } as Record<number, string>)[status] || '本地操作失败，请重试。'
}
async function checked<T>(command: string, args: Record<string, unknown>): Promise<T> {
  if (!isDesktop) throw new DesktopError('请在 Cursor Panel 桌面应用中执行。')
  let result: NativeResponse<T>
  try { result = await invoke<NativeResponse<T>>(command, args) }
  catch { throw new DesktopError('本地后台连接中断，请重新打开 Cursor Panel。') }
  if (result.status >= 400) throw new DesktopError(desktopMessage(result.status))
  return result.body
}
export const native = <T>(operation: 'status' | 'unlock' | 'detect' | 'switch' | 'switch_status' | 'backups' | 'restore' | 'background' | 'resume', body?: unknown) =>
  checked<T>('desktop_request', { operation, body })
export const archive = (operation: 'export' | 'import' | 'recover', password: string, workspace?: string) =>
  checked<{ count?: number; cancelled?: boolean; phase?: string }>('desktop_archive', { operation, password, workspace })
export async function openBackups() { await invoke('desktop_open_backups') }

/** Translate shared business calls into an enum, UUIDs and fields. Rust builds the final URL. */
export async function localRequest(path: string, method: string, body?: unknown): Promise<Response> {
  const url = new URL(path, 'http://local.invalid')
  const parts = url.pathname.split('/').filter(Boolean)
  let operation = '', workspace: string | undefined, account: string | undefined
  if (parts.length === 1 && method === 'GET' && ['bootstrap', 'me'].includes(parts[0]!)) operation = parts[0]!
  else if (parts[0] === 'workspaces' && parts[1]) {
    workspace = parts[1]
    if (parts[2] === 'audit' && parts.length === 3 && method === 'GET') operation = 'audit'
    else if (parts[2] === 'accounts') {
      account = parts[3]
      if (parts.length === 3) operation = ({ GET: 'list', POST: 'add' } as Record<string, string>)[method] || ''
      if (parts.length === 4) operation = ({ GET: 'get', PATCH: 'edit', DELETE: 'delete' } as Record<string, string>)[method] || ''
      if (parts.length === 5) operation = ({ 'POST:authorization': 'reauthorize', 'POST:refresh': 'refresh', 'GET:detail': 'detail' } as Record<string, string>)[`${method}:${parts[4]}`] || ''
    }
  }
  if (!operation) throw new DesktopError('此功能暂不适用于独立桌面。')
  const query = Object.fromEntries([...url.searchParams].map(([key, value]) => [key, ['limit', 'offset'].includes(key) ? Number(value) : value]))
  const result = await invoke<NativeResponse>('account_request', { operation, workspace, account, query, body })
  return new Response(result.status === 204 ? null : JSON.stringify(result.body), { status: result.status, headers: { 'Content-Type': 'application/json' } })
}

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
