<script setup lang="ts">
import { onBeforeUnmount, ref, useId } from 'vue'
import { api, message, type Schema } from '../api'
import { invitationToken, loadMe, me } from '../state'
import EntryLayout from '../components/EntryLayout.vue'
import UiIcon from '../components/UiIcon.vue'
const password = ref(''), error = ref(''), busy = ref(false), joined = ref(false)
const showPassword = ref(false), id = useId(), fromLink = !!invitationToken.value
const controller = new AbortController()
onBeforeUnmount(() => { controller.abort(); password.value = '' })
async function submit() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const result = await api.request<Schema['Joined']>('/invitations/accept', 'POST', { token: invitationToken.value, ...(!me.value ? { password: password.value } : {}) }, controller.signal)
    if (controller.signal.aborted) return
    invitationToken.value = ''; password.value = ''; joined.value = true
    if (me.value) await loadMe(result.workspace_id)
  } catch (reason) { if (!controller.signal.aborted) error.value = message(reason) }
  finally { busy.value = false; password.value = ''; showPassword.value = false }
}
</script>
<template>
  <EntryLayout class="join-page" :title="joined ? '邀请已接受' : '加入团队空间'" :description="joined ? '团队邀请已处理，继续进入你的空间。' : '使用管理员提供的邀请，加入团队并访问共享账号。'">
    <div v-if="joined" class="join-success">
      <span class="join-success-icon"><UiIcon name="check" :size="24" /></span>
      <p role="status">{{ me ? '现在可以查看团队空间。' : '请使用受邀邮箱和刚设置的密码登录。' }}</p>
      <RouterLink class="button primary entry-submit" :to="me ? '/accounts' : '/login'">{{ me ? '进入空间' : '前往登录' }}<UiIcon name="chevron" :size="16" /></RouterLink>
    </div>
    <template v-else>
      <div v-if="me" class="join-identity"><UiIcon name="user" :size="16" /><div><span>当前账号</span><strong>{{ me.login }}</strong></div></div>
      <div v-else class="join-login-choice"><span>已有账号？</span><RouterLink to="/login">先登录，再接受邀请<UiIcon name="chevron" :size="13" /></RouterLink></div>
      <form class="form-stack settings-form entry-form" :aria-busy="busy" @submit.prevent="submit">
        <label :for="`${id}-invitation`">邀请票据<input :id="`${id}-invitation`" v-model.trim="invitationToken" :disabled="busy" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" required minlength="20" maxlength="128" placeholder="粘贴管理员提供的邀请票据" :autofocus="!fromLink" :aria-describedby="`${id}-invitation-hint`" /></label>
        <p :id="`${id}-invitation-hint`" class="field-hint">{{ fromLink ? '已从邀请链接填入，可直接继续。' : '通过邀请链接打开此页，会自动填入票据。' }}</p>
        <template v-if="!me">
          <div class="entry-password-field">
            <label :for="`${id}-password`">设置密码</label>
            <div class="entry-password-input"><input :id="`${id}-password`" v-model="password" :disabled="busy" :type="showPassword ? 'text' : 'password'" autocomplete="new-password" required minlength="12" maxlength="256" placeholder="至少 12 个字符" :autofocus="fromLink" :aria-describedby="`${id}-password-hint`" /><button type="button" class="entry-password-toggle" :aria-label="showPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showPassword" :disabled="busy" @click="showPassword = !showPassword"><UiIcon :name="showPassword ? 'eyeOff' : 'eye'" :size="17" /></button></div>
          </div>
          <p :id="`${id}-password-hint`" class="field-hint">为受邀邮箱创建账号，之后使用此密码登录。</p>
        </template>
        <p v-if="error" role="alert" class="error entry-error">{{ error }}</p>
        <button class="primary entry-submit" :disabled="busy"><UiIcon :name="busy ? 'refresh' : 'users'" :size="16" :class="{ spinning: busy }" />{{ busy ? '正在加入…' : '接受邀请' }}</button>
      </form>
      <p class="entry-help">{{ me ? '请确认当前账号与受邀邮箱一致。' : '邀请无效或已过期，请联系团队管理员。' }}</p>
    </template>
    <template #footer><RouterLink :to="me ? '/accounts' : '/login'"><UiIcon name="chevron" class="join-back-icon" :size="13" />{{ me ? '返回账号' : '返回登录' }}</RouterLink></template>
  </EntryLayout>
</template>

<style>
.join-login-choice { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 22px; color: var(--dim); font-size: 12px; }
.join-login-choice > a { display: inline-flex; align-items: center; gap: 4px; }
.join-identity { display: flex; align-items: center; gap: 10px; margin-bottom: 22px; padding: 12px; background: var(--accent-bg); border-radius: var(--radius-ctl); color: var(--accent); }
.join-identity > div { display: grid; gap: 2px; min-width: 0; }
.join-identity span { font-size: 10px; color: var(--dim); }
.join-identity strong { font-size: 12px; color: var(--fg); font-weight: 500; overflow-wrap: anywhere; }
.join-success { display: grid; gap: 18px; text-align: center; padding-top: 2px; animation: enter .2s ease-out; }
.join-success-icon { display: grid; place-items: center; width: 48px; height: 48px; margin: 0 auto; border-radius: 50%; background: var(--accent-bg); color: var(--accent); }
.join-success p { margin: 0; color: var(--dim); font-size: 12px; line-height: 1.9; }
.join-back-icon { transform: rotate(180deg); }
</style>
