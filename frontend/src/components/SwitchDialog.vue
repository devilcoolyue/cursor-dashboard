<script setup lang="ts">
import UiDialog from './UiDialog.vue'
import SwitchCommandForm from './SwitchCommandForm.vue'
import type { Account } from '../api'
defineProps<{ account: Account }>()
const emit = defineEmits<{ close: [] }>()
</script>
<template>
  <UiDialog title="在本机终端切换" wide @close="emit('close')">
    <p><strong>{{ account.label }}</strong> · {{ account.email }}</p>
    <SwitchCommandForm :account="account" />
  </UiDialog>
</template>
