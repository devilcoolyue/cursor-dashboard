<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import UiDialog from './UiDialog.vue'
import UiIcon from './UiIcon.vue'
import QuotaBar from './QuotaBar.vue'
import { accountPath, api, isAbort, message, type Account, type Schema } from '../api'
import { money, timeText } from '../format'
import { percent } from '../quota'
const props = defineProps<{ account: Account; initialGroup?: string }>()
const emit = defineEmits<{ close: [] }>()
const detail = ref<Schema['DetailView']>(), error = ref(''), busy = ref(true)
const selectedGroup = ref(props.initialGroup || 'overall')
const categories = [{ key: 'overall', name: '综合', label: '全部模型' }, { key: 'cursor_models', name: 'Cursor Models', label: 'Cursor Models' }, { key: 'other_models', name: 'Other Models', label: 'Other Models' }]
const groups = computed(() => detail.value?.groups.filter(group => selectedGroup.value === 'overall' || group.key === selectedGroup.value) || [])
const totals = computed(() => selectedGroup.value === 'overall' && detail.value ? { models: detail.value.totals.model_count, tokens: detail.value.totals.total_tokens, spend: detail.value.totals.spend_usd } : groups.value.reduce((total, group) => ({ models: total.models + group.models.length, tokens: total.tokens + group.total_tokens, spend: total.spend + group.spend_usd }), { models: 0, tokens: 0, spend: 0 }))
const controller = new AbortController()
onBeforeUnmount(() => controller.abort())
async function load() {
  busy.value = true; error.value = ''
  try { detail.value = await api.request<Schema['DetailView']>(accountPath(props.account) + '/detail', 'GET', undefined, controller.signal) }
  catch (reason) { if (!isAbort(reason)) error.value = message(reason) }
  finally { busy.value = false }
}
onMounted(load)
const tokens = (value?: number) => (value || 0).toLocaleString('zh-CN')
</script>
<template>
  <UiDialog :title="`${account.label} · 额度明细`" class="account-detail-dialog" wide dismiss-backdrop @close="emit('close')">
    <template #subtitle><span>{{ account.email || '邮箱未知' }}</span><span class="plan">{{ account.data?.plan?.name || '套餐未知' }}</span></template>
    <div class="detail-overview">
      <div class="detail-cycle"><span><UiIcon name="clock" :size="13" />本账期</span><span>{{ timeText(detail?.cycle_start || account.data?.cycle?.start) }} <span class="muted">→</span> {{ timeText(account.data?.cycle?.reset_at) }}</span></div>
      <div class="detail-quota-tabs" role="group" aria-label="模型分类">
        <button v-for="category in categories" :key="category.key" type="button" :aria-label="`查看${category.label}`" :aria-pressed="selectedGroup === category.key" @click="selectedGroup = category.key"><QuotaBar :name="category.name" :slot="account.data?.quota?.[category.key]" /></button>
      </div>
      <details class="detail-account-info">
        <summary><UiIcon name="chevron" :size="13" /><span>账号与账单信息</span><span v-if="account.data?.spend_usd?.total != null" class="detail-spend">本期消费 <strong>{{ money(account.data.spend_usd.total) }}</strong></span></summary>
        <dl class="detail-metadata">
          <div><dt>最近快照</dt><dd>{{ timeText(account.ok_at) }}</dd></div>
          <div><dt>凭证有效至</dt><dd>{{ timeText(account.expires_at) }}</dd></div>
          <div><dt>最近续期</dt><dd>{{ timeText(account.refreshed_at) }}</dd></div>
          <div v-if="account.tags.length"><dt>账号标签</dt><dd>{{ account.tags.join(' · ') }}</dd></div>
          <div v-if="account.data?.spend_usd?.total != null"><dt>本期参考消费</dt><dd>{{ money(account.data.spend_usd.total) }}</dd></div>
          <div v-if="account.data?.on_demand"><dt>按量付费</dt><dd>{{ account.data.on_demand.enabled ? '已启用' : '未启用' }}<template v-if="account.data.on_demand.used_usd != null"> · 已用 {{ money(account.data.on_demand.used_usd) }}</template><template v-if="account.data.on_demand.limit_usd != null"> / {{ money(account.data.on_demand.limit_usd) }}</template></dd></div>
          <div v-if="account.data?.grok_weekly"><dt>Grok 周额度</dt><dd>剩 {{ account.data.grok_weekly.remaining_pct == null ? '—' : percent(account.data.grok_weekly.remaining_pct) }} · {{ timeText(account.data.grok_weekly.reset_at) }} 重置</dd></div>
        </dl>
      </details>
      <p v-if="account.stale || account.expired || account.auth_invalid" class="notice">{{ account.auth_invalid || account.expired ? '授权不可用，请联系管理员重新授权。' : '最近查询失败，额度保留自最后成功快照。' }}</p>
      <p v-if="account.data?.notice" class="notice">{{ account.data.notice }}</p>
    </div>
    <div class="detail-models" :aria-busy="busy">
      <div v-if="busy" class="detail-loading" role="status"><span class="sr-only">正在查询模型用量…</span><div v-for="row in 5" :key="row" class="detail-skeleton-row" aria-hidden="true"><i /><i /><i /><i /></div></div>
      <div v-else-if="error" class="detail-error"><p role="alert" class="error">{{ error }}</p><button type="button" @click="load"><UiIcon name="refresh" :size="14" />重新查询</button></div>
      <template v-else-if="detail">
        <section v-for="group in groups" :key="group.key" class="detail-group">
          <header class="detail-group-head"><h3>{{ group.name }}</h3><span>已用 <strong>{{ money(group.spend_usd) }}</strong><template v-if="account.data?.quota?.[group.key]?.limit_usd != null"> / 上限 {{ money(account.data.quota[group.key]!.limit_usd!) }}</template></span></header>
          <div v-if="group.models.length" class="detail-table-scroll" role="region" :aria-label="`${group.name}模型用量`" tabindex="0">
            <table class="detail-table"><caption class="sr-only">{{ group.name }} 本账期模型用量，输入、输出及缓存单位为 Token</caption><thead><tr><th scope="col">模型</th><th scope="col">输入</th><th scope="col">输出</th><th scope="col">缓存写入</th><th scope="col">缓存读取</th><th scope="col">花费</th></tr></thead>
              <tbody><tr v-for="model in group.models" :key="model.model"><th scope="row">{{ model.model }}</th><td>{{ tokens(model.input_tokens) }}</td><td>{{ tokens(model.output_tokens) }}</td><td :class="{ zero: !model.cache_write_tokens }">{{ tokens(model.cache_write_tokens) }}</td><td :class="{ zero: !model.cache_read_tokens }">{{ tokens(model.cache_read_tokens) }}</td><td class="model-cost">{{ money(model.spend_cents / 100) }}</td></tr></tbody>
            </table>
          </div>
          <p v-else class="detail-empty">本账期还没有这类模型的用量。</p>
        </section>
        <p v-if="!groups.length" class="detail-empty">本账期暂无模型用量。</p>
        <div class="detail-total"><span>{{ totals.models }} 个模型 · {{ tokens(totals.tokens) }} tokens</span><span>合计 <strong>{{ money(totals.spend) }}</strong></span></div>
      </template>
    </div>
    <template #footer><div class="detail-footnote"><p>额度百分比来自 Cursor，美元上限为推算值。<a href="https://cursor.com/docs/models-and-pricing" target="_blank" rel="noopener noreferrer">模型定价 ↗</a></p><span>{{ detail ? `明细更新于 ${timeText(detail.fetched_at)}` : '按本账期统计模型用量' }}</span></div><button type="button" @click="emit('close')">关闭</button></template>
  </UiDialog>
</template>
