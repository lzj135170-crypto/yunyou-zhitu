export function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function formatDistance(meters) {
  const value = Number(meters)
  if (!Number.isFinite(value) || value <= 0) return '—'
  if (value < 1000) return `${Math.round(value)} m`
  return `${(value / 1000).toFixed(1)} km`
}

export function formatDuration(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value <= 0) return '—'
  const minutes = Math.round(value / 60)
  if (minutes < 60) return `${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`
}

export function formatCost(yuan, isEstimate) {
  const value = Number(yuan)
  if (!Number.isFinite(value) || value <= 0) return '免费'
  const text = `¥${value.toFixed(value < 10 ? 1 : 0)}`
  return isEstimate ? `${text}（估算）` : text
}

export function formatTemp(value) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? `${Math.round(number)}°` : '—'
}

export function formatHours(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) return ''
  const rounded = Math.round(number * 10) / 10
  return `${rounded} 小时`
}

export function formatAvgCost(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0) return String(value || '')
  return `¥${Math.round(number)}`
}

export function mapLabel(index) {
  return String.fromCharCode(65 + (index % 26))
}

export function isHttpUrl(value) {
  return typeof value === 'string' && /^https?:\/\//i.test(value)
}

export function amapMarkerUrl(place) {
  const longitude = Number(place && place.longitude)
  const latitude = Number(place && place.latitude)
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return ''
  const name = encodeURIComponent((place && place.name) || '餐厅')
  return (
    'https://uri.amap.com/marker?position=' +
    `${longitude},${latitude}` +
    `&name=${name}&coordinate=gaode&callnative=1&src=travel`
  )
}
