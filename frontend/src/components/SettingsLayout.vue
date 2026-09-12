<script setup lang="ts">
defineProps<{ title: string; context?: string; error?: string }>()
</script>
<template>
  <section class="settings-page">
    <header class="topbar settings-toolbar">
      <div class="workspace-title"><h1>{{ title }}</h1><span v-if="context" class="settings-context">{{ context }}</span></div>
      <div v-if="$slots.actions" class="settings-toolbar-actions"><slot name="actions" /></div>
    </header>
    <div class="settings-content">
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="settings-surface"><slot /></div>
      <slot name="after" />
    </div>
    <slot name="dialogs" />
  </section>
</template>
