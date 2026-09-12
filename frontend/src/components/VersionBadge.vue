<script setup lang="ts">
import { computed } from 'vue'
import { appVersion, releasesUrl } from '../version'
import { automaticCheckHours, checkUpdate, checkingUpdate, installingUpdate, lastUpdateCheck, openReleases, release, updateError } from '../updates'
import UiIcon from './UiIcon.vue'
import UiPopover from './UiPopover.vue'

const opened = defineModel<boolean>({ default: false })
const available = computed(() => release.value?.available === true)
const hint = computed(() => available.value ? `发现新版本 v${release.value?.latest_version}，点击更新` : `当前版本 v${appVersion}，查看版本与更新`)
const status = computed(() => checkingUpdate.value ? '正在检查更新…' : updateError.value ? '检查未完成，请重试'
  : available.value ? `新版本 v${release.value?.latest_version} 可用` : !release.value ? '尚未检查更新'
    : release.value.latest_version ? '已是最新版本' : '暂无正式发布的版本')
</script>
<template>
  <UiPopover v-model="opened" class="brand-version" label="版本与更新" placement="bottom" align="end" :width="280" focus-on-open>
    <template #trigger="{ toggle, open, id }">
      <button type="button" class="version-badge" :class="{ 'has-update': available }" :title="hint" :aria-label="hint" :aria-expanded="open" :aria-controls="id" aria-haspopup="dialog" @click="toggle">
        <span class="version-badge-text">v{{ appVersion }}</span><span v-if="available" class="version-update-dot" aria-hidden="true" /><UiIcon v-else class="version-collapsed-icon" name="info" :size="12" />
      </button>
    </template>
    <template #default="{ close }">
      <div class="version-menu">
        <header class="version-menu-header"><strong>当前版本</strong><button type="button" class="version-check" aria-label="检查更新" title="检查更新" :disabled="checkingUpdate || installingUpdate" @click="checkUpdate"><UiIcon name="refresh" :class="{ spinning: checkingUpdate }" :size="16" /></button></header>
        <div class="version-menu-body">
          <div class="version-number">v{{ appVersion }}<span v-if="release && !available && !updateError && !checkingUpdate && release.latest_version" class="version-current-check"><UiIcon name="check" :size="13" /></span></div>
          <p class="version-state" :class="{ available }" role="status">{{ status }}</p>
          <RouterLink v-if="available" to="/about" class="version-upgrade" @click="close"><UiIcon name="download" :size="15" />查看更新<UiIcon name="chevron" :size="14" /></RouterLink>
          <a :href="releasesUrl" target="_blank" rel="noopener noreferrer" class="version-release" @click="openReleases"><UiIcon name="external" :size="14" />查看发布</a>
          <p v-if="updateError" class="version-check-error">{{ updateError }}</p>
        </div>
        <footer class="version-menu-footer"><span>每 {{ automaticCheckHours }} 小时自动检查<span v-if="lastUpdateCheck" class="version-last-check">上次 {{ new Date(lastUpdateCheck).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) }}</span></span><RouterLink to="/about" aria-label="关于与更新" @click="close"><UiIcon name="chevron" :size="15" /></RouterLink></footer>
      </div>
    </template>
  </UiPopover>
</template>
<style>
.brand-version { flex: none; }
.version-badge { display: inline-flex; align-items: center; gap: 6px; height: 23px; min-height: 23px; padding: 0 7px; border: 0; border-radius: 6px; background: var(--count-bg); color: var(--dim); box-shadow: none; font-size: 10px; font-weight: 500; line-height: 1; letter-spacing: 0; font-variant-numeric: tabular-nums; white-space: nowrap; transition: background .16s ease, color .16s ease; }
.version-badge:hover:not(:disabled) { background: var(--row-hover-bg); color: var(--fg); box-shadow: none; }
.version-badge.has-update { background: #fff0bb; color: #a45b0d; }
.version-badge.has-update:hover:not(:disabled) { background: #ffe69a; color: #8d4905; }
.version-update-dot { position: relative; display: block; width: 6px; height: 6px; border-radius: 50%; background: #e5a026; flex: none; }
.version-update-dot::after { content: ''; position: absolute; inset: -3px; border: 2px solid #e5a026; border-radius: inherit; animation: version-breathe 2.4s ease-in-out infinite; }
.version-collapsed-icon { display: none; }
.version-menu { color: var(--fg); }
.version-menu-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 15px; border-bottom: 1px solid var(--line); }
.version-menu-header strong { font-size: 12px; font-weight: 500; }
.version-check { display: grid; place-items: center; width: 28px; height: 28px; padding: 0; border: 0; background: transparent; color: var(--dimmer); box-shadow: none; }
.version-check:hover:not(:disabled) { background: var(--row-hover-bg); color: var(--fg); }
.version-menu-body { padding: 22px 18px 20px; text-align: center; }
.version-number { display: flex; justify-content: center; align-items: center; gap: 9px; font-size: 26px; font-weight: 650; line-height: 1.25; font-variant-numeric: tabular-nums; letter-spacing: -.5px; }
.version-current-check { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; background: color-mix(in srgb, var(--ok) 14%, transparent); color: var(--ok); }
.version-state { margin: 7px 0 19px; font-size: 12px; color: var(--dim); }
.version-state.available { color: var(--warn); }
.version-release { display: flex; justify-content: center; align-items: center; gap: 7px; padding: 4px; font-size: 12px; color: var(--dim); }
.version-release:hover { color: var(--fg); }
.version-upgrade { display: flex; align-items: center; justify-content: center; gap: 8px; margin: 0 0 12px; padding: 9px 12px; border-radius: 7px; background: #fff0bb; color: #99540a; font-size: 12px; font-weight: 500; }
.version-upgrade:hover { color: #7c4103; background: #ffe69a; text-decoration: none; }
.version-menu-footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 15px; border-top: 1px solid var(--line); color: var(--dimmer); font-size: 10px; }
.version-last-check { display: block; margin-top: 3px; }
.version-menu-footer > a { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 5px; color: var(--dim); }
.version-menu-footer > a:hover { background: var(--row-hover-bg); }
.version-check-error { margin: 12px 0 0; font-size: 11px; line-height: 1.6; color: var(--dim); }
.sidebar.collapsed .brand-version { position: absolute; right: 6px; bottom: 5px; }
.sidebar.collapsed .version-badge { width: 20px; height: 20px; min-height: 20px; padding: 0; justify-content: center; border: 2px solid var(--sidebar-bg); border-radius: 50%; background: var(--card); }
.sidebar.collapsed .version-badge.has-update { background: #fff0bb; }
.sidebar.collapsed .version-badge-text { display: none; }
.sidebar.collapsed .version-collapsed-icon { display: block; }
@keyframes version-breathe { 0%, 100% { opacity: .18; transform: scale(.72); } 50% { opacity: .7; transform: scale(1.05); } }
@media (prefers-reduced-motion: reduce) { .version-update-dot::after { animation: none; opacity: .4; } }
</style>
