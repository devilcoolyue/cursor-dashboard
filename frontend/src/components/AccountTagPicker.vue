<script setup lang="ts">
import { computed, ref } from 'vue'
import UiIcon from './UiIcon.vue'
import UiPopover from './UiPopover.vue'

const props = defineProps<{ catalog: Record<string, number>; disabled?: boolean }>()
const tags = defineModel<string[]>({ required: true })
const opened = ref(false), query = ref('')
const options = computed(() => [...new Set([...Object.keys(props.catalog), ...tags.value])]
  .filter(tag => tag.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
const newTags = computed(() => [...new Set(query.value.split(/[,，\n]/).map(tag => tag.trim()).filter(Boolean))])
const canCreate = computed(() => newTags.value.some(tag => !options.value.includes(tag) && !tags.value.includes(tag)))
function toggle(tag: string) {
  tags.value = tags.value.includes(tag) ? tags.value.filter(value => value !== tag) : [...tags.value, tag]
}
function addTyped() {
  if (!newTags.value.length) return
  tags.value = [...new Set([...tags.value, ...newTags.value])]
  query.value = ''
}
function enter(event: KeyboardEvent) {
  if (event.isComposing) return
  event.preventDefault()
  addTyped()
}
</script>
<template>
  <div class="account-tag-picker">
    <span class="form-field-label">标签 <small>可选</small></span>
    <UiPopover v-model="opened" label="选择账号标签" :width="360" focus-on-open @shown="query = ''">
      <template #trigger="{ toggle: toggleOpen, id, open }">
        <button type="button" class="account-tag-trigger" aria-label="标签" aria-haspopup="dialog" :aria-expanded="open" :aria-controls="id" :disabled="disabled" @click="toggleOpen">
          <span v-if="tags.length" class="selected-account-tags"><span v-for="tag in tags" :key="tag" class="account-tag">{{ tag }}</span></span>
          <span v-else class="tag-placeholder">选择已有标签，或新建标签</span>
          <UiIcon name="chevronDown" :size="15" />
        </button>
      </template>
      <template #default="{ close }">
        <div class="account-tag-content">
          <label class="account-tag-search"><span class="sr-only">搜索或新建标签</span><UiIcon name="search" :size="15" /><input v-model="query" placeholder="搜索或新建标签…" maxlength="4000" autocomplete="off" @keydown.enter="enter" /></label>
          <div class="account-tag-options" role="group" aria-label="可选账号标签">
            <button v-for="tag in options" :key="tag" type="button" :aria-pressed="tags.includes(tag)" @click="toggle(tag)"><UiIcon name="tags" :size="14" /><span>{{ tag }}</span><small v-if="catalog[tag]">{{ catalog[tag] }}</small><UiIcon v-if="tags.includes(tag)" name="check" :size="15" /></button>
            <p v-if="!options.length && !query.trim()" class="account-tag-empty">还没有标签，输入名称即可新建。</p>
            <button v-if="canCreate" type="button" class="tag-create" @click="addTyped"><span aria-hidden="true">＋</span><span>新建「{{ query.trim() }}」</span><kbd>↵</kbd></button>
          </div>
          <div class="account-tag-footer"><span>已选 {{ tags.length }} 个 · 支持多选</span><button type="button" @click="close()">完成</button></div>
        </div>
      </template>
    </UiPopover>
  </div>
</template>
