export const percent = (value: number) => `${Number(value.toFixed(1))}%`
export const quotaTone = (value?: number | null) => value == null ? 'unknown' : value <= 10 ? 'bad' : value <= 30 ? 'warn' : 'ok'
export function cycleRemaining(reset?: string | null, start?: string | null, now = Date.now()) {
  const end = reset ? Date.parse(reset) : NaN
  if (!Number.isFinite(end)) return null
  const hours = Math.max(0, Math.floor((end - now) / 3600000))
  const beginning = start ? Date.parse(start) : NaN
  const passed = Number.isFinite(beginning) && end > beginning ? Math.max(0, Math.min(1, (now - beginning) / (end - beginning))) : 0
  return { number: end <= now ? '已到' : hours < 1 ? '<1' : hours < 48 ? String(hours) : String(Math.floor(hours / 24)), unit: end <= now ? '' : hours < 48 ? '时' : '天', passed }
}
