import { formatDateTime } from './date'
import { formatHours, formatTemp } from './format'

export function buildMarkdown(plan, meta, tripDateRange) {
  const lines = [`# ${plan.destination || '旅行'}行程单`, '']

  if (tripDateRange) lines.push(`- 日期：${tripDateRange}`)
  if (meta.travelers) lines.push(`- 人数：${meta.travelers} 人`)
  if (Number(meta.budget) > 0) {
    lines.push(`- 目标预算：¥${Number(meta.budget).toLocaleString('zh-CN')}`)
  }
  if (plan.generated_at) lines.push(`- 生成时间：${formatDateTime(plan.generated_at)}`)

  lines.push('', '## 行程概览', '', plan.overview || '（无）')

  if (plan.days.length) {
    lines.push('', '## 每日行程')
    plan.days.forEach((day) => {
      lines.push('', `### 第 ${day.day} 天`)
      if (day.morning) lines.push(`- 上午：${day.morning}`)
      if (day.afternoon) lines.push(`- 下午：${day.afternoon}`)
      if (day.evening) lines.push(`- 晚上：${day.evening}`)
    })
  }

  if (plan.attractions.length) {
    lines.push('', '## 景点推荐')
    plan.attractions.forEach((item) => {
      const extra = []
      if (item.hours) extra.push(`建议 ${formatHours(item.hours)}`)
      if (item.price_range) extra.push(item.price_range)
      lines.push(
        `- **${item.name}**：${item.reason || '暂无理由'}${
          extra.length ? `（${extra.join('；')}）` : ''
        }`
      )
    })
  }

  if (plan.foods && plan.foods.length) {
    lines.push('', '## 美食推荐')
    plan.foods.forEach((item) => {
      const extra = [item.category, item.meal_time, item.price_range].filter(Boolean).join('；')
      const where = item.area ? `（${item.area}）` : ''
      lines.push(
        `- **${item.name}**${where}：${item.reason || '暂无理由'}${extra ? `（${extra}）` : ''}`
      )
    })
    if (plan.food_note) lines.push('', plan.food_note)
  }

  if (plan.hotels.length) {
    lines.push('', '## 住宿建议')
    plan.hotels.forEach((item) => {
      const extra = [item.level, item.suitable_for, item.price_range].filter(Boolean).join('；')
      lines.push(`- **${item.area}**：${item.reason || ''}${extra ? `（${extra}）` : ''}`)
    })
  }

  if (plan.transport.length) {
    lines.push('', '## 交通方案')
    plan.transport.forEach((item) => {
      const extra = [item.duration, item.price_range].filter(Boolean).join('；')
      lines.push(
        `- **${item.mode}**：${item.best_for || item.note || ''}${extra ? `（${extra}）` : ''}`
      )
    })
  }

  if (plan.weather && plan.weather.days.length) {
    lines.push('', `## 天气信息（${plan.weather.city || '目的地'}）`)
    plan.weather.days.forEach((day) => {
      const temps = `${formatTemp(day.temp_max)} / ${formatTemp(day.temp_min)}`
      lines.push(`- ${day.date}：${day.weather || '—'}，${temps}`)
    })
    if (plan.weather.advice) lines.push('', plan.weather.advice)
  }

  if (plan.tips.length) {
    lines.push('', '## 旅行提示')
    plan.tips.forEach((tip) => lines.push(`- ${tip}`))
  }

  if (plan.sources.length) {
    lines.push('', '## 参考资料')
    plan.sources.forEach((item) => {
      const section = item.section && item.section.length ? `（${item.section.join(' / ')}）` : ''
      lines.push(`- 《${item.title}》${section}`)
    })
  }

  lines.push('', '---', '', plan.answer || '')
  return lines.join('\n')
}

export function downloadMarkdown(plan, meta, tripDateRange) {
  const blob = new Blob([`﻿${buildMarkdown(plan, meta, tripDateRange)}`], {
    type: 'text/markdown;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  const safeName = (plan.destination || '旅行').replace(/[\\/:*?"<>|]/g, '')
  link.href = url
  link.download = `云游织图-${safeName}-行程单.md`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
