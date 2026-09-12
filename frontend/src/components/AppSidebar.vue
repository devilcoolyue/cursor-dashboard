<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { activeConnection, activeSpace, me, selectSpace, workspaceId } from '../state'
import { connectionId, isDesktop } from '../platform'
import { sidebarAccounts } from '../sidebar-state'
import { roleText } from '../format'
import { skin, skinOptions, theme } from '../theme'
import UiIcon from './UiIcon.vue'
import UiSelect from './UiSelect.vue'
import UiPopover from './UiPopover.vue'
import AccountTagNavigation from './AccountTagNavigation.vue'
import DesktopStatusBar from './DesktopStatusBar.vue'
import VersionBadge from './VersionBadge.vue'
import brandIcon from '../icon.svg'
import { openGuide } from '../help/onboarding'
import { appVersion } from '../version'
import { restoreFocus } from '../input-modality'

defineProps<{ busy: boolean }>()
const emit = defineEmits<{ logout: []; createTeam: []; joinTeam: []; display: [] }>()
const collapsed = defineModel<boolean>('collapsed', { default: false })
const mobileOpen = defineModel<boolean>('mobileOpen', { default: false })
const route = useRoute(), router = useRouter()
const accountExpanded = ref(true)
const preferencesOpen = ref(false), themesOpen = ref(false), teamOpen = ref(false), userOpen = ref(false), settingsOpen = ref(false)
const helpOpen = ref(false)
const versionOpen = ref(false)
const sidebar = ref<HTMLElement>(), viewportHeight = ref(innerHeight), mobile = ref(innerWidth <= 760)
const availableHeight = computed(() => viewportHeight.value - (mobile.value ? 56 : 0))
// Include the persistent help row in the vertical navigation budget.
const compact = computed(() => availableHeight.value < 840), minimal = computed(() => availableHeight.value < 680), tiny = computed(() => availableHeight.value < 520)
const skinName = computed(() => skinOptions.find(option => option.value === skin.value)?.label || '液态玻璃')
const extraSkin = computed(() => !['classic', 'glass'].includes(skin.value))
const settings = computed(() => [
  ...(activeSpace.value?.kind === 'team' || activeSpace.value?.capabilities.audit ? [{ to: '/workspace', label: '空间设置', icon: 'sliders' }] : []),
  { to: '/settings', label: '个人设置', icon: 'user' },
  ...(me.value?.instance_admin ? [{ to: '/instance', label: '实例设置', icon: 'server' }] : []),
])
const now = ref(Date.now())
const updated = computed(() => {
  if (!sidebarAccounts.loadedAt) return '尚未载入账号'
  const minutes = Math.max(0, Math.floor((now.value - sidebarAccounts.loadedAt) / 60000))
  return minutes < 1 ? '刚刚载入' : minutes < 60 ? `${minutes} 分钟前载入` : `${Math.floor(minutes / 60)} 小时前载入`
})
let clockTimer: ReturnType<typeof setInterval> | undefined, hoverTimer: ReturnType<typeof setTimeout> | undefined
let headerObserver: ResizeObserver | undefined
function observeHeader() {
  headerObserver?.disconnect()
  const header = document.querySelector<HTMLElement>('.main-content .topbar')
  if (!header) { sidebar.value?.style.removeProperty('--workspace-header-height'); return }
  const sync = () => {
    // Replacing a workspace unmounts its toolbar; a detached element reports zero height.
    if (!header.isConnected) return
    const height = Math.ceil(header.getBoundingClientRect().height)
    if (height > 0) sidebar.value?.style.setProperty('--workspace-header-height', `${height}px`)
  }
  sync()
  headerObserver = new ResizeObserver(sync)
  headerObserver.observe(header)
}
watch([() => route.path, workspaceId, () => me.value?.id, connectionId], async () => { await nextTick(); observeHeader() }, { flush: 'post' })
let themesPinned = false
let themeFocus: 'first' | 'last' | undefined
function cancelThemeClose() { clearTimeout(hoverTimer) }
function dimensions() { viewportHeight.value = window.visualViewport?.height || innerHeight; mobile.value = innerWidth <= 760 }
function closePanels() { preferencesOpen.value = false; themesOpen.value = false; teamOpen.value = false; userOpen.value = false; settingsOpen.value = false; helpOpen.value = false; versionOpen.value = false; themesPinned = false; clearTimeout(hoverTimer) }
function startGuide() {
  navigate()
  sidebar.value?.querySelector<HTMLButtonElement>(mobile.value ? '.mobile-toggle' : '[title="帮助与文档"]')?.focus({ preventScroll: true })
  openGuide()
}
function navigate() { mobileOpen.value = false; closePanels() }
function openCollaboration(mode: 'create' | 'join') {
  navigate()
  sidebar.value?.querySelector<HTMLButtonElement>(mobile.value ? '.mobile-toggle' : '[title="团队协作"]')?.focus({ preventScroll: true })
  if (mode === 'create') emit('createTeam')
  else emit('joinTeam')
}
function openCardDisplay() {
  navigate()
  if (mobile.value) sidebar.value?.querySelector<HTMLElement>('.mobile-toggle')?.focus({ preventScroll: true })
  emit('display')
}
function changeSpace(id: string) { selectSpace(id); navigate(); accountExpanded.value = true; void router.push('/accounts') }
function toggleCollapse() { collapsed.value = !collapsed.value; closePanels() }
function navigationKeydown(event: KeyboardEvent) {
  if (!mobile.value || !mobileOpen.value || event.defaultPrevented) return
  if (event.key === 'Escape') { event.preventDefault(); mobileOpen.value = false; return }
  if (event.key !== 'Tab') return
  const controls = [...(sidebar.value?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), [tabindex="0"]') || [])]
    .filter(element => element.tabIndex >= 0 && !element.closest('[inert]') && element.getClientRects().length && getComputedStyle(element).visibility === 'visible')
  const first = controls[0], last = controls[controls.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus({ preventScroll: true }) }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus({ preventScroll: true }) }
}
function selectTheme(value: string) { skin.value = value; themesOpen.value = false; themesPinned = false; clearTimeout(hoverTimer) }
function hoverThemes(event: PointerEvent) { if (event.pointerType === 'mouse') { clearTimeout(hoverTimer); themesOpen.value = true } }
function leaveThemes(event: PointerEvent) { if (event.pointerType === 'mouse' && !themesPinned) hoverTimer = setTimeout(() => { themesOpen.value = false }, 180) }
function clickThemes() { themesPinned = !themesPinned; themesOpen.value = themesPinned }
function keyThemes(event: KeyboardEvent) {
  const container = event.currentTarget as HTMLElement
  const buttons = [...container.querySelectorAll<HTMLButtonElement>('.theme-option')]
  if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault()
    const index = buttons.indexOf(event.target as HTMLButtonElement)
    buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus()
  }
}
function focusTheme() {
  if (!themeFocus) return
  const buttons = sidebar.value?.querySelectorAll<HTMLButtonElement>('.theme-option')
  buttons?.[themeFocus === 'last' ? buttons.length - 1 : 0]?.focus()
  themeFocus = undefined
}
function openThemesKeyboard(event: KeyboardEvent) {
  if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return
  event.preventDefault(); themesPinned = true; themeFocus = event.key === 'ArrowUp' ? 'last' : 'first'
  if (themesOpen.value) focusTheme()
  else themesOpen.value = true
}
watch(() => route.path, () => { navigate(); if (route.path === '/accounts') accountExpanded.value = true })
watch([workspaceId, () => me.value?.id, connectionId], navigate)
watch(preferencesOpen, value => { if (!value) { themesOpen.value = false; themesPinned = false } })
watch(themesOpen, value => { if (!value) { themesPinned = false; themeFocus = undefined; clearTimeout(hoverTimer) } })
watch(mobile, () => { mobileOpen.value = false; closePanels() })
watch(mobileOpen, async value => {
  if (!value) {
    closePanels()
    await nextTick()
    if (mobile.value && !mobileOpen.value && !document.querySelector('dialog[open]')) restoreFocus(() => sidebar.value?.querySelector<HTMLElement>('.mobile-toggle')?.focus({ preventScroll: true }))
    return
  }
  closePanels()
  await nextTick()
  if (mobileOpen.value) sidebar.value?.querySelector<HTMLElement>('.space-selector button')?.focus({ preventScroll: true })
})
onMounted(() => { void nextTick(observeHeader); dimensions(); window.addEventListener('resize', dimensions); window.visualViewport?.addEventListener('resize', dimensions); clockTimer = setInterval(() => { now.value = Date.now() }, 30000) })
onBeforeUnmount(() => { mobileOpen.value = false; headerObserver?.disconnect(); window.removeEventListener('resize', dimensions); window.visualViewport?.removeEventListener('resize', dimensions); clearInterval(clockTimer); clearTimeout(hoverTimer) })
</script>
<template>
  <aside v-if="me" ref="sidebar" class="sidebar" :class="{ expanded: mobileOpen, collapsed: collapsed && !mobile, 'density-compact': compact, 'density-minimal': minimal, 'density-tiny': tiny }" :style="{ '--sidebar-available-height': `${availableHeight}px` }" aria-label="侧栏导航" @keydown="navigationKeydown">
    <div class="sidebar-brand"><RouterLink class="brand-link" to="/accounts" aria-label="Cursor 额度首页" @click="navigate"><img :src="brandIcon" alt="" width="32" height="32" /><span class="sidebar-brand-name">Cursor 额度</span></RouterLink><VersionBadge v-model="versionOpen" /><span class="live-dot" role="img" aria-label="已连接" /><button class="mobile-toggle" :aria-expanded="mobileOpen" :aria-label="mobileOpen ? '收起导航' : '展开导航'" aria-controls="sidebar-content" @click="mobileOpen = !mobileOpen"><UiIcon :name="mobileOpen ? 'close' : 'menu'" /></button></div>
    <button type="button" class="sidebar-backdrop" aria-label="关闭导航遮罩" tabindex="-1" :aria-hidden="!mobileOpen" :inert="!mobileOpen" @click="mobileOpen = false" />
    <div id="sidebar-content" class="sidebar-content" :inert="mobile && !mobileOpen">
      <RouterLink v-if="isDesktop" to="/connections" class="connection-selector" :title="`${activeConnection?.name || '本地'} · 切换实例`" @click="navigate"><UiIcon name="building" /><span>{{ activeConnection?.name || '本地' }} · 切换实例</span></RouterLink>
      <div class="space-selector">
        <UiSelect variant="workspace" aria-label="当前空间" :model-value="workspaceId"
          :options="me.workspaces.map(space => ({ value: space.id, label: `${space.kind === 'personal' ? '个人' : '团队'} · ${space.name}`, name: space.name, icon: space.kind === 'team' ? 'users' : 'user', description: `${space.kind === 'team' ? '团队空间' : '个人空间'} · ${roleText(space.role)}` }))"
          @update:model-value="changeSpace" />
      </div>
      <nav aria-label="主导航" class="sidebar-navigation">
        <p class="sidebar-section-label">工作空间</p>
        <div class="account-nav-row" :class="{ active: route.path === '/accounts' }"><RouterLink to="/accounts" class="sidebar-nav-item" title="账号与额度" @click="accountExpanded = true; navigate()"><UiIcon name="grid" :size="18" /><span class="sidebar-item-label">账号与额度</span></RouterLink><button v-if="route.path === '/accounts'" type="button" class="account-nav-toggle" :aria-expanded="accountExpanded" :aria-label="accountExpanded ? '收起标签筛选' : '展开标签筛选'" aria-controls="sidebar-account-tags" @click="accountExpanded = !accountExpanded"><UiIcon name="chevronDown" :size="14" /></button></div>
        <div v-if="route.path === '/accounts'" id="sidebar-account-tags" v-show="accountExpanded"><AccountTagNavigation @selected="mobileOpen = false" /></div>
        <UiPopover v-if="!isDesktop" v-model="teamOpen" label="团队协作" placement="right" :width="280">
          <template #trigger="{ toggle, open, id }"><button type="button" class="sidebar-nav-item" title="团队协作" :aria-expanded="open" :aria-controls="id" aria-haspopup="dialog" @click="toggle"><UiIcon name="users" :size="18" /><span class="sidebar-item-label">团队协作</span></button></template>
          <template #default><div class="sidebar-menu team-menu"><h2>团队协作</h2><button type="button" aria-label="创建团队空间" @click="openCollaboration('create')"><span class="team-menu-icon"><UiIcon name="users" :size="18" /></span><span><strong>创建团队空间</strong><small>邀请成员，共享账号与额度</small></span><UiIcon name="chevron" :size="14" /></button><button type="button" aria-label="接受团队邀请" @click="openCollaboration('join')"><span class="team-menu-icon"><UiIcon name="mail" :size="18" /></span><span><strong>接受团队邀请</strong><small>通过邀请链接加入已有团队</small></span><UiIcon name="chevron" :size="14" /></button></div></template>
        </UiPopover>
        <div class="sidebar-settings-links"><p class="sidebar-section-label">设置</p><RouterLink v-for="item in settings" :key="item.to" :to="item.to" class="sidebar-nav-item" :title="item.label" @click="navigate"><UiIcon :name="item.icon" :size="18" /><span class="sidebar-item-label">{{ item.label }}</span></RouterLink></div>
        <UiPopover v-model="settingsOpen" label="设置导航" placement="right" class="sidebar-settings-compact"><template #trigger="{ toggle, open, id }"><button type="button" class="sidebar-nav-item" :class="{ 'router-link-active': settings.some(item => item.to === route.path) }" :aria-expanded="open" :aria-controls="id" title="设置" @click="toggle"><UiIcon name="sliders" :size="18" /><span class="sidebar-item-label">设置</span><UiIcon name="chevron" :size="14" class="sidebar-trailing" /></button></template><template #default><div class="sidebar-menu"><h2>设置</h2><RouterLink v-for="item in settings" :key="item.to" :to="item.to" @click="navigate"><UiIcon :name="item.icon" />{{ item.label }}</RouterLink></div></template></UiPopover>
      </nav>
      <footer class="sidebar-footer">
        <UiPopover v-model="helpOpen" class="sidebar-help" label="帮助与文档" placement="top" :width="260" focus-on-open>
          <template #trigger="{ toggle, open, id }"><button class="sidebar-nav-item" title="帮助与文档" aria-label="帮助与文档" :aria-expanded="open" :aria-controls="id" aria-haspopup="dialog" @click="toggle"><UiIcon name="help" :size="18" /><span class="sidebar-item-label">帮助与文档</span><UiIcon name="chevronDown" :size="13" class="sidebar-trailing sidebar-menu-chevron" /></button></template>
          <template #default><div class="sidebar-menu"><h2>帮助与文档</h2><button @click="startGuide"><UiIcon name="compass" :size="16" />新手指引</button><RouterLink to="/docs/overview" @click="navigate"><UiIcon name="book" :size="16" />使用文档</RouterLink><RouterLink to="/about" @click="navigate"><UiIcon name="info" :size="16" />关于与更新 · v{{ appVersion }}</RouterLink></div></template>
        </UiPopover>
        <DesktopStatusBar v-if="isDesktop" :updated="updated" />
        <div v-else class="sidebar-update" :title="updated"><span class="sidebar-icon-slot"><UiIcon name="refresh" :size="13" /></span><span>{{ updated }}</span></div>
        <UiPopover v-model="preferencesOpen" label="显示偏好" placement="top" :width="264">
          <template #trigger="{ toggle, open, id }"><button type="button" class="sidebar-nav-item" :title="`显示偏好：${skinName}`" aria-label="显示偏好" :aria-expanded="open" :aria-controls="id" aria-haspopup="dialog" @click="toggle"><UiIcon name="palette" :size="18" /><span class="sidebar-item-label">显示偏好</span><small class="sidebar-current-skin" :class="{ custom: extraSkin }">{{ skinName }}</small><UiIcon name="chevronDown" :size="13" class="sidebar-trailing sidebar-menu-chevron" /></button></template>
          <template #default="{ close }"><div class="sidebar-preferences"><header class="sidebar-popover-header"><strong>显示偏好</strong><button type="button" class="popover-close" aria-label="关闭显示偏好" @click="close"><UiIcon name="close" :size="15" /></button></header>
            <div class="preference-label"><span>界面风格</span><span class="selected-skin-name" aria-live="polite">{{ skinName }}</span></div>
            <div class="segmented skin-switch" role="group" aria-label="界面风格"><button v-for="option in skinOptions.slice(0, 2)" :key="option.value" :aria-pressed="skin === option.value" @click="selectTheme(option.value)"><i class="skin-swatch" :class="option.value" />{{ option.label }}</button><span class="extra-themes" @pointerenter="cancelThemeClose" @pointerleave="leaveThemes"><UiPopover v-model="themesOpen" label="更多界面风格" placement="top" :width="240" @shown="focusTheme"><template #trigger="{ open, id }"><button type="button" class="more-themes-button" :class="{ selected: extraSkin }" aria-label="更多界面风格" :aria-expanded="open" :aria-controls="id" @pointerenter="hoverThemes" @click="clickThemes" @keydown="openThemesKeyboard"><UiIcon :name="open ? 'chevronDown' : 'chevron'" :size="14" /><i v-if="extraSkin" class="more-themes-dot" /></button></template><template #default="{ close: closeThemes }"><div class="theme-options" @keydown="keyThemes" @pointerenter="cancelThemeClose"><button v-for="option in skinOptions.slice(2)" :key="option.value" class="theme-option" :aria-pressed="skin === option.value" @click="selectTheme(option.value); closeThemes()"><i class="skin-swatch" :class="option.value" /><span>{{ option.label }}</span><UiIcon name="check" :size="15" /></button></div></template></UiPopover></span></div>
            <div class="preference-label">明暗模式</div><div class="segmented" role="group" aria-label="明暗模式"><button :aria-pressed="theme === 'light'" @click="theme = 'light'"><UiIcon name="sun" :size="13" />浅色</button><button :aria-pressed="theme === 'dark'" @click="theme = 'dark'"><UiIcon name="moon" :size="13" />深色</button><button :aria-pressed="theme === 'system'" @click="theme = 'system'"><UiIcon name="monitor" :size="13" />自动</button></div>
            <div class="card-display-entry"><button type="button" class="card-display-button" @click="close(); openCardDisplay()"><UiIcon name="panels" :size="15" />卡片显示项<UiIcon name="chevron" :size="13" /></button></div>
          </div></template>
        </UiPopover>
        <div class="sidebar-footer-end">
          <button class="sidebar-nav-item sidebar-collapse" :aria-label="collapsed && !mobile ? '展开侧栏' : mobile ? '收起导航' : '收起侧栏'" :title="collapsed ? '展开侧栏' : '收起侧栏'" @click="mobile ? mobileOpen = false : toggleCollapse()"><UiIcon name="panel" :size="18" /><span class="sidebar-item-label">{{ mobile ? '收起导航' : '收起侧栏' }}</span></button>
          <UiPopover v-model="userOpen" :label="isDesktop ? '当前用户' : '登录账号'" placement="top">
            <template #trigger="{ toggle, open, id }"><button type="button" class="sidebar-user-trigger" :title="isDesktop && !connectionId ? '本地用户' : me.login" :aria-expanded="open" :aria-controls="id" :aria-label="isDesktop ? '当前用户' : '登录账号'" @click="toggle"><UiIcon name="user" :size="16" /></button></template>
            <template #default><div class="sidebar-menu"><h2>{{ isDesktop && !connectionId ? '本地用户' : me.login }}</h2><span v-if="isDesktop" class="muted">{{ activeConnection?.name || '本机个人空间' }}</span><button v-else :disabled="busy" @click="emit('logout')"><UiIcon name="logout" />退出登录</button></div></template>
          </UiPopover>
        </div>
      </footer>
    </div>
  </aside>
</template>
