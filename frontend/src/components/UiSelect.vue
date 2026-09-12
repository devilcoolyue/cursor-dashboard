<script lang="ts">
// Only one select is open, including selects inside modal dialogs.
let closeActiveSelect: (() => void) | undefined
</script>
<script setup lang="ts" generic="T extends string">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import UiIcon from './UiIcon.vue'

defineOptions({ inheritAttrs: false })
const props = defineProps<{
  modelValue: T
  options: readonly { value: T; label: string; disabled?: boolean; icon?: string; name?: string; description?: string }[]
  disabled?: boolean
  placeholder?: string
  icon?: string
  variant?: 'workspace'
}>()
const emit = defineEmits<{ 'update:modelValue': [value: T] }>()
const trigger = ref<HTMLButtonElement>(), menu = ref<HTMLDivElement>()
const opened = ref(false), activeIndex = ref(-1)
const menuId = `select-${useId()}`
const selected = computed(() => props.options.find(option => option.value === props.modelValue))
const enabled = computed(() => props.options.flatMap((option, index) => option.disabled ? [] : [index]))
let typeahead = '', typedAt = 0
let observer: ResizeObserver | undefined

function close() {
  opened.value = false
  if (menu.value?.matches(':popover-open')) menu.value.hidePopover()
  if (closeActiveSelect === close) closeActiveSelect = undefined
  typeahead = ''
  document.removeEventListener('pointerdown', outside)
  document.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', position)
  window.visualViewport?.removeEventListener('resize', position)
  window.visualViewport?.removeEventListener('scroll', position)
  observer?.disconnect()
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !trigger.value?.contains(event.target) && !menu.value?.contains(event.target)) close()
}
function onScroll(event: Event) {
  if (event.target instanceof Node && menu.value?.contains(event.target)) return
  position()
}
function position() {
  const button = trigger.value, panel = menu.value
  if (!opened.value || !button || !panel) return
  if (!button.getClientRects().length) { close(); return }
  const rect = button.getBoundingClientRect(), viewport = window.visualViewport
  const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0
  const width = viewport?.width || document.documentElement.clientWidth, height = viewport?.height || innerHeight
  const gap = 6, edge = 8
  const menuWidth = Math.min(Math.max(rect.width, 200), width - edge * 2)
  panel.style.width = `${menuWidth}px`
  const below = Math.max(0, top + height - rect.bottom - gap - edge), above = Math.max(0, rect.top - top - gap - edge)
  const flip = below < Math.min(panel.scrollHeight + 2, 288) && above > below
  panel.dataset.placement = flip ? 'top' : 'bottom'
  panel.style.maxHeight = `${Math.min(288, flip ? above : below)}px`
  panel.style.left = `${Math.max(left + edge, Math.min(rect.left, left + width - menuWidth - edge))}px`
  panel.style.top = `${flip ? rect.top - gap - panel.getBoundingClientRect().height : rect.bottom + gap}px`
}
function activate(index: number | undefined, scroll = true) {
  activeIndex.value = index ?? -1
  if (scroll) menu.value?.children[activeIndex.value]?.scrollIntoView({ block: 'nearest' })
}
async function open(last = false) {
  if (props.disabled || opened.value) return
  closeActiveSelect?.()
  closeActiveSelect = close
  opened.value = true
  await nextTick()
  if (!opened.value || !menu.value || !trigger.value || props.disabled) return
  menu.value.showPopover()
  position()
  const selectedIndex = props.options.findIndex(option => option.value === props.modelValue)
  activate(enabled.value.includes(selectedIndex) ? selectedIndex : last ? enabled.value.at(-1) : enabled.value[0])
  document.addEventListener('pointerdown', outside)
  document.addEventListener('scroll', onScroll, true)
  window.addEventListener('resize', position)
  window.visualViewport?.addEventListener('resize', position)
  window.visualViewport?.addEventListener('scroll', position)
  observer = new ResizeObserver(position)
  observer.observe(trigger.value)
}
function choose(index: number) {
  const option = props.options[index]
  if (props.disabled || !option || option.disabled) return
  close()
  trigger.value?.focus({ preventScroll: true })
  if (option.value !== props.modelValue) emit('update:modelValue', option.value)
}
async function keydown(event: KeyboardEvent) {
  const { key } = event
  if (event.isComposing || props.disabled) return
  if (key === 'Escape' && opened.value) {
    event.preventDefault(); event.stopPropagation(); close()
  } else if (key === 'Tab') {
    close()
  } else if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(key)) {
    event.preventDefault(); event.stopPropagation()
    if (!opened.value) {
      await open(key === 'ArrowUp' || key === 'End')
      if (!['Home', 'End'].includes(key)) return
    } else if (key === 'Enter' || key === ' ') { choose(activeIndex.value); return }
    const index = enabled.value.indexOf(activeIndex.value)
    const next = key === 'Home' ? 0 : key === 'End' ? enabled.value.length - 1
      : Math.max(0, Math.min(enabled.value.length - 1, index + (key === 'ArrowUp' ? -1 : 1)))
    activate(enabled.value[next])
  } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault(); event.stopPropagation()
    if (!opened.value) await open()
    const now = Date.now()
    typeahead = (now - typedAt < 700 ? typeahead : '') + key.toLocaleLowerCase()
    typedAt = now
    const match = enabled.value.find(index => props.options[index]!.label.toLocaleLowerCase().startsWith(typeahead))
    if (match !== undefined) activate(match)
  }
}
watch(() => [props.disabled, props.modelValue, props.options], () => {
  if (props.disabled) { close(); return }
  if (!opened.value) return
  if (!enabled.value.includes(activeIndex.value)) activate(enabled.value[0])
  position()
}, { deep: true, flush: 'post' })
onBeforeUnmount(close)
</script>

