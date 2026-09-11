<script setup lang="ts">
import { ref } from 'vue'
import type { Account } from '../api'
import UiIcon from './UiIcon.vue'
import UiTooltip from './UiTooltip.vue'
import UiPopover from './UiPopover.vue'
defineProps<{ account: Account; busy: boolean; refreshing: boolean; canSwitch: boolean; canGrant: boolean }>()
const emit = defineEmits<{ open: [kind: string]; refresh: [] }>()
const menuOpen = ref(false)
</script>
<template>
  <div class="account-action-controls">
    <UiTooltip v-if="account.capabilities.detail" text="额度明细" placement="bottom" align="end" v-slot="{ id, close }"><button type="button" class="card-icon-action" aria-label="明细" :aria-describedby="id" @click="close(); emit('open', 'detail')"><UiIcon name="detail" :size="15" /></button></UiTooltip>
    <UiTooltip v-if="account.capabilities.refresh" :text="refreshing ? '刷新中…' : '刷新账号额度'" placement="bottom" align="end" v-slot="{ id, close }"><button type="button" class="card-icon-action" aria-label="刷新" :aria-describedby="id" :disabled="busy" @click="close(); emit('refresh')"><UiIcon name="refresh" :size="15" :class="{ spinning: refreshing }" /></button></UiTooltip>
    <UiTooltip v-if="canSwitch" text="切换本机 Cursor 账号" placement="bottom" align="end" v-slot="{ id, close }"><button type="button" class="card-icon-action" aria-label="切换" :aria-describedby="id" @click="close(); emit('open', 'switch')"><UiIcon name="switch" :size="15" /></button></UiTooltip>
    <UiPopover v-if="account.capabilities.edit || account.capabilities.authorize || account.capabilities.delete || canGrant" v-model="menuOpen" class="account-actions-menu" label="账号操作" :width="194" align="end" hover>
      <template #trigger="{ id, open, toggle }"><button type="button" class="card-icon-action" aria-label="更多账号操作" aria-haspopup="dialog" :aria-expanded="open" :aria-controls="id" @click="toggle"><UiIcon name="more" :size="16" /></button></template>
      <template #default="{ close }"><div class="account-action-menu-content">
        <button v-if="account.capabilities.edit" type="button" @click="close(); emit('open', 'edit')"><UiIcon name="sliders" :size="15" />编辑资料与标签</button>
        <button v-if="account.capabilities.authorize" type="button" @click="close(); emit('open', 'authorize')"><UiIcon name="key" :size="15" />重新授权</button>
        <button v-if="canGrant" type="button" @click="close(); emit('open', 'grant')"><UiIcon name="users" :size="15" />账号授权</button>
        <button v-if="account.capabilities.delete" type="button" class="danger-text" @click="close(); emit('open', 'delete')"><UiIcon name="trash" :size="15" />删除账号</button>
      </div></template>
    </UiPopover>
  </div>
</template>
