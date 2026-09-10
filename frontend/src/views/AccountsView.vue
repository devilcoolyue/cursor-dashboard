<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { api, ApiError, message, isAbort, accountPath, spacePath, type Account, type Schema } from '../api'
import { activeSpace, bootstrap } from '../state'
import { timeText } from '../format'
import QuotaBar from '../components/QuotaBar.vue'
import UiDialog from '../components/UiDialog.vue'
import AccountForm from '../components/AccountForm.vue'
import DetailDialog from '../components/DetailDialog.vue'
import SwitchDialog from '../components/SwitchDialog.vue'
import GrantDialog from '../components/GrantDialog.vue'
const page = ref<Schema['AccountPage']>(), query = ref(''), tag = ref(''), offset = ref(0), busy = ref(false), error = ref('')
const rowBusy = ref(''), selected = ref<Account>(), modal = ref(''), removeBusy = ref(false), modalError = ref('')
let listController: AbortController | undefined
const lifetime = new AbortController()
let sequence = 0
onBeforeUnmount(() => { listController?.abort(); lifetime.abort(); page.value = undefined; selected.value = undefined })
async function load() {
  if (!activeSpace.value) return
  listController?.abort(); listController = new AbortController()
  const current = ++sequence
  busy.value = true; error.value = ''
  const params = new URLSearchParams({ q: query.value, offset: String(offset.value), limit: '25' })
  if (tag.value) params.set('tag', tag.value)
  try { page.value = await api.request<Schema['AccountPage']>(spacePath(activeSpace.value.id) + '/accounts?' + params, 'GET', undefined, listController.signal) }
  catch (reason) { if (!isAbort(reason)) { error.value = message(reason); if (reason instanceof ApiError && [401, 403, 404].includes(reason.status)) page.value = undefined } }
  finally { if (sequence === current) busy.value = false }
}
watch([query, tag], () => { page.value = undefined; offset.value = 0; void load() })
watch(offset, () => void load())
watch(() => activeSpace.value?.id, () => { page.value = undefined; selected.value = undefined; modal.value = ''; query.value = ''; tag.value = ''; offset.value = 0; void load() }, { immediate: true })
function open(kind: string, account?: Account) {
  const menu = document.activeElement?.closest('details.account-menu') as HTMLDetailsElement | null
  if (menu) { menu.open = false; menu.querySelector('summary')?.focus() }
  selected.value = account; modal.value = kind; modalError.value = ''
}
function close() { modal.value = ''; selected.value = undefined; modalError.value = '' }
async function saved() { close(); await load() }
async function refresh(account: Account) {
  rowBusy.value = account.id; error.value = ''
  try { await api.request<Account>(accountPath(account) + '/refresh', 'POST', undefined, lifetime.signal); await load() }
  catch (reason) { error.value = message(reason) }
  finally { rowBusy.value = '' }
}
async function remove() {
  if (!selected.value) return
  removeBusy.value = true; modalError.value = ''
  try { await api.request(accountPath(selected.value), 'DELETE', undefined, lifetime.signal); close(); await load() }
  catch (reason) { modalError.value = message(reason) }
  finally { removeBusy.value = false }
}
const statusText = (a: Account) => a.auth_invalid || a.expired ? '需要重新授权' : a.stale ? '刷新失败 · 保留上次数据' : a.pending ? '等待首次刷新' : a.error_kind ? '暂时无法查询' : '授权有效'
</script>
<template>
  <section v-if="activeSpace" class="workspace-page">
    <header class="page-heading"><div><p class="eyebrow">{{ activeSpace.kind === 'personal' ? '个人空间' : '团队空间' }}</p><h1>账号与额度</h1><p class="muted">{{ activeSpace.kind === 'personal' ? '仅你可见的 Cursor 账号。' : '这里只展示你获授权的账号。' }} 列表展示最后成功快照。</p></div><button v-if="activeSpace.capabilities.manage_accounts" class="primary" @click="open('add')">＋ 添加账号</button></header>
    <div class="overview" aria-label="当前筛选统计"><div><strong>{{ page?.total ?? '—' }}</strong><span>个可见账号</span></div><div><strong>{{ page?.stats.with_snapshot ?? '—' }}</strong><span>个已有快照</span></div><div><strong>{{ page?.stats.invalid ?? '—' }}</strong><span>个授权失效</span></div></div>
    <div class="filter-bar"><label class="search-field"><span class="sr-only">搜索账号</span><input v-model="query" type="search" placeholder="搜索名称或邮箱…" maxlength="256" /></label><label><span class="sr-only">筛选标签</span><select v-model="tag"><option value="">全部标签</option><option v-if="tag && !page?.tags[tag]" :value="tag">{{ tag }}</option><option v-for="(count, name) in page?.tags" :key="name" :value="name">{{ name }} · {{ count }}</option></select></label><button :disabled="busy" @click="load">{{ busy ? '载入中…' : '重载列表' }}</button></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="busy && !page" class="empty-state" role="status">正在载入账号…</p>
    <div v-else-if="page && !page.total" class="empty-state"><span class="empty-symbol" aria-hidden="true">＋</span><h2>{{ query || tag ? '没有匹配的账号' : '这里还没有可见账号' }}</h2><p>{{ query || tag ? '试试其他关键词或标签。' : activeSpace.capabilities.manage_accounts ? '添加第一个 Cursor 账号，开始查看额度。' : '请联系空间管理员，为你分配账号权限。' }}</p></div>
    <div v-if="page?.items.length" class="account-list" :aria-busy="busy">
      <div class="list-heading"><span>账号 / 授权</span><span>综合池剩余</span><span>Other Models 剩余</span><span>操作</span></div>
      <article v-for="account in page.items" :key="account.id" class="account-row" data-account>
        <div class="account-identity"><div class="account-title"><span class="avatar" aria-hidden="true">{{ account.label.slice(0, 1).toUpperCase() }}</span><div><h2>{{ account.label }}</h2><span class="muted account-email">{{ account.email || '邮箱未知' }}</span></div></div>
          <div class="account-meta"><span :class="['status-dot', { invalid: account.auth_invalid || account.expired, stale: account.stale }]" aria-hidden="true" />{{ statusText(account) }}<span v-for="name in account.tags" :key="name" class="tag">{{ name }}</span></div><small>快照 {{ timeText(account.ok_at) }} · {{ account.data?.plan?.name || '套餐未知' }}</small>
        </div>
        <QuotaBar name="综合池" :slot="account.data?.quota?.overall" /><QuotaBar name="Other Models" :slot="account.data?.quota?.other_models" />
        <div class="row-actions"><button v-if="account.capabilities.detail" @click="open('detail', account)">明细</button><button v-if="account.capabilities.refresh" :disabled="!!rowBusy" @click="refresh(account)">{{ rowBusy === account.id ? '刷新中…' : '刷新' }}</button><button v-if="account.capabilities.switch && bootstrap?.capabilities.manual_switch" @click="open('switch', account)">切换</button>
          <details v-if="account.capabilities.edit || account.capabilities.authorize || account.capabilities.delete" class="account-menu"><summary aria-label="更多账号操作">···</summary><div class="menu-items"><button v-if="account.capabilities.edit" @click="open('edit', account)">编辑资料与标签</button><button v-if="account.capabilities.authorize" @click="open('authorize', account)">重新授权</button><button v-if="account.capabilities.grant && activeSpace.capabilities.manage_members" @click="open('grant', account)">账号授权</button><button v-if="account.capabilities.delete" class="danger-text" @click="open('delete', account)">删除账号</button></div></details>
        </div>
      </article>
    </div>
    <footer v-if="page" class="pagination"><span>{{ page.total }} 个账号 · 每页 25 个</span><div class="actions"><button :disabled="!offset || busy" @click="offset = Math.max(0, offset - 25)">上一页</button><button :disabled="offset + 25 >= page.total || busy" @click="offset += 25">下一页</button></div></footer>
    <AccountForm v-if="['add', 'edit', 'authorize'].includes(modal)" :workspace-id="activeSpace.id" :account="selected" :authorize="modal === 'authorize'" @close="close" @saved="saved" />
    <DetailDialog v-if="modal === 'detail' && selected" :account="selected" @close="close" />
    <SwitchDialog v-if="modal === 'switch' && selected" :account="selected" @close="close" />
    <GrantDialog v-if="modal === 'grant' && selected" :account="selected" @close="close" />
    <UiDialog v-if="modal === 'delete' && selected" title="删除账号" @close="close"><p>确认从此空间删除 {{ selected.label }}？凭证、授权与额度快照会一并删除。</p><p v-if="modalError" role="alert" class="error">{{ modalError }}</p><div class="actions"><button @click="close">取消</button><button class="danger" :disabled="removeBusy" @click="remove">确认删除</button></div></UiDialog>
  </section>
</template>
