<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, message, type Schema } from '../api'
import { deviceAuthorization, me } from '../state'
const router = useRouter(), busy = ref(false), error = ref('')
const origin = window.location.origin
async function approve() {
  if (!deviceAuthorization.value) return
  busy.value = true; error.value = ''
  try {
    const result = await api.request<Schema['DeviceApproved']>('/auth/devices/authorize', 'POST', deviceAuthorization.value)
    deviceAuthorization.value = undefined
    window.location.assign(result.callback_url)
  } catch (reason) { error.value = message(reason); busy.value = false }
}
async function cancel() { deviceAuthorization.value = undefined; await router.replace('/accounts') }
</script>
<template><section class="workspace-page"><header class="page-heading"><div><p class="eyebrow">桌面连接授权</p><h1>连接 Cursor Panel 桌面</h1></div></header>
  <template v-if="deviceAuthorization"><p>允许 <strong>{{ deviceAuthorization.device_name }}</strong> 以 <strong>{{ me?.login }}</strong> 的身份连接此实例。</p><p class="muted">{{ origin }}</p><p>桌面将获得与你相同的空间权限。你可以随时在个人设置中撤销这台设备；设备登录最长保留 30 天。</p><div class="actions"><button :disabled="busy" @click="cancel">取消</button><button class="primary" :disabled="busy" @click="approve">{{ busy ? '正在连接…' : '允许连接此设备' }}</button></div></template>
  <p v-else class="notice">授权请求已失效。请回到桌面应用，重新发起浏览器登录。</p><p v-if="error" role="alert" class="error">{{ error }}</p>
</section></template>