<template>
  <span class="ui-select" :class="{ 'ui-select-workspace': variant === 'workspace' }">
    <button ref="trigger" v-bind="$attrs" type="button" class="ui-select-trigger" role="combobox"
      :title="($attrs.title as string | undefined) || selected?.label" :disabled="disabled"
      aria-haspopup="listbox" :aria-expanded="opened" :aria-controls="menuId"
      :aria-activedescendant="opened && activeIndex >= 0 ? `${menuId}-${activeIndex}` : undefined"
      @click="opened ? close() : open()" @keydown="keydown" @blur="close">
      <template v-if="variant === 'workspace'"><span class="space-avatar"><UiIcon :name="selected?.icon || 'users'" :size="18" /></span><span class="space-copy"><span class="space-name">{{ selected?.name || selected?.label }}</span><span class="space-role">{{ selected?.description }}</span></span><UiIcon name="chevrons" class="space-chevron" :size="14" /></template>
      <template v-else><UiIcon v-if="icon" :name="icon" :size="14" class="ui-select-icon" /><span class="ui-select-value"><slot name="value">{{ selected?.label || placeholder || '请选择' }}</slot></span><UiIcon name="chevronDown" class="ui-select-chevron" :size="14" /></template>
    </button>
    <div :id="menuId" ref="menu" class="ui-select-menu" :class="{ 'workspace-menu': variant === 'workspace' }" role="listbox" popover="manual" :hidden="!opened"
      :aria-label="$attrs['aria-label'] as string | undefined" :aria-labelledby="$attrs['aria-labelledby'] as string | undefined"
      @pointerdown.prevent @click.stop.prevent>
      <div v-for="(option, index) in options" :id="`${menuId}-${index}`" :key="option.value"
        role="option" class="ui-select-option" :class="{ 'is-active': activeIndex === index }"
        :aria-label="option.label"
        :aria-selected="option.value === modelValue" :aria-disabled="!!option.disabled"
        @pointermove="!option.disabled && activate(index, false)" @click.stop.prevent="choose(index)">
        <UiIcon v-if="variant === 'workspace'" :name="option.icon || 'users'" :size="18" />
        <span class="ui-select-option-label">{{ variant === 'workspace' ? option.name || option.label : option.label }}<small v-if="variant === 'workspace'">{{ option.description }}</small></span><UiIcon name="check" class="ui-select-check" :size="15" />
      </div>
      <div v-if="!options.length" class="ui-select-empty">暂无选项</div>
    </div>
  </span>
