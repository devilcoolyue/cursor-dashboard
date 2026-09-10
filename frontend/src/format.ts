export const timeText = (value?: number | string | null) => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '尚无数据'
export const roleText = (value: string) => ({ owner: '所有者', admin: '管理员', member: '成员', viewer: '只读成员' })[value] || value
export const money = (value: number) => `$${value.toFixed(value > 0 && value < .01 ? 4 : 2)}`
