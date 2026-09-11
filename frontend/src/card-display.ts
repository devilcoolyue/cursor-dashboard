import { reactive, watch } from 'vue'

export const cardFields = [
  { key: 'ribbon', label: '额度角标' }, { key: 'ring', label: '续期圆环' },
  { key: 'watermark', label: '燃尽水印' }, { key: 'stats', label: '最后统计' },
  { key: 'reset', label: '额度刷新时间' }, { key: 'spend', label: '本周期消费' },
  { key: 'onDemand', label: '按量付费' }, { key: 'grok', label: 'Grok Bot 周额度' },
] as const
type Field = typeof cardFields[number]['key']
const defaults: Record<Field, boolean> = { ribbon: true, ring: true, watermark: true, stats: true, reset: true, spend: true, onDemand: true, grok: true }
function read() {
  try {
    const saved = JSON.parse(localStorage.getItem('cursor.v2.cards') || '{}')
    for (const { key } of cardFields) if (typeof saved?.[key] === 'boolean') defaults[key] = saved[key]
  } catch { /* Display preferences are optional. */ }
  return defaults
}
export const cardDisplay = reactive(read())
watch(cardDisplay, value => { try { localStorage.setItem('cursor.v2.cards', JSON.stringify(value)) } catch { /* Keep session preferences. */ } })