</template>

<style>
/* V1 PanelUI materials, shared by every Vue select and every skin. */
.ui-select { position: relative; display: inline-block; width: 100%; min-width: 0; vertical-align: middle; }
.ui-select-menu[hidden] { display: none; }
button.ui-select-trigger { display: flex; align-items: center; justify-content: space-between; gap: 10px; width: 100%; min-width: 0; min-height: 40px; padding: 8px 11px; background: var(--input-bg); color: var(--fg); border: 1px solid var(--line); border-radius: var(--radius-ctl); box-shadow: var(--ui-control-shadow); text-align: left; line-height: 1.5; transition: border-color .15s ease, box-shadow .15s ease, background-color .15s ease; }
button.ui-select-trigger:hover:not(:disabled) { background: var(--input-bg); border-color: var(--dim); }
html.keyboard-input button.ui-select-trigger:focus-visible, button.ui-select-trigger[aria-expanded=true] { outline: none; border-color: var(--accent); box-shadow: var(--ui-focus-shadow); }
button.ui-select-trigger[aria-invalid=true] { border-color: var(--bad); }
button.ui-select-trigger:disabled { cursor: not-allowed; }
.ui-select-value { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ui-select-icon, .ui-select-chevron { flex: none; color: var(--dim); }
.ui-select-chevron { transition: transform .15s ease, color .15s ease; }
.ui-select-trigger[aria-expanded=true] .ui-select-chevron { transform: rotate(180deg); color: var(--accent); }
/* Popovers remain in their dialog's DOM while rendering above its scroll container. */
.ui-select-menu { position: fixed; inset: auto; z-index: 1200; margin: 0; padding: 5px; box-sizing: border-box; min-width: 0; max-width: calc(100vw - 16px); overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-color: var(--line-popover) transparent; border: 1px solid var(--line-popover); border-radius: var(--radius-ctl); background: var(--card-popover); color: var(--fg); box-shadow: var(--shadow-popover); -webkit-backdrop-filter: var(--blur-panel); backdrop-filter: var(--blur-panel); }
.ui-select-menu::backdrop { background: transparent; }
.ui-select-option { display: flex; align-items: center; gap: 10px; min-height: 36px; padding: 8px 9px; border-radius: var(--ui-option-radius); color: var(--fg); cursor: pointer; font-size: 13px; font-weight: 400; line-height: 1.45; user-select: none; }
.ui-select-option-label { flex: 1; min-width: 0; overflow-wrap: anywhere; white-space: normal; }
.ui-select-option.is-active:not([aria-disabled=true]) { background: var(--ui-option-hover-bg); }
.ui-select-option[aria-selected=true] { color: var(--accent); background: var(--ui-option-selected-bg); font-weight: 600; }
.ui-select-option.is-active[aria-selected=true] { box-shadow: var(--ui-option-selected-shadow); }
.ui-select-check { flex: none; color: var(--accent); visibility: hidden; }
.ui-select-option[aria-selected=true] .ui-select-check { visibility: visible; }
.ui-select-option[aria-disabled=true] { opacity: .45; cursor: not-allowed; }
.ui-select-empty { padding: 12px 9px; color: var(--dim); font-size: 13px; }
@media (max-width: 760px) { .ui-select-option { min-height: 40px; } }
@media (prefers-reduced-motion: no-preference) { .ui-select-menu:popover-open { animation: ui-menu-in .12s ease-out; } }
@keyframes ui-menu-in { from { opacity: 0; transform: translateY(-3px); } to { opacity: 1; transform: none; } }
</style>
