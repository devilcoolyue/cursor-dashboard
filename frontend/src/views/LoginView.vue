<script setup lang="ts">
import { ref, useId } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError, message, type Schema } from '../api'
import { invitationToken, loadMe, deviceAuthorization } from '../state'
import UiIcon from '../components/UiIcon.vue'
import EntryLayout from '../components/EntryLayout.vue'
const router = useRouter()
const login = ref(''), password = ref(''), busy = ref(false), error = ref('')
const showPassword = ref(false), id = useId()
async function submit() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    api.csrf = (await api.request<Schema['LoginResult']>('/auth/login', 'POST', { login: login.value, password: password.value })).csrf_token
    password.value = ''
    await loadMe()
    await router.replace(deviceAuthorization.value ? '/device' : invitationToken.value ? '/join' : '/accounts')
  } catch (reason) { error.value = reason instanceof ApiError && reason.status === 401 ? '登录邮箱或密码不正确。' : message(reason) }
  finally { password.value = ''; showPassword.value = false; busy.value = false }
}
</script>
<template>
  <EntryLayout title="登录你的空间" :description="deviceAuthorization ? '登录后继续授权桌面客户端。' : invitationToken ? '登录后继续接受团队邀请。' : '查看账号额度，管理个人与团队空间。'">
    <form class="form-stack settings-form entry-form" :aria-busy="busy" @submit.prevent="submit">
      <label :for="`${id}-email`">登录邮箱<input :id="`${id}-email`" v-model="login" :disabled="busy" type="email" autocomplete="username" autocapitalize="none" spellcheck="false" required maxlength="320" placeholder="you@example.com" autofocus :aria-describedby="error ? `${id}-error` : undefined" /></label>
      <div class="entry-password-field">
        <label :for="`${id}-password`">密码</label>
        <div class="entry-password-input"><input :id="`${id}-password`" v-model="password" :disabled="busy" :type="showPassword ? 'text' : 'password'" autocomplete="current-password" required maxlength="256" placeholder="输入密码" :aria-describedby="error ? `${id}-error` : undefined" /><button type="button" class="entry-password-toggle" :aria-label="showPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showPassword" :disabled="busy" @click="showPassword = !showPassword"><UiIcon :name="showPassword ? 'eyeOff' : 'eye'" :size="17" /></button></div>
      </div>
      <p v-if="error" :id="`${id}-error`" role="alert" class="error entry-error">{{ error }}</p>
      <button class="primary entry-submit" :disabled="busy"><UiIcon v-if="busy" name="refresh" :size="16" class="spinning" />{{ busy ? '正在登录…' : '登录' }}<UiIcon v-if="!busy" name="chevron" :size="16" /></button>
    </form>
    <p class="entry-help">忘记密码请联系实例管理员。</p>
    <template #footer><RouterLink to="/join"><UiIcon name="users" :size="14" />接受团队邀请</RouterLink><RouterLink to="/setup">首次使用与初始化<UiIcon name="chevron" :size="13" /></RouterLink></template>
  </EntryLayout>
</template>
