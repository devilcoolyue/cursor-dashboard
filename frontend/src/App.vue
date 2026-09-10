<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, message, type Schema } from './api'
import { me, activeSpace, workspaceId, selectSpace, clearIdentity, initialize, loadMe, ready, startupError, desktopStatus } from './state'
import { roleText } from './format'
import UiDialog from './components/UiDialog.vue'
import DesktopGate from './components/DesktopGate.vue'
import DesktopStatusBar from './components/DesktopStatusBar.vue'
import { isDesktop } from './platform'
const route = useRoute(), router = useRouter()
const creating = ref(false), teamName = ref(''), busy = ref(false), error = ref(''), menuOpen = ref(false)
const lifetime = new AbortController()
onBeforeUnmount(() => lifetime.abort())
watch(me, value => { if (value && isDesktop && route.meta.public) void router.replace('/accounts'); if (!value) { creating.value = false; teamName.value = ''; error.value = ''; if (!route.meta.public) void router.replace('/login') } })
watch(workspaceId, () => { menuOpen.value = false })
async function logout() {
  busy.value = true; error.value = ''
  try { await api.request('/auth/logout', 'POST'); clearIdentity(); await router.replace('/login') }
  catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
async function createTeam() {
  busy.value = true; error.value = ''
  try { const result = await api.request<Schema['WorkspaceCreated']>('/workspaces', 'POST', { name: teamName.value }, lifetime.signal); creating.value = false; teamName.value = ''; await loadMe(result.id); await router.push('/accounts') }
  catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
function focusMain() { document.getElementById('main-content')?.focus() }
function changeSpace(event: Event) { selectSpace((event.target as HTMLSelectElement).value); void router.push('/accounts') }
</script>
<template>
  <DesktopGate v-if="isDesktop && desktopStatus && desktopStatus.phase !== 'ready'" />
  <div v-else-if="!ready" class="auth-page" role="status">正在连接服务…</div>
  <div v-else-if="startupError" class="auth-page"><h1>暂时无法打开面板</h1><p role="alert" class="error">{{ startupError }}</p><button @click="initialize">重试</button></div>
  <template v-else>
    <div v-if="me && !route.meta.public" class="app-shell">
      <a class="skip-link" href="#main-content" @click.prevent="focusMain">跳到主要内容</a>
      <aside class="sidebar" :class="{ expanded: menuOpen }"><RouterLink class="wordmark" to="/accounts">CURSOR PANEL<span class="brand-dot">.</span></RouterLink><button class="mobile-toggle" :aria-expanded="menuOpen" aria-label="展开导航" @click="menuOpen = !menuOpen">☰</button>
        <div class="sidebar-content"><label class="space-selector"><span>当前空间</span><select :value="workspaceId" @change="changeSpace"><option v-for="space in me.workspaces" :key="space.id" :value="space.id">{{ space.kind === 'personal' ? '个人 · ' : '团队 · ' }}{{ space.name }}</option></select></label>
          <p v-if="activeSpace" class="space-role">{{ roleText(activeSpace.role) }}</p>
          <nav aria-label="主导航" @click="menuOpen = false"><RouterLink to="/accounts"><span aria-hidden="true">▤</span>账号与额度</RouterLink><RouterLink v-if="activeSpace?.kind === 'team' || activeSpace?.capabilities.audit" to="/workspace"><span aria-hidden="true">⚙</span>空间设置</RouterLink><RouterLink to="/settings"><span aria-hidden="true">◯</span>个人设置</RouterLink><RouterLink v-if="me.instance_admin" to="/instance"><span aria-hidden="true">▦</span>实例设置</RouterLink></nav>
          <details v-if="!isDesktop" class="team-options"><summary>团队协作</summary><button @click="creating = true; error = ''">＋ 创建团队空间</button><RouterLink to="/join">接受团队邀请</RouterLink></details>
          <div class="sidebar-bottom"><span class="user-avatar">{{ me.login.slice(0, 1).toUpperCase() }}</span><div><strong>{{ isDesktop ? '本地用户' : me.login }}</strong><span v-if="isDesktop" class="muted">本机个人空间</span><button v-else :disabled="busy" @click="logout">退出登录</button></div></div>
        </div>
      </aside>
      <main id="main-content" class="main-content" tabindex="-1"><div class="topbar"><span>{{ activeSpace?.name }}</span><span class="muted">Cursor · {{ isDesktop ? 'Desktop' : 'Web' }}</span></div><p v-if="error && !creating" role="alert" class="error global-error">{{ error }}</p><DesktopStatusBar v-if="isDesktop" /><RouterView :key="`${me.id}:${workspaceId}:${route.path}`" /></main>
    </div>
    <RouterView v-else />
  </template>
  <UiDialog v-if="creating && me" title="创建团队空间" @close="creating = false"><form class="form-stack" @submit.prevent="createTeam"><p>个人账号不会自动共享到团队。你将成为新空间的所有者。</p><label>空间名称<input v-model="teamName" required maxlength="128" /></label><p v-if="error" role="alert" class="error">{{ error }}</p><div class="actions"><button type="button" @click="creating = false">取消</button><button class="primary" :disabled="busy">创建空间</button></div></form></UiDialog>
</template>
