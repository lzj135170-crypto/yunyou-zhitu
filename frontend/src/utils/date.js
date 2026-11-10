export const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

export function pad2(value) {
  return String(value).padStart(2, '0')
}

export function toISODate(date) {
  if (!date) return ''
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

export function parseDateOnly(value) {
  if (!value) return null
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return null
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return Number.isNaN(date.getTime()) ? null : date
}

export function addDays(value, days) {
  const date = parseDateOnly(value)
  if (!date) return null
  date.setDate(date.getDate() + days)
  return date
}

export function todayISO() {
  return toISODate(new Date())
}

export function defaultEndDate() {
  const date = new Date()
  date.setDate(date.getDate() + 2)
  return toISODate(date)
}

export function formatDateTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(
    date.getHours()
  )}:${pad2(date.getMinutes())}`
}

export function formatMonthDay(value) {
  if (!value) return '—'
  const date = parseDateOnly(value)
  if (!date) return String(value)
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`
}

export function weekday(value) {
  const date = parseDateOnly(value)
  return date ? WEEKDAYS[date.getDay()] : ''
}

export function dayDate(dayNumber, meta = {}, plan = null) {
  if (meta.startDate) {
    const date = addDays(meta.startDate, dayNumber - 1)
    if (date) return `${date.getMonth() + 1} 月 ${date.getDate()} 日`
  }
  const weatherDays = plan && plan.weather ? plan.weather.days : []
  if (weatherDays && weatherDays[dayNumber - 1] && weatherDays[dayNumber - 1].date) {
    return formatMonthDay(weatherDays[dayNumber - 1].date)
  }
  return ''
}
