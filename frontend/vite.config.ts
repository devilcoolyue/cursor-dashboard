import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'

const appVersion = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')).version

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  define: { __APP_VERSION__: JSON.stringify(appVersion), __BUILD_TIME__: JSON.stringify(new Date().toISOString()) },
  clearScreen: false,
  server: {
    fs: { allow: ['.', '../cursor_dashboard/web/css'] },
    ...(mode === 'desktop-probe' ? {} : { proxy: { '/api/v1': { target: 'http://127.0.0.1:8000', changeOrigin: false } } }),
  },
}))
