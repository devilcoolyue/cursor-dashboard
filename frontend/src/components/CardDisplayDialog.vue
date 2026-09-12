<script setup lang="ts">
import { computed, useId } from 'vue'
import { cardDisplay, cardFields, cardGroups, setCardPreset } from '../card-display'
import UiDialog from './UiDialog.vue'
import UiIcon from './UiIcon.vue'

const emit = defineEmits<{ close: [] }>()
const id = useId()
const activeCount = computed(() => cardFields.filter(field => cardDisplay[field.key]).length)
</script>

<template>
  <UiDialog title="卡片显示项" class="card-display-dialog" @close="emit('close')">
    <template #subtitle>自定义账号卡片，修改立即生效，仅保存在当前浏览器或客户端。</template>
    <div class="display-toolbar">
      <span class="display-count" role="status">{{ activeCount }}/{{ cardFields.length }} 开启</span>
      <div class="actions" role="group" aria-label="显示预设">
        <button type="button" @click="setCardPreset('all')">全开</button>
        <button type="button" @click="setCardPreset('compact')">精简</button>
      </div>
    </div>
    <section v-for="group in cardGroups" :key="group.key" class="display-section" :aria-labelledby="`${id}-${group.key}`">
      <div class="display-section-heading">
        <h3 :id="`${id}-${group.key}`"><UiIcon :name="group.icon" :size="15" />{{ group.label }}</h3>
        <span>{{ group.fields.filter(field => cardDisplay[field.key]).length }}/{{ group.fields.length }}</span>
      </div>
      <div class="display-options">
        <label v-for="field in group.fields" :key="field.key" class="display-option" :class="{ 'is-active': cardDisplay[field.key] }">
          <span class="display-option-text"><strong>{{ field.label }}</strong><span :id="`${id}-${field.key}-hint`">{{ field.hint }}</span></span>
          <span class="display-switch">
            <input v-model="cardDisplay[field.key]" type="checkbox" :aria-label="field.label" :aria-describedby="`${id}-${field.key}-hint`" />
            <span class="display-switch-track" aria-hidden="true" />
          </span>
        </label>
      </div>
    </section>
    <template #footer>
      <button type="button" class="display-reset" @click="setCardPreset('default')">恢复默认</button>
      <button type="button" class="primary" @click="emit('close')">完成</button>
    </template>
  </UiDialog>
</template>

<style>
.card-display-dialog { width: min(760px, calc(100vw - 32px)); }
.card-display-dialog .dialog-body { padding-top: 18px; padding-bottom: 18px; }
.display-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.display-toolbar button { font-size: 12px; padding: 4px 12px; }
.display-count { color: var(--accent); background: var(--accent-bg); border-radius: 20px; padding: 3px 11px; font-size: 12px; font-variant-numeric: tabular-nums; }
.display-section + .display-section { margin-top: 18px; }
.display-section-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 9px; color: var(--dim); }
.display-section-heading h3 { display: flex; align-items: center; gap: 8px; font-size: 12px; margin: 0; }
.display-section-heading svg { color: var(--accent); }
.display-section-heading > span { font-size: 11px; font-variant-numeric: tabular-nums; }
.display-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.display-option { display: flex; align-items: center; justify-content: space-between; gap: 14px; min-width: 0; padding: 9px 13px; border: 1px solid var(--line); border-radius: var(--radius-ctl); transition: background .15s ease, border-color .15s ease; }
.display-option.is-active { background: var(--accent-bg); border-color: color-mix(in srgb, var(--accent) 40%, var(--line)); }
.display-option:hover { background: var(--row-hover-bg); border-color: var(--accent); }
.display-option-text { display: grid; gap: 3px; min-width: 0; }
.display-option-text strong { color: var(--fg); font-size: 13px; }
.display-option-text > span { font-size: 11px; line-height: 1.5; overflow-wrap: anywhere; }
.display-switch { position: relative; display: inline-flex; flex: none; }
.display-switch input { position: absolute; inset: 0; opacity: 0; width: 100%; height: 100%; margin: 0; z-index: 1; }
.display-switch-track { width: 34px; height: 20px; border: 1px solid var(--line-popover); background: var(--track-bg); border-radius: 20px; transition: background .18s ease, border-color .18s ease; }
.display-switch-track::after { content: ''; display: block; width: 14px; height: 14px; margin: 2px; border-radius: 50%; background: var(--dim); transition: transform .18s ease, background .18s ease; }
.display-switch input:checked + .display-switch-track { background: var(--accent); border-color: var(--accent); }
.display-switch input:checked + .display-switch-track::after { background: var(--btn-primary-fg); transform: translateX(14px); }
.display-switch input:focus-visible + .display-switch-track { outline: 2px solid var(--accent); outline-offset: 3px; }
.display-reset { margin-right: auto; }
@media (max-width: 600px) { .display-options { grid-template-columns: 1fr; } }
</style>
