import { createRouter, createWebHashHistory } from 'vue-router'
import AccountsView from './views/AccountsView.vue'
import LoginView from './views/LoginView.vue'
import JoinView from './views/JoinView.vue'
import SetupView from './views/SetupView.vue'
import SettingsView from './views/SettingsView.vue'
import WorkspaceView from './views/WorkspaceView.vue'
import InstanceView from './views/InstanceView.vue'
import { invitationToken, me, deviceAuthorization } from './state'
import { isDesktop } from './platform'
import ConnectionsView from './views/ConnectionsView.vue'
import DeviceView from './views/DeviceView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/accounts' },
    { path: '/login', component: LoginView, meta: { public: true } },
    { path: '/join', component: JoinView, meta: { public: true } },
    { path: '/setup', component: SetupView, meta: { public: true } },
    { path: '/accounts', component: AccountsView },
    { path: '/settings', component: SettingsView },
    { path: '/about', component: () => import('./views/AboutView.vue'), meta: { public: true, help: true } },
    { path: '/workspace', component: WorkspaceView },
    { path: '/instance', component: InstanceView },
    { path: '/connections', component: ConnectionsView, meta: { public: true } },
    { path: '/device', component: DeviceView },
    { path: '/docs/:article?', component: () => import('./views/DocsView.vue'), meta: { public: true, help: true } },
    { path: '/:pathMatch(.*)*', redirect: '/accounts' },
  ],
})
router.beforeEach(to => {
  if (isDesktop && ['/join', '/setup', '/device'].includes(to.path)) return '/accounts'
  if (!isDesktop && to.path === '/connections') return '/accounts'
  if (to.path === '/device' && Object.keys(to.query).length) {
    const keys = ['code_challenge', 'state', 'callback', 'device_id', 'device_name'] as const
    if (keys.every(k => typeof to.query[k] === 'string' && to.query[k]!.length <= 256)) {
      deviceAuthorization.value = Object.fromEntries(keys.map(k => [k, to.query[k]])) as NonNullable<typeof deviceAuthorization.value>
    } else deviceAuthorization.value = undefined
    return { path: '/device', replace: true }
  }
  // Invitation tokens live in the fragment only and are removed from history on arrival.
  if (to.path === '/join' && typeof to.query.token === 'string') {
    invitationToken.value = to.query.token.slice(0, 128)
    return { path: '/join', replace: true }
  }
  if (!me.value && !to.meta.public) return '/login'
  if (me.value && to.path === '/login') return '/accounts'
  if (to.path === '/instance' && !me.value?.instance_admin) return '/accounts'
})
