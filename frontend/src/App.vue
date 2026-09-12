<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, message } from './api'
import { me, activeSpace, workspaceId, clearIdentity, initialize, ready, startupError, desktopStatus, bootstrap } from './state'
import AppSidebar from './components/AppSidebar.vue'
import CardDisplayDialog from './components/CardDisplayDialog.vue'
import TeamDialog from './components/TeamDialog.vue'
import DesktopGate from './components/DesktopGate.vue'
import StartupScreen from './components/StartupScreen.vue'
import GuideDialog from './components/GuideDialog.vue'
import UiIcon from './components/UiIcon.vue'
import { guideOpen, guideProgress, guideSteps, openGuide, dismissGuide } from './help/onboarding'
import { isDesktop, connectionId } from './platform'
import { startAutomaticUpdateChecks } from './updates'
const route = useRoute(), router = useRouter()
const collaborationMode = ref<'create' | 'join'>(), busy = ref(false), error = ref(''), sidebarCollapsed = ref(false)
const sidebarMobileOpen = ref(false)
const displayOpen = ref(false)
let stopUpdateChecks: (() => void) | undefined
watch(() => isDesktop || !!me.value, enabled => {
  stopUpdateChecks?.()
  stopUpdateChecks = enabled ? startAutomaticUpdateChecks() : undefined
}, { immediate: true })
onBeforeUnmount(() => stopUpdateChecks?.())
watch(me, value => { if (value && isDesktop && route.meta.public && !route.meta.help && route.path !== '/connections') void router.replace('/accounts'); if (!value) { sidebarCollapsed.value = false; collaborationMode.value = undefined; displayOpen.value = false; guideOpen.value = false; error.value = ''; if (!route.meta.public) void router.replace(isDesktop ? '/connections' : '/login') } })
watch([ready, me, desktopStatus, () => route.path], () => {
  if (isDesktop && ready.value && !startupError.value && desktopStatus.value?.phase === 'ready'
    && me.value && !connectionId.value && route.path === '/accounts' && !guideProgress.seen) openGuide()
}, { immediate: true, flush: 'post' })
async function logout() {
  busy.value = true; error.value = ''
  try { await api.request('/auth/logout', 'POST'); clearIdentity(); await router.replace('/login') }
  catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
function focusMain() { document.getElementById('main-content')?.focus() }
</script>
<template>
  <DesktopGate v-if="!route.meta.help && isDesktop && desktopStatus && desktopStatus.phase !== 'ready'" />
  <StartupScreen v-else-if="!ready && !route.meta.help" />
  <StartupScreen v-else-if="startupError && route.path !== '/connections' && !route.meta.help" :busy="false" title="暂时无法打开面板" :description="startupError"><button @click="initialize">重试</button><RouterLink v-if="isDesktop" to="/connections">管理实例与返回本地</RouterLink><RouterLink to="/docs/troubleshooting">查看排查文档</RouterLink></StartupScreen>
  <template v-else>
    <div v-if="me && (!route.meta.public || route.meta.help || route.path === '/connections')" class="app-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
      <a class="skip-link" href="#main-content" :inert="sidebarMobileOpen" @click.prevent="focusMain">跳到主要内容</a>
      <AppSidebar v-model:collapsed="sidebarCollapsed" v-model:mobile-open="sidebarMobileOpen" :busy="busy" @logout="logout" @create-team="collaborationMode = 'create'" @join-team="collaborationMode = 'join'" @display="displayOpen = true" />
      <main id="main-content" class="main-content" tabindex="-1" :inert="sidebarMobileOpen"><div v-if="!route.meta.help && !['/accounts', '/workspace', '/settings', '/instance', '/connections'].includes(route.path)" class="topbar"><span>{{ activeSpace?.name }}</span><span class="muted">Cursor · {{ isDesktop ? 'Desktop' : 'Web' }}</span></div><p v-if="error" role="alert" class="error global-error">{{ error }}</p><p v-if="connectionId && bootstrap && !bootstrap.capabilities.remote_switch && route.path === '/accounts'" class="notice">此实例尚未开放远程切换。你可以查看额度并执行获授权的账号操作。</p><RouterView :key="`${connectionId || 'local'}:${me.id}:${workspaceId}:${route.meta.help ? 'docs' : route.path}`" /></main>
    </div>
    <RouterView v-else />
  </template>
  <CardDisplayDialog v-if="displayOpen && me" @close="displayOpen = false" />
  <TeamDialog v-if="collaborationMode && me" :mode="collaborationMode" @close="collaborationMode = undefined" />
  <GuideDialog v-if="guideOpen && (route.meta.help || (ready && !startupError && (!isDesktop || desktopStatus?.phase === 'ready')))" />
  <aside v-else-if="guideProgress.active && ready && (route.meta.help || (me && !startupError && (!isDesktop || desktopStatus?.phase === 'ready')))" class="guide-resume" aria-label="继续新手指引" :inert="sidebarMobileOpen">
    <button @click="openGuide"><UiIcon name="compass" :size="18" /><span><strong>继续新手指引</strong><small>{{ guideProgress.step + 1 }} / 6 · {{ guideSteps[guideProgress.step]?.title }}</small></span><UiIcon name="chevron" :size="14" /></button><button class="icon-button" aria-label="结束本次指引" @click="dismissGuide"><UiIcon name="close" :size="15" /></button>
  </aside>
</template>
