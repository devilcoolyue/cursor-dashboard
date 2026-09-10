import { computed, ref } from 'vue'
import { api, ApiError, message, type Me, type Schema } from './api'
import { isDesktop, native, connectionId, connectionAction, type DesktopStatus, type ConnectionsState } from './platform'

export const me = ref<Me>()
export const bootstrap = ref<Schema['Bootstrap']>()
export const workspaceId = ref('')
export const activeSpace = computed(() => me.value?.workspaces.find(w => w.id === workspaceId.value))
export const ready = ref(false)
export const startupError = ref('')
export const invitationToken = ref('')
export const desktopStatus = ref<DesktopStatus>()
export const connections = ref<ConnectionsState>({ active_id: null, generation: 0, items: [] })
export const activeConnection = computed(() => connections.value.items.find(c => c.id === connectionId.value))
export const connectionNotice = ref('')
export const deviceAuthorization = ref<{ code_challenge: string; state: string; callback: string; device_id: string; device_name: string }>()
export function clearIdentity() {
  api.invalidate()
  api.csrf = ''
  me.value = undefined
  workspaceId.value = ''
  invitationToken.value = ''
}
api.onUnauthorized = clearIdentity
export function selectSpace(id: string) {
  if (id === workspaceId.value) return
  api.invalidate()
  workspaceId.value = id
}
export async function loadMe(preferred?: string) {
  const result = await api.request<Me>('/me')
  me.value = result
  const next = preferred || workspaceId.value
  selectSpace(result.workspaces.find(w => w.id === next)?.id || result.workspaces.find(w => w.kind === 'personal')?.id || result.workspaces[0]?.id || '')
}
export async function initialize() {
  startupError.value = ''
  ready.value = false
  try {
    if (isDesktop) {
      desktopStatus.value = await native<DesktopStatus>('status')
      if (desktopStatus.value.phase !== 'ready') return
      connections.value = await connectionAction('list')
    }
    const result = await api.request<Schema['Bootstrap']>('/bootstrap')
    if (result.api_version !== 1 || result.mode !== (isDesktop && !connectionId.value ? 'local' : 'server')) throw new ApiError(409, '服务 API 版本不兼容，请升级界面与服务。')
    bootstrap.value = result
    if (isDesktop) { await loadMe(); return }
    try {
      api.csrf = (await api.request<Schema['Csrf']>('/auth/csrf')).csrf_token
      await loadMe()
    } catch (error) { if (!(error instanceof ApiError && error.status === 401)) throw error }
  } catch (error) { startupError.value = message(error) }
  finally { ready.value = true }
}

export async function selectConnection(id: string | null) {
  connections.value = await connectionAction('select', undefined, { connection_id: id })
  clearIdentity()
  bootstrap.value = undefined
  connectionId.value = id
  await initialize()
}
