<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { sidebarAccounts } from '../sidebar-state'
import UiIcon from './UiIcon.vue'
import UiPopover from './UiPopover.vue'

const emit = defineEmits<{ selected: [] }>()
const opened = ref(false), search = ref('')
const entries = computed(() => Object.entries(sidebarAccounts.tags).map(([name, count]) => ({ name, count })))
const shortcuts = computed(() => {
  const items = entries.value.slice(0, 3)
  const selected = sidebarAccounts.tag
  if (selected && !items.some(item => item.name === selected)) {
    if (items.length === 3) items.pop()
    items.push({ name: selected, count: sidebarAccounts.tags[selected] || 0 })
  }
  return items
})
const results = computed(() => [{ name: '', count: sidebarAccounts.total }, ...entries.value]
  .filter(item => (item.name || '全部标签').toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())))
watch(opened, value => { if (value) search.value = '' })
function choose(name: string, close?: () => void) {
  sidebarAccounts.tag = name
  close?.()
  emit('selected')
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing) return
  const container = (event.currentTarget as HTMLElement)
  const buttons = [...container.querySelectorAll<HTMLButtonElement>('.tag-result')]
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) && buttons.length) {
    if ((event.key === 'Home' || event.key === 'End') && event.target instanceof HTMLInputElement) return
    event.preventDefault()
    const index = buttons.indexOf(event.target as HTMLButtonElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : index < 0 ? (event.key === 'ArrowDown' ? 0 : buttons.length - 1)
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
    buttons[next]?.focus()
  } else if (event.key === 'Enter' && event.target instanceof HTMLInputElement && buttons.length === 1) {
    event.preventDefault(); buttons[0]?.click()
  }
}
</script>
<template>
  <div class="account-tag-navigation" aria-label="按标签筛选账号">
    <div class="tag-shortcuts">
      <p class="sidebar-section-label tag-caption">标签筛选</p>
      <button type="button" class="tag-shortcut" :aria-pressed="!sidebarAccounts.tag" @click="choose('')"><i class="nav-dot" /><span>全部标签</span><b>{{ sidebarAccounts.total }}</b></button>
      <button v-for="item in shortcuts" :key="item.name" type="button" class="tag-shortcut" :title="item.name" :aria-pressed="sidebarAccounts.tag === item.name" @click="choose(item.name)"><i class="nav-dot" /><span>{{ item.name }}</span><b>{{ item.count }}</b></button>
    </div>
    <UiPopover v-model="opened" label="选择标签" placement="right" :width="280" focus-on-open>
      <template #trigger="{ toggle, open, id }">
        <button type="button" class="tag-picker-trigger" :class="{ 'few-tags': entries.length <= 3 }" :title="`标签：${sidebarAccounts.tag || '全部标签'}`" :aria-label="`选择标签，当前：${sidebarAccounts.tag || '全部标签'}`" :aria-expanded="open" :aria-controls="id" aria-haspopup="dialog" @click="toggle">
          <UiIcon name="tags" :size="15" /><span class="tag-picker-compact">标签：{{ sidebarAccounts.tag || '全部标签' }}</span><span class="tag-picker-more">更多标签 <small>{{ entries.length }}</small></span><UiIcon name="chevronDown" :size="13" />
        </button>
      </template>
      <template #default="{ close }">
        <div class="tag-picker" @keydown="keydown">
          <header class="sidebar-popover-header"><strong>选择标签</strong><button type="button" class="popover-close" aria-label="关闭标签选择" @click="close"><UiIcon name="close" :size="15" /></button></header>
          <label class="tag-picker-search"><UiIcon name="search" :size="15" /><input v-model="search" type="search" aria-label="搜索标签" placeholder="搜索标签…" maxlength="128" /></label>
          <div class="tag-picker-results" role="group" aria-label="全部标签列表">
            <button v-for="item in results" :key="item.name" type="button" class="tag-result" :aria-pressed="sidebarAccounts.tag === item.name" @click="choose(item.name, close)"><i class="nav-dot" /><span>{{ item.name || '全部标签' }}</span><b>{{ item.count }}</b><UiIcon name="check" :size="14" /></button>
            <p v-if="!results.length" class="tag-picker-empty">没有匹配的标签</p>
          </div>
          <div class="tag-picker-status" role="status">{{ search.trim() ? `${results.length} 个匹配结果` : `${entries.length} 个标签` }}</div>
        </div>
      </template>
    </UiPopover>
  </div>
</template>
