export { routeModeLabel as modeLabel } from './routeModes'

const TRANSPORT_LABELS = {
  high_speed_rail: '高铁',
  high_speed_train: '高铁',
  intercity_train: '城际列车',
  airport_express: '机场快线',
  airport_train: '机场快线',
  plane: '飞机',
  flight: '飞机',
  airplane: '飞机',
  train: '火车',
  rail: '火车',
  self_drive: '自驾',
  self_driving: '自驾',
  drive: '自驾',
  car: '自驾',
  bus: '长途汽车',
  coach: '长途汽车',
  charter_bus: '包车',
  metro: '地铁',
  subway: '地铁',
  taxi: '打车',
  cab: '打车',
  ferry: '轮渡',
  boat: '轮渡',
  walk: '步行',
  walking: '步行',
  bike: '骑行',
  bicycle: '骑行',
}

// 模型偶尔会返回表里没有的复合 key（airport_express、intercity_train 等）。
// 按关键词兜底成中文，实在认不出也不把英文原样露给用户。
const TRANSPORT_HINTS = [
  ['plane', '飞机'],
  ['flight', '飞机'],
  ['air', '飞机'],
  ['high_speed', '高铁'],
  ['rail', '火车'],
  ['train', '火车'],
  ['metro', '地铁'],
  ['subway', '地铁'],
  ['taxi', '打车'],
  ['cab', '打车'],
  ['charter', '包车'],
  ['coach', '长途汽车'],
  ['bus', '长途汽车'],
  ['drive', '自驾'],
  ['car', '自驾'],
  ['ferry', '轮渡'],
  ['boat', '轮渡'],
  ['walk', '步行'],
  ['bike', '骑行'],
  ['bicycle', '骑行'],
]

const CJK_RE = /[㐀-鿿]/

export function transportLabel(mode) {
  const key = String(mode || '').trim()
  if (!key) return '交通'

  const mapped = TRANSPORT_LABELS[key] || TRANSPORT_LABELS[key.toLowerCase()]
  if (mapped) return mapped

  // 模型有时直接给中文；原样保留，不要硬套词表。
  if (CJK_RE.test(key)) return key

  const lower = key.toLowerCase()
  for (const [needle, label] of TRANSPORT_HINTS) {
    if (lower.includes(needle)) return label
  }
  return '其他交通'
}
