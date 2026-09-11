<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, message } from './api'
import { me, activeSpace, workspaceId, clearIdentity, initialize, ready, startupError, desktopStatus, bootstrap } from './state'
import { cardDisplay, cardFields } from './card-display'
import AppSidebar from './components/AppSidebar.vue'
import UiDialog from './components/UiDialog.vue'
import TeamDialog from './components/TeamDialog.vue'
import DesktopGate from './components/DesktopGate.vue'
import { isDesktop, connectionId } from './platform'
const route = useRoute(), router = useRouter()
const collaborationMode = ref<'create' | 'join'>(), busy = ref(false), error = ref(''), sidebarCollapsed = ref(false)
const displayOpen = ref(false)
watch(me, value => { if (value && isDesktop && route.meta.public && route.path !== '/connections') void router.replace('/accounts'); if (!value) { sidebarCollapsed.value = false; collaborationMode.value = undefined; displayOpen.value = false; error.value = ''; if (!route.meta.public) void router.replace(isDesktop ? '/connections' : '/login') } })
async function logout() {
  busy.value = true; error.value = ''
  try { await api.request('/auth/logout', 'POST'); clearIdentity(); await router.replace('/login') }
  catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
function focusMain() { document.getElementById('main-content')?.focus() }
</script>
<template>
  <DesktopGate v-if="isDesktop && desktopStatus && desktopStatus.phase !== 'ready'" />
  <div v-else-if="!ready" class="auth-page" role="status">正在连接服务…</div>
  <div v-else-if="startupError && route.path !== '/connections'" class="auth-page"><h1>暂时无法打开面板</h1><p role="alert" class="error">{{ startupError }}</p><button @click="initialize">重试</button><RouterLink v-if="isDesktop" to="/connections">管理实例与返回本地</RouterLink></div>
  <template v-else>
    <div v-if="me && (!route.meta.public || route.path === '/connections')" class="app-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
      <a class="skip-link" href="#main-content" @click.prevent="focusMain">跳到主要内容</a>
      <AppSidebar v-model:collapsed="sidebarCollapsed" :busy="busy" @logout="logout" @create-team="collaborationMode = 'create'" @join-team="collaborationMode = 'join'" @display="displayOpen = true" />
      <main id="main-content" class="main-content" tabindex="-1"><div v-if="!['/accounts', '/workspace', '/settings', '/instance'].includes(route.path)" class="topbar"><span>{{ activeSpace?.name }}</span><span class="muted">Cursor · {{ isDesktop ? 'Desktop' : 'Web' }}</span></div><p v-if="error" role="alert" class="error global-error">{{ error }}</p><p v-if="connectionId && bootstrap && !bootstrap.capabilities.remote_switch && route.path === '/accounts'" class="notice">此实例尚未开放远程切换。你可以查看额度并执行获授权的账号操作。</p><RouterView :key="`${connectionId || 'local'}:${me.id}:${workspaceId}:${route.path}`" /></main>
    </div>
    <RouterView v-else />
  </template>
  <UiDialog v-if="displayOpen && me" title="卡片显示项" @close="displayOpen = false"><p class="muted">选择账号卡片上需要展示的信息。</p><div class="display-options"><label v-for="field in cardFields" :key="field.key" class="check-label"><input v-model="cardDisplay[field.key]" type="checkbox" />{{ field.label }}</label></div><div class="actions"><button class="primary" @click="displayOpen = false">完成</button></div></UiDialog>
  <TeamDialog v-if="collaborationMode && me" :mode="collaborationMode" @close="collaborationMode = undefined" />
</template>
