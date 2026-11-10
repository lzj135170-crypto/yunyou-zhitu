import { computed, reactive } from 'vue'
import { defaultEndDate, parseDateOnly, todayISO } from '../utils/date'

export const PREFERENCE_OPTIONS = ['自然风景', '拍照', '美食', '古镇', '休闲', '亲子']
export const DIETARY_OPTIONS = ['少辣', '不吃香菜', '不吃葱', '素食']

export function usePlanForm({ showToast }) {
  const planForm = reactive({
    destination: '',
    startDate: todayISO(),
    endDate: defaultEndDate(),
    travelers: 2,
    budget: 3000,
    pace: '轻松',
    hotelLevel: '舒适型',
    preferences: ['自然风景', '美食'],
    dietary: [],
    notes: '',
  })

  const dayCount = computed(() => {
    const start = parseDateOnly(planForm.startDate)
    const end = parseDateOnly(planForm.endDate)
    if (!start || !end) return 0
    const diff = Math.round((end.getTime() - start.getTime()) / 86400000)
    return Math.max(0, diff + 1)
  })

  function togglePreference(field, option) {
    const list = planForm[field]
    const index = list.indexOf(option)
    if (index >= 0) list.splice(index, 1)
    else list.push(option)
  }

  function validate() {
    const destination = planForm.destination.trim()
    if (!destination) {
      showToast('请先填写目的地城市', 'info')
      return false
    }

    const start = parseDateOnly(planForm.startDate)
    const end = parseDateOnly(planForm.endDate)
    if (!start || !end) {
      showToast('请选择开始和结束日期', 'info')
      return false
    }
    if (end < start) {
      showToast('结束日期不能早于开始日期', 'info')
      return false
    }
    if (dayCount.value > 30) {
      showToast('一次最多规划 30 天，更长的行程建议拆成几段', 'info')
      return false
    }
    if (!Number.isFinite(Number(planForm.travelers)) || Number(planForm.travelers) < 1) {
      showToast('出行人数至少 1 人', 'info')
      return false
    }
    if (planForm.budget !== '' && Number(planForm.budget) < 0) {
      showToast('预算不能是负数', 'info')
      return false
    }
    return true
  }

  function composePrompt() {
    const form = planForm
    const lines = [
      '请为我生成一份可直接执行的旅行行程。',
      `目的地：${form.destination.trim()}`,
      `日期：${form.startDate} 至 ${form.endDate}，共 ${dayCount.value} 天`,
      `人数：${form.travelers} 人`,
    ]

    const budget = Number(form.budget)
    if (Number.isFinite(budget) && budget > 0) {
      lines.push(`预算：约 ${budget} 元`)
    }
    lines.push(`节奏：${form.pace}`)
    lines.push(`住宿偏好：${form.hotelLevel}`)
    if (form.preferences.length) lines.push(`旅行偏好：${form.preferences.join('、')}`)
    if (form.dietary.length) lines.push(`饮食偏好：${form.dietary.join('、')}`)
    if (form.notes.trim()) lines.push(`额外要求：${form.notes.trim()}`)

    lines.push(
      '请分别给出景点推荐、住宿建议、交通方案（到达目的地后怎么移动）和天气预报，' +
        '再按天给出上午/下午/晚上的安排，并说明需要注意的事项。'
    )
    return lines.join('\n')
  }

  function snapshot() {
    const form = planForm
    return {
      destination: form.destination.trim(),
      startDate: form.startDate,
      endDate: form.endDate,
      days: dayCount.value,
      travelers: Number(form.travelers),
      budget: Number(form.budget) || 0,
      pace: form.pace,
      hotelLevel: form.hotelLevel,
      preferences: [...form.preferences],
      dietary: [...form.dietary],
      notes: form.notes.trim(),
    }
  }

  return {
    planForm,
    preferenceOptions: PREFERENCE_OPTIONS,
    dietaryOptions: DIETARY_OPTIONS,
    dayCount,
    togglePreference,
    validate,
    composePrompt,
    snapshot,
  }
}
