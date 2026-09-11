<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { api, ApiError, message, isAbort, accountPath, spacePath, type Account, type Schema } from '../api'
import { activeSpace, bootstrap } from '../state'
import { sidebarAccounts, clearSidebarAccounts } from '../sidebar-state'
import AccountCard from '../components/AccountCard.vue'
import AccountActions from '../components/AccountActions.vue'
import UiIcon from '../components/UiIcon.vue'
import UiSelect from '../components/UiSelect.vue'
import UiDialog from '../components/UiDialog.vue'
import AccountForm from '../components/AccountForm.vue'
import DetailDialog from '../components/DetailDialog.vue'
import SwitchDialog from '../components/SwitchDialog.vue'
import GrantDialog from '../components/GrantDialog.vue'
import NativeSwitchDialog from '../components/NativeSwitchDialog.vue'
import { isDesktop, reportReady } from '../platform'
const page = ref<Schema['AccountPage']>(), query = ref(''), busy = ref(false), error = ref('')
const tag = computed({ get: () => sidebarAccounts.tag, set: value => { sidebarAccounts.tag = value } })
const rowBusy = ref(''), selected = ref<Account>(), modal = ref(''), removeBusy = ref(false), modalError = ref('')
const refreshErrors = ref<Record<string, string>>({})
let listController: AbortController | undefined
const lifetime = new AbortController()
const detailGroup = ref('overall')
const searchInput = ref<HTMLInputElement>(), order = ref('default'), now = ref(Date.now())
let clockTimer: ReturnType<typeof setInterval> | undefined
const sortOptions = [{ value: 'default', label: '默认顺序' }, { value: 'name', label: '名称顺序' }, { value: 'remaining', label: '剩余额度从低到高' }]
const sortedAccounts = computed(() => {
  const items = [...(page.value?.items || [])]
  if (order.value === 'name') items.sort((a, b) => a.label.localeCompare(b.label, 'zh-CN', { numeric: true }))
  if (order.value === 'remaining') items.sort((a, b) => (a.data?.quota?.overall?.remaining_pct ?? Infinity) - (b.data?.quota?.overall?.remaining_pct ?? Infinity))
  return items
})
function searchShortcut(event: KeyboardEvent) {
  if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || document.querySelector('dialog[open]')) return
  if (event.target instanceof Element && event.target.closest('input, textarea, [role=combobox], [contenteditable]')) return
  event.preventDefault(); searchInput.value?.focus()
}
onMounted(() => { window.addEventListener('keydown', searchShortcut); clockTimer = setInterval(() => { now.value = Date.now() }, 60000) })
onBeforeUnmount(() => { window.removeEventListener('keydown', searchShortcut); clearInterval(clockTimer) })
let sequence = 0
onBeforeUnmount(() => { listController?.abort(); lifetime.abort(); page.value = undefined; selected.value = undefined })
async function load() {
  if (!activeSpace.value) return
  listController?.abort(); listController = new AbortController()
  const current = ++sequence, signal = listController.signal
  busy.value = true; error.value = ''
  const params = new URLSearchParams({ q: query.value, offset: '0', limit: '200' })
  if (tag.value) params.set('tag', tag.value)
  try {
    const path = spacePath(activeSpace.value.id) + '/accounts?'
    const request = api.request<Schema['AccountPage']>(path + params, 'GET', undefined, signal)
    // The API returns facets of its filtered result. Keep the navigation scoped to all visible accounts.
    const [result, catalog] = await Promise.all([request, query.value || tag.value
      ? api.request<Schema['AccountPage']>(path + 'limit=1', 'GET', undefined, signal) : request])
    if (current !== sequence || lifetime.signal.aborted) return
    // Fetch every API batch before publishing the list so search and sorting cover all accounts.
    const accounts = new Map(result.items.map(account => [account.id, account]))
    let nextOffset = result.items.length, total = result.total
    while (nextOffset < total) {
      params.set('offset', String(nextOffset))
      const batch = await api.request<Schema['AccountPage']>(path + params, 'GET', undefined, signal)
      if (current !== sequence || lifetime.signal.aborted) return
      if (!batch.items.length) break
      for (const account of batch.items) accounts.set(account.id, account)
      nextOffset += batch.items.length
      total = batch.total
    }
    page.value = { ...result, items: [...accounts.values()], total: accounts.size }; now.value = Date.now()
    sidebarAccounts.tags = catalog.tags; sidebarAccounts.total = catalog.total; sidebarAccounts.loadedAt = Date.now()
    if (isDesktop) { await nextTick(); void reportReady(document.querySelectorAll('[data-account]').length).catch(() => {}) }
  }
  catch (reason) { if (sequence === current && !isAbort(reason)) { error.value = message(reason); if (reason instanceof ApiError && [401, 403, 404, 426].includes(reason.status)) { page.value = undefined; clearSidebarAccounts() } } }
  finally { if (sequence === current) busy.value = false }
}
watch([query, tag, () => activeSpace.value?.id], ([, , spaceId], previous) => {
  page.value = undefined
  if (spaceId !== previous?.[2]) { selected.value = undefined; modal.value = ''; query.value = '' }
  void load()
}, { immediate: true })
function open(kind: string, account?: Account) {
  selected.value = account; modal.value = kind; modalError.value = ''
}
function openDetail(account: Account, group = 'overall') { detailGroup.value = group; open('detail', account) }
function clearSearch() { query.value = ''; searchInput.value?.focus() }
function escapeSearch(event: KeyboardEvent) {
  if (event.isComposing) return
  event.preventDefault()
  if (query.value) clearSearch(); else searchInput.value?.blur()
}
function close() { modal.value = ''; selected.value = undefined; modalError.value = '' }
async function saved() { close(); await load() }
async function refresh(account: Account) {
  if (rowBusy.value) return
  delete refreshErrors.value[account.id]
  rowBusy.value = account.id; error.value = ''
  try { await api.request<Account>(accountPath(account) + '/refresh', 'POST', undefined, lifetime.signal); await load() }
  catch (reason) { if (!isAbort(reason) && !lifetime.signal.aborted) refreshErrors.value[account.id] = message(reason) }
  finally { rowBusy.value = '' }
}
async function remove() {
  if (!selected.value) return
  removeBusy.value = true; modalError.value = ''
  try { await api.request(accountPath(selected.value), 'DELETE', undefined, lifetime.signal); close(); await load() }
  catch (reason) { modalError.value = message(reason) }
  finally { removeBusy.value = false }
}
</script>
<template>
  <section v-if="activeSpace" class="accounts-page">
    <header class="topbar accounts-toolbar">
      <div class="workspace-title"><h1>账号与额度</h1><span class="workspace-title-name">{{ activeSpace.kind === 'personal' ? '个人空间' : activeSpace.name }}</span><span class="account-count">{{ page?.total ?? '—' }} 个账号</span></div>
      <div class="toolbar-controls"><div class="search-wrap" :class="{ 'has-query': query }"><div class="search-field"><label class="sr-only" for="account-search">搜索账号</label><UiIcon name="search" /><input id="account-search" ref="searchInput" v-model="query" type="search" placeholder="按姓名或邮箱搜索…" maxlength="256" autocomplete="off" spellcheck="false" @keydown.esc="escapeSearch" /><button v-if="query" type="button" class="search-clear" aria-label="清空搜索" @click="clearSearch"><UiIcon name="close" :size="13" /></button><kbd v-else aria-hidden="true">/</kbd></div></div>
        <label class="sort-field"><span class="sr-only">账号排序</span><UiSelect v-model="order" aria-label="账号排序" icon="sort" :options="sortOptions" title="账号排序" /></label>
        <button type="button" class="toolbar-reload" aria-label="重载列表" title="重载列表" :disabled="busy" @click="load"><UiIcon name="refresh" :class="{ spinning: busy }" :size="15" /></button>
        <button v-if="activeSpace.capabilities.manage_accounts" class="primary" @click="open('add')">＋ 添加账号</button>
      </div>
    </header>
    <div class="accounts-content">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="busy && !page" class="empty-state" role="status">正在载入账号…</p>
    <div v-else-if="page && !page.total" class="empty-state"><span class="empty-symbol" aria-hidden="true">＋</span><h2>{{ query || tag ? '没有匹配的账号' : '这里还没有可见账号' }}</h2><p>{{ query || tag ? '试试其他关键词或标签。' : activeSpace.capabilities.manage_accounts ? '添加第一个 Cursor 账号，开始查看额度。' : '请联系空间管理员，为你分配账号权限。' }}</p></div>
    <div v-if="page?.items.length" class="account-grid" :aria-busy="busy">
      <AccountCard v-for="account in sortedAccounts" :key="account.id" :account="account" :space-name="activeSpace.name" :now="now" :refreshing="rowBusy === account.id" :refresh-error="refreshErrors[account.id]" @detail="openDetail(account, $event)">
        <template #quick-actions><AccountActions :account="account" :busy="!!rowBusy" :refreshing="rowBusy === account.id" :can-switch="!!(account.capabilities.switch && (bootstrap?.capabilities.manual_switch || bootstrap?.capabilities.native_switch))" :can-grant="account.capabilities.grant && activeSpace.capabilities.manage_members" @open="$event === 'detail' ? openDetail(account) : open($event, account)" @refresh="refresh(account)" /></template>
      </AccountCard>
    </div>

    </div>
    <AccountForm v-if="['add', 'edit', 'authorize'].includes(modal)" :workspace-id="activeSpace.id" :account="selected" :authorize="modal === 'authorize'" :tag-catalog="sidebarAccounts.tags" @close="close" @saved="saved" />
    <DetailDialog v-if="modal === 'detail' && selected" :account="selected" :initial-group="detailGroup" @close="close" />
    <SwitchDialog v-if="modal === 'switch' && selected && !isDesktop" :account="selected" @close="close" />
    <NativeSwitchDialog v-if="modal === 'switch' && selected && isDesktop" :account="selected" @close="close" />
    <GrantDialog v-if="modal === 'grant' && selected" :account="selected" @close="close" />
    <UiDialog v-if="modal === 'delete' && selected" title="删除账号" @close="close"><p>确认从此空间删除 {{ selected.label }}？凭证、授权与额度快照会一并删除。</p><p v-if="modalError" role="alert" class="error">{{ modalError }}</p><div class="actions"><button @click="close">取消</button><button class="danger" :disabled="removeBusy" @click="remove">确认删除</button></div></UiDialog>
  </section>
</template>
