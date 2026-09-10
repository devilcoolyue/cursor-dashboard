<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import UiDialog from './UiDialog.vue'
import QuotaBar from './QuotaBar.vue'
import { accountPath, api, message, type Account, type Schema } from '../api'
import { money, timeText } from '../format'
const props = defineProps<{ account: Account }>()
const emit = defineEmits<{ close: [] }>()
const detail = ref<Schema['DetailView']>(), error = ref(''), busy = ref(true)
const controller = new AbortController()
onBeforeUnmount(() => controller.abort())
onMounted(async () => {
  try { detail.value = await api.request<Schema['DetailView']>(accountPath(props.account) + '/detail', 'GET', undefined, controller.signal) }
  catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
})
</script>
<template>
  <UiDialog :title="`${account.label} · 额度明细`" wide @close="emit('close')">
    <p class="muted">{{ account.email }} · {{ account.data?.plan?.name || '套餐未知' }} · 快照 {{ timeText(account.ok_at) }}</p>
    <p class="muted">凭证有效至 {{ timeText(account.expires_at) }} · 最近续期 {{ timeText(account.refreshed_at) }}</p>
    <p v-if="account.stale || account.expired || account.auth_invalid" class="notice">{{ account.auth_invalid || account.expired ? "授权不可用，请联系管理员重新授权。" : "最近查询失败，以下额度保留自最后成功快照。" }}</p>
    <div class="detail-quotas"><QuotaBar name="综合池" :slot="account.data?.quota?.overall" /><QuotaBar name="Cursor Models" :slot="account.data?.quota?.cursor_models" /><QuotaBar name="Other Models" :slot="account.data?.quota?.other_models" /></div>
    <p class="muted">账期 {{ timeText(account.data?.cycle?.start) }} → {{ timeText(account.data?.cycle?.reset_at) }}</p>
    <div v-if="account.data?.spend_usd || account.data?.on_demand" class="usage-summary">
      <span v-if="account.data.spend_usd?.total != null">本期参考消费 <strong>{{ money(account.data.spend_usd.total) }}</strong></span>
      <span v-if="account.data.on_demand">按量付费 <strong>{{ account.data.on_demand.enabled ? '已启用' : '未启用' }}</strong><template v-if="account.data.on_demand.used_usd != null"> · 已用 {{ money(account.data.on_demand.used_usd) }}</template></span>
    </div>
    <p v-if="account.data?.grok_weekly">Grok 周额度剩余 {{ account.data.grok_weekly.remaining_pct ?? '—' }}% · 重置 {{ timeText(account.data.grok_weekly.reset_at) }}</p>
    <p v-if="account.data?.notice" class="notice">{{ account.data.notice }}</p>
    <p class="muted">额度百分比来自 Cursor；美元上限为推算值。明细按本账期、服务商模型分类展示。</p>
    <p v-if="busy" role="status">正在查询模型用量…</p><p v-if="error" role="alert" class="error">{{ error }}</p>
    <template v-if="detail"><div class="section-heading"><h3>模型用量</h3><span>{{ money(detail.totals.spend_usd) }} · {{ detail.totals.total_tokens.toLocaleString() }} tokens</span></div>
      <section v-for="group in detail.groups" :key="group.key" class="detail-group"><h4>{{ group.name }} <span>{{ money(group.spend_usd) }}</span></h4><p v-if="!group.models.length" class="muted">本账期暂无模型用量。</p>
        <div v-for="model in group.models" :key="model.model" class="model-block"><div class="model-row"><strong>{{ model.model }}</strong><span>{{ model.total_tokens.toLocaleString() }} tokens</span><span>{{ money(model.spend_cents / 100) }}</span></div><details><summary>{{ model.model }} Token 明细</summary><dl class="token-counts"><div><dt>输入</dt><dd>{{ (model.input_tokens || 0).toLocaleString() }}</dd></div><div><dt>输出</dt><dd>{{ (model.output_tokens || 0).toLocaleString() }}</dd></div><div><dt>缓存写入</dt><dd>{{ (model.cache_write_tokens || 0).toLocaleString() }}</dd></div><div><dt>缓存读取</dt><dd>{{ (model.cache_read_tokens || 0).toLocaleString() }}</dd></div></dl></details></div>
      </section><p class="muted">明细查询时间 {{ timeText(detail.fetched_at) }}</p>
    </template>
  </UiDialog>
</template>
