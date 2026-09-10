<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError, message, type Schema } from '../api'
import { invitationToken, loadMe } from '../state'
const router = useRouter()
const login = ref(''), password = ref(''), busy = ref(false), error = ref('')
async function submit() {
  busy.value = true; error.value = ''
  try {
    api.csrf = (await api.request<Schema['LoginResult']>('/auth/login', 'POST', { login: login.value, password: password.value })).csrf_token
    password.value = ''
    await loadMe()
    await router.replace(invitationToken.value ? '/join' : '/accounts')
  } catch (reason) { error.value = reason instanceof ApiError && reason.status === 401 ? '登录邮箱或密码不正确。' : message(reason) }
  finally { password.value = ''; busy.value = false }
}
</script>
<template>
  <main class="auth-page">
    <div class="auth-intro"><span class="wordmark">CURSOR PANEL<span class="brand-dot">.</span></span><p>账号与额度</p><h1>登录你的空间</h1><p>个人账号独立管理，团队账号按授权共享。</p></div>
    <form class="form-stack" @submit.prevent="submit">
      <label>登录邮箱<input v-model="login" type="email" autocomplete="username" required maxlength="320" autofocus /></label>
      <label>密码<input v-model="password" type="password" autocomplete="current-password" required maxlength="256" /></label>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <button class="primary" :disabled="busy">{{ busy ? '正在登录…' : '登录' }}</button>
    </form>
    <div class="auth-links"><RouterLink to="/join">接受团队邀请</RouterLink><RouterLink to="/setup">首次使用与初始化</RouterLink></div>
  </main>
</template>
