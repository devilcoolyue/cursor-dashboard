<script setup lang="ts">
import brandMarkup from '../icon.svg?raw'
withDefaults(defineProps<{ title?: string; description?: string; busy?: boolean }>(), {
  title: '正在连接服务', description: '正在读取空间与账号信息…', busy: true,
})
</script>

<template>
  <main class="startup-screen" :aria-busy="busy">
    <div class="startup-content">
      <span class="startup-mark" aria-hidden="true" v-html="brandMarkup" />
      <p class="startup-brand">Cursor Panel</p>
      <span v-if="busy" class="startup-progress" aria-hidden="true" />
      <h1 class="startup-title" :role="busy ? 'status' : undefined">{{ title }}</h1>
      <p class="startup-description" :role="busy ? undefined : 'alert'">{{ description }}</p>
      <div v-if="$slots.default" class="startup-actions"><slot /></div>
    </div>
  </main>
</template>
