<script setup lang="ts">
import { ref } from 'vue'
import { api, message, type Schema } from '../api'
import { invitationToken, loadMe, me } from '../state'
const password = ref(''), error = ref(''), busy = ref(false), joined = ref(false)
async function submit() {
  busy.value = true; error.value = ''
  try {
    const result = await api.request<Schema['Joined']>('/invitations/accept', 'POST', { token: invitationToken.value, ...(!me.value ? { password: password.value } : {}) })
    invitationToken.value = ''; password.value = ''; joined.value = true
    if (me.value) await loadMe(result.workspace_id)
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = false; password.value = '' }
}
</script>
<template>
  <main class="auth-page">
    <span class="wordmark">CURSOR PANEL<span class="brand-dot">.</span></span><h1>加入团队空间</h1>
    <template v-if="joined"><p role="status">邀请已接受。{{ me ? '现在可以查看团队空间。' : '请使用受邀邮箱和刚设置的密码登录。' }}</p><RouterLink class="button primary" :to="me ? '/accounts' : '/login'">{{ me ? '进入空间' : '前往登录' }}</RouterLink></template>
    <form v-else class="form-stack" @submit.prevent="submit">
      <p>{{ me ? `当前身份：${me.login}` : '新用户请设置密码。已有账号请先登录，再接受邀请。' }}</p>
      <label>邀请票据<input v-model="invitationToken" type="password" autocomplete="off" required minlength="20" maxlength="128" /></label>
      <label v-if="!me">设置密码<input v-model="password" type="password" autocomplete="new-password" required minlength="12" maxlength="256" /><small>至少 12 个字符。</small></label>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <button class="primary" :disabled="busy">{{ busy ? '正在加入…' : '接受邀请' }}</button>
    </form>
    <RouterLink :to="me ? '/accounts' : '/login'">{{ me ? '返回账号' : '已有账号，前往登录' }}</RouterLink>
  </main>
</template>
