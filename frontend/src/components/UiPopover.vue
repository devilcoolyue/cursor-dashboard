<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { restoringFocus, restoreFocus } from '../input-modality'

const props = withDefaults(defineProps<{ label: string; placement?: 'top' | 'bottom' | 'right'; align?: 'start' | 'end'; width?: number; focusOnOpen?: boolean; hover?: boolean; panelRole?: 'dialog' | 'tooltip' }>(), { placement: 'bottom', width: 264, align: 'start', panelRole: 'dialog' })
const opened = defineModel<boolean>({ default: false })
const emit = defineEmits<{ shown: [] }>()
const anchor = ref<HTMLElement>(), panel = ref<HTMLElement>()
const id = `popover-${useId()}`
let observer: ResizeObserver | undefined
let hoverTimer: ReturnType<typeof setTimeout> | undefined
let pinned = false, keyboardFocus: 'first' | 'last' | undefined
function cancelHover() { clearTimeout(hoverTimer) }
function hoverEnter(event: PointerEvent) {
  if (!props.hover || event.pointerType !== 'mouse') return
  cancelHover()
  if (!opened.value) hoverTimer = setTimeout(() => { opened.value = true }, props.panelRole === 'tooltip' ? 250 : 120)
}
function hoverLeave(event: PointerEvent) {
  if (!props.hover || event.pointerType !== 'mouse') return
  cancelHover()
  if (!pinned && !panel.value?.contains(document.activeElement)) hoverTimer = setTimeout(() => close(), 180)
}
function focusin() {
  if (props.hover && props.panelRole === 'tooltip' && !restoringFocus && document.documentElement.classList.contains('keyboard-input')) {
    cancelHover(); opened.value = true
  }
}
function focusout(event: FocusEvent) {
  if (props.hover && !anchor.value?.contains(event.relatedTarget as Node | null)) close()
}
function toggle(event?: MouseEvent) {
  cancelHover()
  if (props.hover && props.panelRole !== 'tooltip' && event?.detail === 0) keyboardFocus = 'first'
  if (props.hover && opened.value && !pinned) { pinned = true; focusPanel(); return }
  pinned = !opened.value
  opened.value = !opened.value
}
function focusPanel() {
  if (!keyboardFocus) return
  const controls = panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]')
  controls?.[keyboardFocus === 'last' ? controls.length - 1 : 0]?.focus()
  keyboardFocus = undefined
}
function trigger() { return anchor.value?.querySelector<HTMLElement>('button, a') }
function position() {
  if (!opened.value || !panel.value || !trigger()) return
  const button = trigger()!, menu = panel.value
  if (!button.getClientRects().length) { opened.value = false; return }
  const rect = button.getBoundingClientRect(), viewport = window.visualViewport
  const x = viewport?.offsetLeft || 0, y = viewport?.offsetTop || 0
  const width = viewport?.width || document.documentElement.clientWidth, height = viewport?.height || innerHeight
  const edge = 8, gap = 8
  let menuWidth = Math.min(props.width, width - edge * 2)
  menu.style.width = props.panelRole === 'tooltip' ? 'max-content' : `${menuWidth}px`
  menu.style.maxWidth = `${menuWidth}px`
  menu.style.maxHeight = `${height - edge * 2}px`
  menuWidth = menu.offsetWidth
  const menuHeight = menu.offsetHeight
  let left = props.align === 'end' ? rect.right - menuWidth : rect.left, top = rect.bottom + gap
  if (props.placement === 'right' && rect.right + gap + menuWidth <= x + width - edge) {
    left = rect.right + gap; top = rect.top
  } else if (props.placement === 'top' || (top + menuHeight > y + height - edge && rect.top - gap >= menuHeight)) {
    top = rect.top - gap - menuHeight
  }
  menu.style.left = `${Math.max(x + edge, Math.min(left, x + width - menuWidth - edge))}px`
  menu.style.top = `${Math.max(y + edge, Math.min(top, y + height - menuHeight - edge))}px`
}
function cleanup() {
  observer?.disconnect(); observer = undefined
  window.removeEventListener('resize', position)
  document.removeEventListener('scroll', position, true)
  window.visualViewport?.removeEventListener('resize', position)
  window.visualViewport?.removeEventListener('scroll', position)
}
function close(returnFocus = false) {
  cancelHover(); pinned = false; keyboardFocus = undefined
  opened.value = false
  if (returnFocus) restoreFocus(() => trigger()?.focus({ preventScroll: true }))
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && opened.value) { event.preventDefault(); event.stopPropagation(); close(true); return }
  if (!props.hover || props.panelRole === 'tooltip' || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  event.preventDefault(); pinned = true
  if (event.target === trigger()) {
    keyboardFocus = event.key === 'ArrowUp' || event.key === 'End' ? 'last' : 'first'
    if (opened.value) focusPanel(); else opened.value = true
    return
  }
  const controls = [...(panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]') || [])]
  const index = controls.indexOf(event.target as HTMLElement)
  if (controls.length) controls[event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + controls.length) % controls.length]?.focus()
}
watch(opened, async value => {
  cleanup()
  if (!value) { cancelHover(); pinned = false; keyboardFocus = undefined; if (panel.value?.matches(':popover-open')) restoreFocus(() => panel.value?.hidePopover()); return }
  await nextTick()
  if (!opened.value || !panel.value) return
  panel.value.showPopover()
  position()
  if (props.focusOnOpen) (panel.value.querySelector<HTMLElement>('input') || panel.value.querySelector<HTMLElement>('button, a'))?.focus({ preventScroll: true })
  focusPanel()
  emit('shown')
  observer = new ResizeObserver(position)
  observer.observe(panel.value)
  if (trigger()) observer.observe(trigger()!)
  window.addEventListener('resize', position)
  document.addEventListener('scroll', position, true)
  window.visualViewport?.addEventListener('resize', position)
  window.visualViewport?.addEventListener('scroll', position)
})
onBeforeUnmount(() => { cancelHover(); cleanup(); if (panel.value?.matches(':popover-open')) restoreFocus(() => panel.value?.hidePopover()) })
</script>
<template>
  <span ref="anchor" class="ui-popover-anchor" @pointerenter="hoverEnter" @pointerleave="hoverLeave" @focusin="focusin" @focusout="focusout" @keydown="keydown">
    <slot name="trigger" :open="opened" :id="id" :toggle="toggle" :close="() => close()" />
    <div :id="id" ref="panel" class="ui-popover-panel" :class="{ 'ui-tooltip-panel': panelRole === 'tooltip' }" popover="auto" :role="panelRole" :aria-label="panelRole === 'dialog' ? label : undefined"
      @toggle="opened = ($event as ToggleEvent).newState === 'open'" @pointerenter="cancelHover">
      <slot :close="() => close(true)" />
    </div>
  </span>
</template>
