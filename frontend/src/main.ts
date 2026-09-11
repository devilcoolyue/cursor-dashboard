import { createApp } from 'vue'

async function start() {
  if (import.meta.env.MODE === 'desktop-probe') {
    document.title = 'Cursor Panel · P0'
    const { default: ProbeApp } = await import('./ProbeApp.vue')
    createApp(ProbeApp).mount('#app')
    return
  }
  await import('./theme')
  await import('./styles.css')
  await import('./sidebar.css')
  await import('./settings.css')
  const [{ default: App }, { initialize }, { router }] = await Promise.all([import('./App.vue'), import('./state'), import('./router')])
  const { isDesktop } = await import('./platform')
  if (!isDesktop) await initialize()
  const app = createApp(App).use(router)
  await router.isReady()
  app.mount('#app')
  if (isDesktop) await initialize()
  if ((await import('./state')).me.value && router.currentRoute.value.path === '/login') await router.replace('/accounts')
}
void start()
