<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, useId } from 'vue'
import UiIcon from './UiIcon.vue'
import { restoreFocus } from '../input-modality'
const props = defineProps<{ title: string; wide?: boolean; dismissBackdrop?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const titleId = useId()
let previous: HTMLElement | null = null
let overflow = ''
let backdropStart = false
function outside(event: MouseEvent) {
  const bounds = dialog.value?.getBoundingClientRect()
  return !!bounds && event.target === dialog.value && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)
}
function backdropClick(event: MouseEvent) {
  if (props.dismissBackdrop && backdropStart && outside(event)) emit('close')
  backdropStart = false
}
function backdropPointerdown(event: PointerEvent) { backdropStart = outside(event) }
onMounted(() => {
  previous = document.activeElement as HTMLElement
  overflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  dialog.value?.showModal()
})
onBeforeUnmount(() => {
  // close() can restore focus itself before the explicit fallback below.
  restoreFocus(() => {
    dialog.value?.close()
    document.body.style.overflow = overflow
    if (previous?.isConnected) previous.focus({ preventScroll: true })
  })
})
</script>
<template>
  <dialog ref="dialog" :class="{ wide }" :aria-labelledby="titleId" @cancel.prevent="emit('close')" @pointerdown="backdropPointerdown" @click="backdropClick">
    <header class="dialog-header"><div class="dialog-heading"><h2 :id="titleId">{{ title }}</h2><p v-if="$slots.subtitle" class="dialog-subtitle"><slot name="subtitle" /></p></div><button type="button" class="icon-button dialog-close" aria-label="关闭弹窗" @click="emit('close')"><UiIcon name="close" :size="18" /></button></header>
    <div class="dialog-body"><slot /></div>
    <footer v-if="$slots.footer" class="dialog-footer"><slot name="footer" /></footer>
  </dialog>
</template>
