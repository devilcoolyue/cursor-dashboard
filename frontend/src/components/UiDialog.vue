<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, useId } from 'vue'
defineProps<{ title: string; wide?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const titleId = useId()
let previous: HTMLElement | null = null
let overflow = ''
onMounted(() => {
  previous = document.activeElement as HTMLElement
  overflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  dialog.value?.showModal()
})
onBeforeUnmount(() => {
  dialog.value?.close()
  document.body.style.overflow = overflow
  if (previous?.isConnected) previous.focus()
})
</script>
<template>
  <dialog ref="dialog" :class="{ wide }" :aria-labelledby="titleId" @cancel.prevent="emit('close')">
    <header class="dialog-header"><h2 :id="titleId">{{ title }}</h2><button type="button" class="icon-button" aria-label="关闭弹窗" @click="emit('close')">×</button></header>
    <div class="dialog-body"><slot /></div>
  </dialog>
</template>
