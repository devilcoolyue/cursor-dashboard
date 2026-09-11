<script setup lang="ts">
import { useId } from 'vue'
defineProps<{ title: string; description?: string; count?: number }>()
const headingId = useId()
</script>
<template>
  <section class="settings-section" :aria-labelledby="headingId">
    <div class="settings-section-info"><h2 :id="headingId">{{ title }}<span v-if="count !== undefined" class="account-count">{{ count }}</span></h2><p v-if="description">{{ description }}</p></div>
    <div class="settings-section-body"><slot /></div>
  </section>
</template>
