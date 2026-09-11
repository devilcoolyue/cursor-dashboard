<script setup lang="ts">
import UiPopover from './UiPopover.vue'
withDefaults(defineProps<{ text: string; placement?: 'top' | 'bottom'; align?: 'start' | 'end' }>(), { placement: 'top', align: 'start' })
</script>
<template>
  <UiPopover :label="text" :placement="placement" :align="align" :width="250" panel-role="tooltip" hover class="ui-tooltip">
    <template #trigger="controls"><slot v-bind="controls" /></template>
    <template #default><span>{{ text }}</span></template>
  </UiPopover>
</template>
