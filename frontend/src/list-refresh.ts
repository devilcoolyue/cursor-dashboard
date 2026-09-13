import { ref, watch } from 'vue'

export const refreshIntervals = [10, 30, 60] as const
export type RefreshInterval = typeof refreshIntervals[number]
const key = 'cursor.v2.list-refresh'
function read(): RefreshInterval {
  try {
    const saved = Number(localStorage.getItem(key))
    if (refreshIntervals.includes(saved as RefreshInterval)) return saved as RefreshInterval
  } catch {}
  return 10
}
export const listRefreshSeconds = ref<RefreshInterval>(read())
watch(listRefreshSeconds, value => {
  try { localStorage.setItem(key, String(value)) } catch {}
})
