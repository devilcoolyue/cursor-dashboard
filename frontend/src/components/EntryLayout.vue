<script setup lang="ts">
import { useId } from 'vue'
import UiIcon from './UiIcon.vue'
import brandIcon from '../icon.svg'
import { appVersion } from '../version'
defineProps<{ title: string; description: string }>()
const titleId = useId(), instanceHost = window.location.host
</script>
<template>
  <main class="entry-page">
    <div class="entry-layout">
      <div class="entry-brand"><img :src="brandIcon" alt="" width="42" height="42" /><div><strong>Cursor 额度</strong><span>Cursor Panel</span></div></div>
      <section class="entry-surface" :aria-labelledby="titleId">
        <header class="entry-heading"><h1 :id="titleId">{{ title }}</h1><p>{{ description }}</p></header>
        <div class="entry-instance"><UiIcon name="server" :size="14" /><span>当前实例</span><strong :title="instanceHost">{{ instanceHost }}</strong></div>
        <slot />
      </section>
      <nav class="entry-links" aria-label="账号帮助"><slot name="footer" /><RouterLink to="/docs/overview"><UiIcon name="book" :size="13" />使用文档</RouterLink><RouterLink to="/about">v{{ appVersion }} · 关于与更新</RouterLink></nav>
    </div>
  </main>
</template>

<style>
.entry-page { min-height: 100svh; display: grid; place-items: center; padding: 48px 20px; }
.entry-layout { width: min(420px, 100%); min-width: 0; animation: enter .25s ease-out; }
.entry-brand { display: flex; align-items: center; justify-content: center; gap: 12px; margin-bottom: 28px; }
.entry-brand > img { flex: none; }
.entry-brand > div { display: grid; gap: 1px; }
.entry-brand strong { font-size: 20px; font-weight: 650; line-height: 1.4; letter-spacing: -.025em; }
.entry-brand span { color: var(--dim); font-size: 11px; letter-spacing: .035em; }
.entry-surface { padding: 30px; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-dialog); box-shadow: var(--shadow); -webkit-backdrop-filter: var(--blur-panel); backdrop-filter: var(--blur-panel); }
.entry-heading h1 { margin: 0 0 8px; font-size: 22px; line-height: 1.45; font-weight: 600; letter-spacing: -.025em; }
.entry-heading p { margin: 0; font-size: 12px; color: var(--dim); line-height: 1.8; }
.entry-instance { display: flex; align-items: center; gap: 7px; min-width: 0; margin: 22px 0 24px; padding-block: 12px; border-block: 1px solid var(--line); color: var(--dim); font-size: 11px; }
.entry-instance > span { flex: none; }
.entry-instance > strong { min-width: 0; margin-left: auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg); font-weight: 400; }
.entry-form { gap: 20px; }
.entry-form input { height: 43px; }
.entry-password-field { display: grid; gap: 7px; }
.entry-password-input { position: relative; }
.entry-password-input > input { padding-right: 44px; }
.entry-password-toggle { position: absolute; top: 6px; right: 6px; width: 31px; height: 31px; padding: 0; color: var(--dim); background: transparent; border-color: transparent; box-shadow: none; }
.entry-password-toggle:hover:not(:disabled) { color: var(--fg); background: var(--row-hover-bg); border-color: transparent; }
.entry-submit { min-height: 42px; margin-top: 4px; font-size: 13px; }
.entry-submit > svg:last-child { transition: transform .15s ease; }
.entry-submit:hover:not(:disabled) > svg:last-child:not(.spinning) { transform: translateX(3px); }
.entry-help { margin: 16px 0 0; color: var(--dimmer); font-size: 11px; text-align: center; }
.entry-error { margin: 0; }
.entry-links { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-top: 20px; padding-inline: 3px; }
.entry-links > a { display: inline-flex; align-items: center; gap: 5px; padding: 4px 0; color: var(--dim); font-size: 11px; border-radius: 0; }
.entry-links > a:hover { color: var(--accent); background: transparent; }
@media (max-width: 480px) { .entry-page { padding: 32px 18px; } .entry-surface { padding: 24px; } .entry-brand { margin-bottom: 24px; } }
@media (max-height: 680px) and (min-width: 481px) { .entry-page { padding-block: 24px; } .entry-brand { margin-bottom: 20px; } }
</style>
