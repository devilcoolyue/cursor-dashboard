import { reactive, watch } from 'vue'
import { me, workspaceId } from './state'
import { connectionId } from './platform'

// Keep navigation while changing pages, but never carry account metadata across scopes.
export const sidebarAccounts = reactive({ tags: {} as Record<string, number>, tag: '', total: 0, loadedAt: 0 })
export function clearSidebarAccounts() {
  sidebarAccounts.tags = {}; sidebarAccounts.tag = ''; sidebarAccounts.total = 0; sidebarAccounts.loadedAt = 0
}
watch([() => me.value?.id, workspaceId, connectionId], clearSidebarAccounts, { flush: 'sync' })
