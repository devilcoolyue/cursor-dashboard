import { createRouter, createWebHashHistory } from 'vue-router'
import AccountsView from './views/AccountsView.vue'
import LoginView from './views/LoginView.vue'
import JoinView from './views/JoinView.vue'
import SetupView from './views/SetupView.vue'
import SettingsView from './views/SettingsView.vue'
import WorkspaceView from './views/WorkspaceView.vue'
import InstanceView from './views/InstanceView.vue'
import { invitationToken, me } from './state'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/accounts' },
    { path: '/login', component: LoginView, meta: { public: true } },
    { path: '/join', component: JoinView, meta: { public: true } },
    { path: '/setup', component: SetupView, meta: { public: true } },
    { path: '/accounts', component: AccountsView },
    { path: '/settings', component: SettingsView },
    { path: '/workspace', component: WorkspaceView },
    { path: '/instance', component: InstanceView },
    { path: '/:pathMatch(.*)*', redirect: '/accounts' },
  ],
})
router.beforeEach(to => {
  // Invitation tokens live in the fragment only and are removed from history on arrival.
  if (to.path === '/join' && typeof to.query.token === 'string') {
    invitationToken.value = to.query.token.slice(0, 128)
    return { path: '/join', replace: true }
  }
  if (!me.value && !to.meta.public) return '/login'
  if (me.value && to.path === '/login') return '/accounts'
  if (to.path === '/instance' && !me.value?.instance_admin) return '/accounts'
})
