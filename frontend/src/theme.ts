import { ref, watch } from 'vue'
const skins = ['classic', 'glass', 'cyberpunk', 'graphite', 'verdant', 'blueprint']
const themes = ['system', 'light', 'dark']
function preference(key: string, choices: string[], fallback: string) {
  try { const value = localStorage.getItem(key) || ''; return choices.includes(value) ? value : fallback } catch { return fallback }
}
export const skin = ref(preference('cursor.v2.skin', skins, 'classic'))
export const theme = ref(preference('cursor.v2.theme', themes, 'system'))
const media = window.matchMedia('(prefers-color-scheme: dark)')
function apply() {
  document.documentElement.dataset.skin = skin.value
  document.documentElement.dataset.theme = theme.value === 'system' ? (media.matches ? 'dark' : 'light') : theme.value
  try { localStorage.setItem('cursor.v2.skin', skin.value); localStorage.setItem('cursor.v2.theme', theme.value) } catch { /* optional display preferences */ }
}
watch([skin, theme], apply)
media.addEventListener('change', apply)
apply()
