<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { probe, reportReady, type Probe } from './platform'

const result = ref<Probe>()
const error = ref('')
const busy = ref(false)
async function refresh() {
  busy.value = true
  error.value = ''
  try {
    result.value = await probe()
    await nextTick()
    await reportReady(document.querySelectorAll('[data-account]').length)
  } catch (reason) {
    error.value = String(reason)
  } finally {
    busy.value = false
  }
}
onMounted(refresh)
</script>

<template>
  <main>
    <header><span class="brand">CURSOR PANEL</span><span class="badge">P0 · 桌面技术验证</span></header>
    <section class="intro">
      <p class="eyebrow">LOCAL WORKSPACE</p>
      <h1>你的账号，留在本机。</h1>
      <p>此版本仅使用模拟数据，验证桌面与本地后台的连接。</p>
    </section>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <template v-if="result">
      <section class="checks" aria-label="运行状态">
        <div><span>本地后台</span><strong>{{ result.backend.frozen ? '随包 Python 已启动' : '开发运行时' }}</strong></div>
        <div><span>系统凭证库</span><strong>{{ result.keyring.ok ? '临时读写与清理通过' : result.keyring.detail }}</strong></div>
        <div><span>后台就绪</span><strong>{{ result.backend_start_ms }} ms</strong></div>
      </section>
      <section aria-labelledby="accounts-heading">
        <div class="section-title"><h2 id="accounts-heading">模拟账号</h2><span>{{ result.accounts.length }} 个账号</span></div>
        <article v-for="account in result.accounts" :key="account.id" data-account>
          <div class="identity"><strong>{{ account.label }}</strong><span>{{ account.email }}</span></div>
          <div class="quota"><progress :value="account.remaining_pct" max="100" :aria-label="`${account.label}剩余额度`"></progress><strong>{{ account.remaining_pct }}%</strong></div>
        </article>
      </section>
      <footer><span>Python {{ result.backend.python }} · SQLite {{ result.backend.sqlite }} · API v{{ result.backend.api_version }}</span><button :disabled="busy" @click="refresh">{{ busy ? '检查中…' : '重新检查' }}</button></footer>
    </template>
    <p v-else-if="!error" role="status">正在连接本地后台…</p>
  </main>
</template>

<style>
:root { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #20312d; background: #f5f6f2; font-synthesis: none; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1000px; margin: auto; padding: 34px 48px; }
header, .section-title, article, footer { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.brand { font-size: 13px; letter-spacing: .16em; font-weight: 750; }
.badge { color: #547367; font-size: 12px; padding: 6px 10px; border: 1px solid #d5ded5; border-radius: 5px; }
.intro { margin: 54px 0 30px; }
.eyebrow { color: #648473; letter-spacing: .18em; font-size: 11px; }
h1 { font-size: clamp(26px, 4vw, 38px); letter-spacing: -.04em; margin: 12px 0; }
.intro > p:last-child { color: #687870; font-size: 14px; }
.checks { display: grid; grid-template-columns: repeat(3, 1fr); padding: 20px 0; border-top: 1px solid #dce3da; border-bottom: 1px solid #dce3da; gap: 20px; }
.checks div { display: grid; gap: 8px; }
.checks span, .identity span, footer, .section-title span { color: #687870; font-size: 12px; }
.checks strong { font-size: 13px; }
.section-title { margin-top: 24px; }
h2 { font-size: 16px; font-weight: 650; }
article { padding: 22px 0; border-bottom: 1px solid #e0e5dc; }
.identity { display: grid; gap: 6px; font-size: 14px; }
.quota { display: flex; gap: 18px; align-items: center; font-size: 13px; }
.quota strong { min-width: 36px; text-align: right; }
progress { width: 160px; height: 6px; border: 0; accent-color: #4b8067; border-radius: 5px; }
progress::-webkit-progress-bar { background: #e0e8dd; border-radius: 5px; }
progress::-webkit-progress-value { background: #4b8067; border-radius: 5px; }
footer { margin-top: 26px; }
button { border: 1px solid #b6c5b8; border-radius: 5px; background: transparent; padding: 9px 16px; color: #385e4b; cursor: pointer; }
button:focus-visible { outline: 2px solid #4b8067; outline-offset: 3px; }
button:disabled { opacity: .5; cursor: wait; }
.error { padding: 16px; background: #f7e9e3; color: #923b26; }
@media (max-width: 600px) { main { padding: 24px; } .checks { grid-template-columns: 1fr; } progress { width: 80px; } footer { align-items: flex-start; } }
</style>
