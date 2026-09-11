import { ref, watch } from 'vue'
export const skinOptions = [
  { value: 'classic', label: '经典' }, { value: 'glass', label: '液态玻璃' },
  { value: 'cyberpunk', label: '赛博朋克' }, { value: 'graphite', label: '石墨极简' },
  { value: 'verdant', label: '青野绿意' }, { value: 'blueprint', label: '工程蓝图' },
] as const
const skins = skinOptions.map(option => option.value)
const themes = ['system', 'light', 'dark']
function preference(key: string, choices: string[], fallback: string) {
  try { const value = localStorage.getItem(key) || ''; return choices.includes(value) ? value : fallback } catch { return fallback }
}
export const skin = ref(preference('cursor.v2.skin', skins, 'glass'))
export const theme = ref(preference('cursor.v2.theme', themes, 'dark'))
const media = window.matchMedia('(prefers-color-scheme: dark)')
function apply() {
  document.documentElement.dataset.skin = skin.value
  document.documentElement.dataset.theme = theme.value === 'system' ? (media.matches ? 'dark' : 'light') : theme.value
  try { localStorage.setItem('cursor.v2.skin', skin.value); localStorage.setItem('cursor.v2.theme', theme.value) } catch { /* optional display preferences */ }
}
watch([skin, theme], apply)
media.addEventListener('change', apply)
apply()
