// 路线模式的唯一来源：标签、AMap 折线颜色和界面色点都从这里取。
// 颜色偏深，是为了在高德亮色底图上保持可读；界面里只作为小色点和图例。
export const ROUTE_MODES = {
  walking: { label: '步行', color: '#1F6F5C' },
  bicycling: { label: '共享单车', color: '#5A7D4E' },
  driving: { label: '打车', color: '#B07A35' },
  transit: { label: '公交地铁', color: '#2A7388' },
}

export function routeMode(mode) {
  return ROUTE_MODES[mode] || null
}

export function routeModeLabel(mode, fallback) {
  const item = routeMode(mode)
  return (item && item.label) || fallback || mode || '—'
}

export function routeModeColor(mode, fallback = '#666666') {
  const item = routeMode(mode)
  return (item && item.color) || fallback
}
