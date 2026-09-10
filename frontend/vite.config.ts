import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  clearScreen: false,
  server: {
    fs: { allow: ['.', '../cursor_dashboard/web/css'] },
    ...(mode === 'desktop-probe' ? {} : { proxy: { '/api/v1': { target: 'http://127.0.0.1:8000', changeOrigin: false } } }),
  },
}))
