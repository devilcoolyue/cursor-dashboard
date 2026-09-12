import { reactive, watch } from 'vue'

export const cardFields = [
  { group: 'decoration', key: 'ribbon', label: '额度角标', hint: '左上角标记，按综合剩余额度分为四档', compact: true },
  { group: 'decoration', key: 'watermark', label: '燃尽水印', hint: '额度用尽时显示「燃尽了」水印', compact: false },
  { group: 'decoration', key: 'ring', label: '周期环', hint: '右上角显示距下次额度刷新还剩多久', compact: false },
  { group: 'identity', key: 'plan', label: '套餐徽章', hint: '账号名称旁的 Pro / Business 等套餐名', compact: true },
  { group: 'identity', key: 'department', label: '部门标记', hint: '邮箱前的标签；未设标签时显示空间名', compact: true },
  { group: 'identity', key: 'email', label: '邮箱', hint: '隐藏后仍可通过邮箱搜索账号', compact: false },
  { group: 'identity', key: 'limit', label: '额度上限', hint: '各额度名称旁的推算金额；无法推算时显示 —', compact: false },
  { group: 'info', key: 'stats', label: '最后统计', hint: '上一次成功获取账号数据的时间', compact: false },
  { group: 'info', key: 'reset', label: '额度刷新', hint: '下次重置的日期、时间与剩余时长', compact: true },
  { group: 'info', key: 'spend', label: '本周期消费', hint: '已花金额与本周期综合额度上限', compact: true },
  { group: 'info', key: 'onDemand', label: '按量付费', hint: '是否已开启按量付费', compact: false },
  { group: 'info', key: 'grok', label: 'Grok Bot 周额度', hint: '独立周额度，隐藏后仍会正常刷新', compact: false },
] as const
type Field = typeof cardFields[number]['key']
export const cardGroups = [
  { key: 'decoration', label: '卡片装饰', icon: 'palette' },
  { key: 'identity', label: '身份区', icon: 'user' },
  { key: 'info', label: '信息行', icon: 'detail' },
].map(group => ({ ...group, fields: cardFields.filter(field => field.group === group.key) }))
const defaults = Object.fromEntries(cardFields.map(({ key }) => [key, true])) as Record<Field, boolean>
function read() {
  const preferences = { ...defaults }
  try {
    const current = localStorage.getItem('cursor.v2.cards')
    const saved = JSON.parse(current ?? localStorage.getItem('panelCardPrefs') ?? '{}')
    for (const { key } of cardFields) {
      const savedKey = current === null ? ({ ring: 'cycleRing', stats: 'statTime' } as Partial<Record<Field, string>>)[key] ?? key : key
      if (typeof saved?.[savedKey] === 'boolean') preferences[key] = saved[savedKey]
    }
  } catch { /* Display preferences are optional. */ }
  return preferences
}
export const cardDisplay = reactive(read())
export function setCardPreset(preset: 'all' | 'compact' | 'default') {
  for (const field of cardFields) cardDisplay[field.key] = preset === 'compact' ? field.compact : defaults[field.key]
}
watch(cardDisplay, value => { try { localStorage.setItem('cursor.v2.cards', JSON.stringify(value)) } catch { /* Keep session preferences. */ } })
